"""Bounded, explicit canonical Work retention; no timers or external stores.

Only the named files of one task are considered. This is logical local removal,
not a secure wipe of SQLite free pages, storage snapshots, backups or exports.
"""
import hashlib
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = MAX_ARTIFACT_BYTES
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_CATALOG_ITEMS = 100
HASH_CHUNK_BYTES = 64 * 1024
SQL_TIMEOUT_S = 5
LABEL_CHARS = 160
SIDECARS = ('', '-journal', '-wal', '-shm')
EFFECTS = (
    'Remove this task from Work history and prevent its old request from restarting it.',
    'Remove its saved graph checkpoints, source snapshots and read-only tool receipts.',
    'Keep a minimal removal marker and admission fingerprints to prevent replay.',
)
EXCLUSIONS = (
    'Original notes, preferences, approval and undo records, other tasks and legacy Ultra runs are unchanged.',
    'Optional minimized learning outcome hashes and admission fingerprints remain.',
    'Earlier exports, backups, filesystem snapshots and SQLite free pages are outside this removal; this is not a secure storage wipe.',
)
LIMITS = {'automatic_cleanup': False, 'max_total_bytes': MAX_TOTAL_BYTES,
          'max_state_bytes': MAX_STATE_BYTES, 'catalog_items': MAX_CATALOG_ITEMS,
          'scope': 'canonical_work_task', 'secure_wipe': False}


class RetentionError(Exception):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def state_bytes(run):
    data = encode(run)
    if len(data) > MAX_STATE_BYTES:
        raise RetentionError('This task is too large for bounded removal.')
    return data


def _owned(st, directory=False):
    return (st.st_uid == os.geteuid() and (stat.S_ISDIR(st.st_mode) if directory
            else stat.S_ISREG(st.st_mode) and st.st_nlink == 1))


@contextmanager
def directory(path=None, *, name=None, parent=None, expected=None):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path if path is not None else name, flags, dir_fd=parent)
    try:
        st = os.fstat(fd)
        if not _owned(st, directory=True) or expected and (st.st_dev, st.st_ino) != tuple(expected):
            raise RetentionError('The task storage directory changed or is unsafe.')
        yield fd
    finally:
        os.close(fd)


def identities(root):
    with directory(root) as fd:
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)


def validate_paths(root, root_identity, catalog_identity):
    """The durable marker must remain in the same live catalog as its connection."""
    with directory(root, expected=root_identity) as fd:
        current_root = os.stat(root, follow_symlinks=False)
        current = os.stat('catalog.sqlite', dir_fd=fd, follow_symlinks=False)
        if (not _owned(current_root, directory=True)
                or (current_root.st_dev, current_root.st_ino) != tuple(root_identity)
                or not _owned(current)
                or (current.st_dev, current.st_ino) != tuple(catalog_identity)):
            raise RetentionError('The private workflow catalog changed or is unsafe.')


def artifact_names(identifier):
    rows = []
    for suffix in SIDECARS:
        kind = 'checkpoint' + (suffix.replace('-', '_') if suffix else '')
        rows.append((kind, None, identifier + '.sqlite' + suffix))
    rows.append(('tool_journal', 'tool-journals', identifier + '.json'))
    for suffix in SIDECARS:
        kind = 'tool_receipts' + (suffix.replace('-', '_') if suffix else '')
        rows.append((kind, 'tool-journals', identifier + '.sqlite' + suffix))
    return rows


@contextmanager
def _artifact_directory(root_fd, subdir):
    if subdir is None:
        yield root_fd
    else:
        try:
            fd = os.open(subdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        except FileNotFoundError:
            yield None
            return
        try:
            if not _owned(os.fstat(fd), directory=True):
                raise RetentionError('The task artifact directory is unsafe.')
            yield fd
        finally:
            os.close(fd)


def _file(fd, name, kind):
    if fd is None:
        return {'kind': kind, 'present': False, 'bytes': 0}
    try:
        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return {'kind': kind, 'present': False, 'bytes': 0}
    if not _owned(entry) or entry.st_size > MAX_ARTIFACT_BYTES:
        raise RetentionError('A task artifact is unsafe or exceeds the removal limit.')
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(file_fd)
        if not _owned(before) or (before.st_dev, before.st_ino) != (entry.st_dev, entry.st_ino):
            raise RetentionError('A task artifact changed during review.')
        digest, size = hashlib.sha256(), 0
        while True:
            chunk = os.read(file_fd, HASH_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise RetentionError('A task artifact exceeds the removal limit.')
            digest.update(chunk)
        after = os.fstat(file_fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise RetentionError('A task artifact changed during review.')
        return {'kind': kind, 'present': True, 'bytes': size, 'sha256': digest.hexdigest(),
                'device': before.st_dev, 'inode': before.st_ino,
                'mtime_ns': before.st_mtime_ns, 'ctime_ns': before.st_ctime_ns}
    finally:
        os.close(file_fd)


def manifest(root, identifier, expected_root=None):
    rows, total = [], 0
    with directory(root, expected=expected_root) as root_fd:
        for kind, subdir, name in artifact_names(identifier):
            with _artifact_directory(root_fd, subdir) as fd:
                row = _file(fd, name, kind)
            total += row['bytes']
            if total > MAX_TOTAL_BYTES:
                raise RetentionError('This task exceeds the bounded removal limit.')
            rows.append(row)
    return rows


def revision(run, files):
    return hashlib.sha256(encode({'schema': SCHEMA_VERSION,
        'state_sha256': hashlib.sha256(state_bytes(run)).hexdigest(), 'artifacts': files})).hexdigest()


def public_artifacts(files, run=None):
    rows = [{key: row[key] for key in ('kind', 'present', 'bytes')} for row in files]
    if run is not None:
        rows.insert(0, {'kind': 'catalog_record', 'present': True, 'bytes': len(state_bytes(run))})
    return rows


def remove(root, identifier, files, expected_root=None):
    """Remove reviewed objects only. Missing entries permit crash-safe retries."""
    expected = {row['kind']: row for row in files}
    removed = []
    with directory(root, expected=expected_root) as root_fd:
        for kind, subdir, name in artifact_names(identifier):
            with _artifact_directory(root_fd, subdir) as fd:
                current = _file(fd, name, kind)
                if not current['present']:
                    if expected[kind]['present']:
                        removed.append(kind)
                    continue
                if current != expected[kind]:
                    raise RetentionError('A task artifact changed after review; cleanup is pending.')
                # Recheck the anchored directory entry immediately before unlink.
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not _owned(st) or (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns) != (
                        current['device'], current['inode'], current['mtime_ns'], current['ctime_ns']):
                    raise RetentionError('A task artifact changed after review; cleanup is pending.')
                os.unlink(name, dir_fd=fd)
                os.fsync(fd)
                removed.append(kind)
    return removed


def read_catalog(root, identifier=None):
    """No initialization, migrations, recovery, chmod or writes on a GET."""
    root = Path(root)
    if not root.exists():
        return False, [], []
    with directory(root) as fd:
        try:
            st = os.stat('catalog.sqlite', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return False, [], []
        if not _owned(st) or st.st_size > MAX_CATALOG_BYTES:
            raise RetentionError('The private workflow catalog is unsafe or exceeds its limit.')
        for suffix in SIDECARS[1:]:
            try:
                os.stat('catalog.sqlite' + suffix, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise RetentionError('Wait until the private workflow catalog transaction has settled.')
        # Immutable mode is used only for this detached snapshot reader, after
        # excluding SQLite sidecars. It cannot create a WAL/SHM or recovery file.
        # Reject a changed file below instead of reading through a concurrent writer.
        root_stat = os.fstat(fd)
        root_identity = (root_stat.st_dev, root_stat.st_ino)
        catalog_identity = (st.st_dev, st.st_ino)
        validate_paths(root, root_identity, catalog_identity)
        catalog_fd = os.open('catalog.sqlite', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        pinned = os.fstat(catalog_fd)
        if not _owned(pinned) or (pinned.st_dev, pinned.st_ino) != catalog_identity:
            os.close(catalog_fd)
            raise RetentionError('The private workflow catalog changed during review.')
        # /dev/fd is supported on the target macOS and pins the opened inode,
        # even when a directory pathname is exchanged during SQLite connection.
        try:
            conn = sqlite3.connect('file:/dev/fd/' + str(catalog_fd) + '?mode=ro&immutable=1',
                                   uri=True, timeout=SQL_TIMEOUT_S)
        except BaseException:
            os.close(catalog_fd)
            raise
        try:
            conn.execute('PRAGMA query_only=ON')
            # Older catalogs have no erasure table; the first explicit removal migrates it.
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            marks = []
            if 'erasures' in tables:
                query = 'SELECT data FROM erasures' + (' WHERE id=?' if identifier else '') + " ORDER BY json_extract(data,'$.state')='pending' DESC, created_at DESC LIMIT ?"
                args = (identifier, MAX_CATALOG_ITEMS) if identifier else (MAX_CATALOG_ITEMS,)
                marks = [json.loads(row[0]) for row in conn.execute(query, args)]
            hidden = {row['id'] for row in marks}
            # NOT EXISTS must hide all tombstones even when the visible receipt list is capped.
            clauses = ['NOT EXISTS (SELECT 1 FROM erasures e WHERE e.id=r.id)'] if 'erasures' in tables else []
            if identifier:
                clauses.append('r.id=?')
            exclusion = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
            runs = []
            args = (identifier, MAX_CATALOG_ITEMS) if identifier else (MAX_CATALOG_ITEMS,)
            for row in conn.execute('SELECT data FROM runs r' + exclusion + ' LIMIT ?', args):
                if len(row[0].encode()) > MAX_STATE_BYTES:
                    raise RetentionError('A task record exceeds the bounded review limit.')
                run = json.loads(row[0])
                if run['id'] not in hidden:
                    runs.append(run)
            current = os.stat('catalog.sqlite', dir_fd=fd, follow_symlinks=False)
            if (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size) != (
                    current.st_dev, current.st_ino, current.st_mtime_ns, current.st_ctime_ns, current.st_size):
                raise RetentionError('The private workflow catalog changed during review.')
            validate_paths(root, root_identity, catalog_identity)
            return True, runs, marks
        finally:
            conn.close()
            os.close(catalog_fd)

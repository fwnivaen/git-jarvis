"""Bounded Markdown vault reads shared by Jarvis and Obsidian; no app/plugin API."""
import csv
import hashlib
import io
import os
from pathlib import Path
import re
import stat

MAX_NOTES = 1000
MAX_NOTE_BYTES = 800_000
MAX_VAULT_BYTES = 64 * 1024 * 1024
MAX_FRONTMATTER_CHARS = 8192
MAX_PROPERTY_ITEMS = 64
MAX_PROPERTY_CHARS = 160
EXCERPT_CHARS = 700
EXCLUDED_FOLDERS = frozenset({'private', 'secrets', 'node_modules'})
PROPERTY = re.compile(r'^([A-Za-z_][\w-]*):\s*(.*?)\s*$')
WIKILINK = re.compile(r'(?<!!)\[\[([^\]\n]+)\]\]')
MARKDOWN_LINK = re.compile(r'(?<!!)\[[^\]\n]*\]\(([^)\n]+\.md(?:#[^)\n]*)?)\)')


class VaultError(ValueError):
    pass


def allowed_path(relative):
    path = Path(relative)
    return (not path.is_absolute() and bool(path.parts) and path.suffix.casefold() == '.md'
        and not any(part in ('.', '..') or part.startswith('.')
            or part.casefold() in EXCLUDED_FOLDERS for part in path.parts))


def read_file(root, relative):
    """Anchor every component to an opened directory; never follow a file/link swap."""
    root, relative = Path(root), Path(relative)
    if root.is_symlink() or not allowed_path(relative):
        raise VaultError('Choose a regular Markdown file inside the shared vault.')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_NOTE_BYTES:
                raise VaultError('A shared note exceeds the supported file limit.')
            data = handle.read(MAX_NOTE_BYTES + 1)
            after = os.fstat(handle.fileno())
        if len(data) > MAX_NOTE_BYTES or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise VaultError('A shared note changed while reading. Retry after editing finishes.')
        return data.decode('utf-8'), after
    finally:
        os.close(descriptor)


def properties(text):
    """Read a small safe subset of properties, never execute YAML tags/templates."""
    result = {'aliases': [], 'tags': [], 'private': False}
    lines = text.lstrip('\ufeff')[:MAX_FRONTMATTER_CHARS].splitlines()
    if not lines or lines[0] != '---':
        return result
    try:
        end = lines.index('---', 1)
    except ValueError:
        # Leading properties that cannot be fully checked are not shared.
        # In particular a privacy flag must never be bypassed by a long block.
        result['private'] = True
        return result
    current = None
    for line in lines[1:end]:
        match = PROPERTY.fullmatch(line)
        if match:
            name, value = match.groups()
            current = name.casefold()
            if current in ('jarvis_private', 'jarvis_exclude'):
                # Only an explicit false is public. Quoted booleans/comments
                # are common Obsidian properties; unknown syntax stays private.
                privacy = value.split(' #', 1)[0].strip().strip('\"\'').casefold()
                if privacy not in ('false', 'no', '0'):
                    result['private'] = True
            if current in ('aliases', 'alias', 'tags') and value:
                name = 'aliases' if current in ('alias', 'aliases') else 'tags'
                raw = value[1:-1] if value.startswith('[') and value.endswith(']') else value
                values = next(csv.reader(io.StringIO(raw), skipinitialspace=True), [])
                result[name].extend(values)
        elif current in ('aliases', 'alias', 'tags') and line.lstrip().startswith('- '):
            name = 'aliases' if current in ('alias', 'aliases') else 'tags'
            result[name].append(line.lstrip()[2:])
    for name in ('aliases', 'tags'):
        result[name] = list(dict.fromkeys(value.strip().strip('\"\'')[:MAX_PROPERTY_CHARS]
            for value in result[name] if value.strip()))[:MAX_PROPERTY_ITEMS]
    return result


def read_notes(directory, cache=None):
    """Incremental content reads, complete deletion detection, stable relative identities."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise FileNotFoundError('Your notes folder is missing. Restore the notes folder beside server.py, then restart Jarvis.')
    notes, updated, total, read_count = [], {}, 0, 0
    cache = cache or {}
    candidates = []
    for parent, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not name.startswith('.')
            and name.casefold() not in EXCLUDED_FOLDERS and not (Path(parent) / name).is_symlink())
        for name in files:
            path = Path(parent) / name
            relative = path.relative_to(directory)
            if allowed_path(relative) and not path.is_symlink():
                candidates.append(relative)
                if len(candidates) > MAX_NOTES:
                    raise VaultError('This vault exceeds the note limit. Use a smaller shared folder.')
    for relative in sorted(candidates, key=lambda path: path.as_posix().casefold()):
        path, identity = directory / relative, relative.as_posix()
        info = path.lstat()
        signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        previous = cache.get(identity)
        if previous and previous[0] == signature:
            text, meta = previous[1], previous[2]
        else:
            text, info = read_file(directory, relative)
            signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            meta = properties(text)
            read_count += 1
        total += len(text.encode('utf-8'))
        if total > MAX_VAULT_BYTES:
            raise VaultError('This vault exceeds the shared text limit. Use a smaller shared folder.')
        updated[identity] = (signature, text, meta)
        if meta['private']:
            continue
        label = path.stem
        if path.parent.name in ('captures', 'imports') and text.startswith('# '):
            label = text.splitlines()[0][2:].strip() or label
        notes.append({'id': len(notes), 'label': label, '_path': identity,
            'edited_at': info.st_mtime, 'group': path.parent.name,
            'excerpt': text[:EXCERPT_CHARS], 'text': text, 'aliases': meta['aliases'], 'tags': meta['tags']})
    fingerprint = hashlib.sha256(repr([(key, value[0]) for key, value in updated.items()]).encode()).hexdigest()
    return notes, updated, {'fingerprint': fingerprint, 'content_reads': read_count,
        'note_count': len(notes), 'text_bytes': total}

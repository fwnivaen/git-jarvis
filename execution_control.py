"""Private stdlib control plane. Only trusted controller code may issue capabilities.

This module does not expose a network listener or select a model. The separate
remote/SDK rollout must pass its gates before being wired into request routing.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from task_limits import parse_task_limit

SCHEMA_VERSION = 1
ID_BYTES = 12
TOKEN_BYTES = 32
MAX_CALLS = 24
MAX_WORKERS = 3
MAX_DURATION_S = 900
MAX_DB_BYTES = 64 * 1024 * 1024
SQL_TIMEOUT_S = 5
TERMINAL = frozenset({'complete', 'failed', 'cancelled', 'interrupted'})
TRANSITIONS = {
    'queued': {'running', 'cancelled', 'interrupted'},
    'running': {'waiting', 'cancelling', 'complete', 'failed', 'interrupted'},
    'waiting': {'running', 'cancelling', 'failed', 'interrupted'},
    'cancelling': {'cancelled', 'failed', 'interrupted'},
    'complete': set(), 'failed': set(), 'cancelled': set(), 'interrupted': set(),
}
FLAGS = ('deepagents', 'remote_workspaces', 'outcome_collection', 'learning_promotion')


class ControlError(ValueError):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def identifier():
    return secrets.token_hex(ID_BYTES)


def feature_flags(path):
    """Absent flags are off; unknown/non-boolean keys fail closed."""
    path = Path(path)
    if not path.exists():
        return dict.fromkeys(FLAGS, False)
    if path.is_symlink():
        raise ControlError('Unsafe feature configuration.')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) - set(FLAGS) or any(type(v) is not bool for v in data.values()):
        raise ControlError('Invalid feature configuration.')
    return {key: data.get(key, False) for key in FLAGS}


class ControlStore:
    """Stable IDs, monotonic event cursors, durable budgets and fail-closed leases.

    Transactions use BEGIN IMMEDIATE across processes. No credential is stored in
    plaintext. Restart recovery is explicit and revokes all old run generations.
    Operation results can contain selected tool data: keep this store private.
    """
    def __init__(self, path, clock=time.time, max_active=1):
        self.path, self.clock = Path(path), clock
        # Existing optional executors retain one owner unless their trusted
        # controller explicitly opts into a larger, bounded admission limit.
        self.max_active = parse_task_limit(max_active)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ControlError('Unsafe control store.')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=SQL_TIMEOUT_S, check_same_thread=False, isolation_level=None)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, request_key TEXT UNIQUE, fingerprint TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS identities(token_hash TEXT PRIMARY KEY, run_id TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, kind TEXT, at REAL);
            CREATE TABLE IF NOT EXISTS operations(run_id TEXT, op_id TEXT, fingerprint TEXT, data TEXT,
                PRIMARY KEY(run_id,op_id));
        ''')
        row = self.db.execute('SELECT version FROM metadata').fetchone()
        if row is None:
            self.db.execute('INSERT INTO metadata VALUES (?)', (SCHEMA_VERSION,))
        elif row[0] != SCHEMA_VERSION:
            raise ControlError('Unsupported control schema; restore compatible code.')

    def transaction(self, function):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                result = function()
                self.db.execute('COMMIT')
                return result
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def _get(self, run_id):
        row = self.db.execute('SELECT data FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise ControlError('Unknown run.')
        return json.loads(row[0])

    def get(self, run_id):
        with self.lock:
            return self._get(run_id)

    def _put(self, run):
        self.db.execute('UPDATE runs SET data=? WHERE id=?', (encode(run), run['id']))

    def _event(self, run_id, kind):
        self.db.execute('INSERT INTO events(run_id,kind,at) VALUES (?,?,?)', (run_id, kind, self.clock()))

    def admit(self, request_key, *, conversation_id, project_id, engine, versions, permissions,
              duration_s, max_calls=MAX_CALLS, parent_run_id=None):
        if (not all(isinstance(x, str) and x for x in (request_key, conversation_id, project_id, engine))
                or not isinstance(versions, dict) or not {'engine', 'model', 'tools', 'behavior'} <= set(versions)
                or not isinstance(permissions, (list, tuple, set, frozenset))
                or not all(isinstance(x, str) for x in permissions)
                or type(max_calls) is not int or not 0 <= max_calls <= MAX_CALLS
                or type(duration_s) not in (int, float) or not 0 < duration_s <= MAX_DURATION_S
                or parent_run_id is not None):
            raise ControlError('Invalid admission or recursive run.')
        spec = dict(conversation_id=conversation_id, project_id=project_id, engine=engine,
                    versions=versions, permissions=sorted(permissions), duration_s=duration_s, max_calls=max_calls)
        fingerprint = digest(spec)
        def create():
            row = self.db.execute('SELECT id,fingerprint FROM runs WHERE request_key=?', (request_key,)).fetchone()
            if row:
                if row[1] != fingerprint:
                    raise ControlError('Admission key reused with different parameters.')
                return self._get(row[0]), False
            if self.path.stat().st_size >= MAX_DB_BYTES:
                raise ControlError('Control storage budget exhausted.')
            # Queued tasks coexist; the trusted controller bounds active owners.
            now = self.clock()
            run = dict(id=identifier(), parent_run_id=None, created_at=now, expires_at=now+duration_s,
                       status='queued', generation=1, calls=0, cleanup='none', **spec)
            self.db.execute('INSERT INTO runs VALUES (?,?,?,?)', (run['id'], request_key, fingerprint, encode(run)))
            self._event(run['id'], 'queued')
            return run, True
        return self.transaction(create)

    def transition(self, run_id, status):
        def update():
            run = self._get(run_id)
            if status == run['status']:
                return run
            if status not in TRANSITIONS[run['status']]:
                raise ControlError('Invalid lifecycle transition.')
            if status == 'running':
                if self.clock() >= run['expires_at']:
                    raise ControlError('Run expired.')
                others = [json.loads(r[0]) for r in self.db.execute('SELECT data FROM runs WHERE id!=?', (run_id,))]
                if sum(r['status'] in {'running', 'waiting', 'cancelling'} for r in others) >= self.max_active:
                    raise ControlError('Execution capacity occupied.')
            if status == 'complete':
                pending = [json.loads(r[0]) for r in self.db.execute('SELECT data FROM operations WHERE run_id=?', (run_id,))]
                if any(p['status'] != 'complete' for p in pending) or run['cleanup'] == 'pending':
                    raise ControlError('Unsettled operation or cleanup.')
            run['status'] = status
            if status in TERMINAL:
                run['ended_at'] = self.clock()
            if status == 'cancelling' or status in TERMINAL:
                run['generation'] += 1
            self._put(run)
            self._event(run_id, status)
            return run
        return self.transaction(update)

    def issue(self, run_id, *, permissions=None, max_calls=None, expires_at=None, parent_token=None, worker=None):
        def grant():
            run = self._get(run_id)
            if run['status'] != 'running' or self.clock() >= run['expires_at']:
                raise ControlError('Inactive run.')
            parent = self._auth(parent_token, run_id) if parent_token else None
            if parent and parent['depth']:
                raise ControlError('Recursive delegation disabled.')
            allowed = set(parent['permissions'] if parent else run['permissions'])
            selected = allowed if permissions is None else set(permissions)
            budget = (parent['max_calls'] if parent else run['max_calls']) if max_calls is None else max_calls
            expiry = (parent['expires_at'] if parent else run['expires_at']) if expires_at is None else expires_at
            if (not selected <= allowed or type(budget) is not int or not 0 <= budget <= (parent['max_calls'] if parent else run['max_calls'])
                    or expiry > (parent['expires_at'] if parent else run['expires_at']) or expiry <= self.clock()):
                raise ControlError('Child scope exceeds owner.')
            if parent:
                count = sum(json.loads(r[0])['depth'] == 1 for r in self.db.execute('SELECT data FROM identities WHERE run_id=?', (run_id,)))
                if count >= MAX_WORKERS:
                    raise ControlError('Worker capacity exhausted.')
            token = secrets.token_urlsafe(TOKEN_BYTES)
            identity = dict(id=identifier(), worker=worker, parent_id=parent['id'] if parent else None,
                            depth=1 if parent else 0, generation=run['generation'], permissions=sorted(selected),
                            expires_at=expiry, max_calls=budget, calls=0)
            self.db.execute('INSERT INTO identities VALUES (?,?,?)', (digest(token), run_id, encode(identity)))
            return token
        return self.transaction(grant)

    def _auth(self, token, run_id):
        if not isinstance(token, str):
            raise ControlError('Execution authentication required.')
        row = self.db.execute('SELECT run_id,data FROM identities WHERE token_hash=?', (digest(token),)).fetchone()
        run = self._get(run_id)
        if row is None or row[0] != run_id:
            raise ControlError('Invalid execution identity.')
        identity = json.loads(row[1])
        if (run['status'] != 'running' or identity['generation'] != run['generation']
                or self.clock() >= min(identity['expires_at'], run['expires_at'])):
            raise ControlError('Expired or fenced execution.')
        return identity

    def authenticate(self, token, run_id):
        with self.lock:
            return self._auth(token, run_id)

    def reserve(self, token, run_id, operation_id, name, arguments, classification):
        if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128:
            raise ControlError('Stable operation identity required.')
        fingerprint = digest([name, arguments])
        def reserve():
            actor = self._auth(token, run_id)
            run = self._get(run_id)
            if name not in actor['permissions']:
                raise ControlError('Tool outside scope.')
            old = self.db.execute('SELECT fingerprint,data FROM operations WHERE run_id=? AND op_id=?', (run_id, operation_id)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ControlError('Operation identity reused.')
                saved = json.loads(old[1])
                if saved['status'] == 'complete':
                    return saved, True
                raise ControlError('Uncertain operation; verify effect before retry.')
            if classification == 'consequential':
                raise ControlError('Consequential tools require a separate trusted approval receipt.')
            ancestors = []
            parent_id = actor.get('parent_id')
            while parent_id:
                rows = self.db.execute('SELECT token_hash,data FROM identities WHERE run_id=?', (run_id,)).fetchall()
                found = next(((h, json.loads(d)) for h, d in rows if json.loads(d)['id'] == parent_id), None)
                if found is None or found[1]['calls'] >= found[1]['max_calls']:
                    raise ControlError('Parent tool budget exhausted.')
                ancestors.append(found)
                parent_id = found[1].get('parent_id')
            if run['calls'] >= run['max_calls'] or actor['calls'] >= actor['max_calls']:
                raise ControlError('Shared tool budget exhausted.')
            run['calls'] += 1
            actor['calls'] += 1
            for parent_hash, parent in ancestors:
                parent['calls'] += 1
                self.db.execute('UPDATE identities SET data=? WHERE token_hash=?', (encode(parent), parent_hash))
            self._put(run)
            self.db.execute('UPDATE identities SET data=? WHERE token_hash=?', (encode(actor), digest(token)))
            operation = dict(id=operation_id, actor=actor['id'], generation=run['generation'],
                             classification=classification, tool=name, status='pending', started_at=self.clock())
            self.db.execute('INSERT INTO operations VALUES (?,?,?,?)', (run_id, operation_id, fingerprint, encode(operation)))
            self._event(run_id, 'tool_started')
            return operation, False
        return self.transaction(reserve)

    def settle(self, token, run_id, operation_id, result):
        def settle():
            actor = self._auth(token, run_id)
            row = self.db.execute('SELECT data FROM operations WHERE run_id=? AND op_id=?', (run_id, operation_id)).fetchone()
            if row is None:
                raise ControlError('No operation reservation.')
            operation = json.loads(row[0])
            if actor['id'] != operation['actor'] or operation['status'] != 'pending':
                raise ControlError('Invalid operation settlement.')
            operation.update(status='complete', result=result, ended_at=self.clock())
            self.db.execute('UPDATE operations SET data=? WHERE run_id=? AND op_id=?', (encode(operation), run_id, operation_id))
            self._event(run_id, 'tool_complete')
            return {'operation_id': operation_id, 'result_hash': digest(result), 'generation': operation['generation']}
        return self.transaction(settle)

    def events(self, run_id, after=0):
        if type(after) is not int or after < 0:
            raise ControlError('Invalid event cursor.')
        with self.lock:
            self._get(run_id)
            return [dict(id=row[0], kind=row[1], at=row[2], run_id=run_id) for row in
                    self.db.execute('SELECT seq,kind,at FROM events WHERE run_id=? AND seq>? ORDER BY seq', (run_id, after))]

    def recover(self):
        def recover():
            recovered = []
            for row in self.db.execute('SELECT data FROM runs').fetchall():
                run = json.loads(row[0])
                if run['status'] in {'running', 'waiting', 'cancelling'}:
                    run.update(status='interrupted', generation=run['generation']+1, cleanup='pending')
                    self._put(run)
                    self._event(run['id'], 'interrupted')
                    recovered.append(run['id'])
            return recovered
        return self.transaction(recover)

    def backup(self, destination):
        destination = Path(destination)
        if destination.exists() or destination.is_symlink():
            raise ControlError('Backup destination must be new.')
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        with self.lock, sqlite3.connect(destination) as target:
            self.db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ControlError('Backup verification failed.')

    def close(self):
        with self.lock:
            self.db.close()

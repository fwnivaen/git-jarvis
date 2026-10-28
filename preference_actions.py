"""Explicit preference review using the existing durable approval state machine.

The caller enforces the local viewer origin and keeps this controller out of the
model tool registry. A proposal reads only the explicit preference map. The
private queue must be namespaced with its vault, separately from note actions.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

from execution_control import digest
import note_actions
from note_actions import NoteActions, NoteActionError
from ultra_memory import Preferences, atomic_json, MAX_PREFERENCES, MAX_PREFERENCE_BYTES

STATE_ID = 0
READ_EXTRA_BYTES = 1
PRIVATE_MODE_MASK = 0o077
CREDENTIAL_PATTERNS = (
    re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----'),
    re.compile(r'\b(?:sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9_]{8,}|github_pat_[a-z0-9_]{8,})', re.I),
    re.compile(r'\bbearer\s+[a-z0-9._~+/-]+=*', re.I),
    re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'),
    re.compile(r'\bxox[baprs]-[0-9A-Za-z-]{10,}\b'),
    re.compile(r'''["']?\b(?:api[_ -]?key|authorization|password|secret|access[_ -]?token|refresh[_ -]?token)["']?\s*[:=]\s*["']?[^\s,;"']+''', re.I),
)
LIMITATIONS = (
    'Only explicitly supplied preference changes can be reviewed here. No inferred memories or external actions are available.',
    'Approval binds the entire displayed preference state and expires after five minutes. Any other preference change creates a conflict.',
    'Undo is available for one hour and only while the entire preference state still matches the approved result.',
    'An interrupted or unconfirmed change is never replayed automatically. Inspect preference memory before another change.',
    'Known credential forms are rejected. Preference memory is not a password store.',
)


class PreferenceActionError(ValueError):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


def _encode(values):
    return json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _validate(values):
    if not isinstance(values, dict) or len(values) > MAX_PREFERENCES:
        raise PreferenceActionError('Use a bounded map of explicit preferences.', 400)
    try:
        for key, value in values.items():
            Preferences.validate(key, value)
            if any(pattern.search(key) or pattern.search(value) for pattern in CREDENTIAL_PATTERNS):
                raise ValueError
        encoded = _encode(values)
        if len(encoded.encode('utf-8')) > MAX_PREFERENCE_BYTES:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise PreferenceActionError('Use short preference names and values without credentials.', 400) from None
    return dict(values)


class PreferenceState:
    """Compare-and-replace contract for one server-owned Preferences instance.

    Both snapshot and compare_and_replace hold Preferences.lock. All server
    preference writers must use that same instance/lock. External editors do not
    participate in this lock; the private JSON file must stay service-owned.
    Invalid or unsafe storage is refused rather than treated as an empty map.
    """
    def __init__(self, preferences):
        if not isinstance(preferences, Preferences):
            raise TypeError('Use the existing explicit preference store.')
        self.preferences = preferences
        self.identity = 'preferences:' + hashlib.sha256(str(preferences.path.absolute()).encode()).hexdigest()
        self.stable_id = digest(self.identity)

    def revision(self, values):
        return digest([self.identity, values])

    def _read(self):
        path = self.preferences.path
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError
        parent = None
        try:
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, 'rb') as handle:
                before = os.fstat(handle.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                        or stat.S_IMODE(before.st_mode) & PRIVATE_MODE_MASK
                        or before.st_size > MAX_PREFERENCE_BYTES):
                    raise ValueError
                data = handle.read(MAX_PREFERENCE_BYTES + READ_EXTRA_BYTES)
                after = os.fstat(handle.fileno())
            if (len(data) > MAX_PREFERENCE_BYTES or
                    (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError
            return _validate(json.loads(data))
        except FileNotFoundError:
            return {}
        finally:
            if parent is not None:
                os.close(parent)

    def snapshot(self):
        with self.preferences.lock:
            try:
                values = self._read()
                return {'preferences': values, 'revision': self.revision(values)}
            except Exception:
                raise PreferenceActionError('Private preference memory is unavailable or needs attention.', 503) from None

    def compare_and_replace(self, values, expected_revision):
        """Return verified state, or refuse without replacing a different revision."""
        values = _validate(values)
        if not isinstance(expected_revision, str) or not note_actions.REVISION.fullmatch(expected_revision):
            raise PreferenceActionError('Supply the exact preference-state revision.', 400)
        with self.preferences.lock:
            current = self.snapshot()
            if current['revision'] != expected_revision:
                raise PreferenceActionError('Preferences changed after review. Refresh them before another change.')
            try:
                atomic_json(self.preferences.path, values)
                verified = self.snapshot()
                if verified['preferences'] != values:
                    raise ValueError
            except Exception:
                raise PreferenceActionError('The preference change could not be verified. Inspect memory before retrying.', 503) from None
            return dict(verified, verified=True)


class PreferenceActions:
    def __init__(self, path, preferences, clock=time.time):
        self.state = PreferenceState(preferences)
        self._lifecycle = threading.RLock()
        self._closed = False
        self._engine = NoteActions(path, self._read_state, self._write_state, clock=clock)

    def _read_state(self, state_id):
        if state_id != STATE_ID:
            raise ValueError
        value = self.state.snapshot()
        return dict(id=STATE_ID, title='Preference memory', text=_encode(value['preferences']),
            stable_id=self.state.stable_id, revision=value['revision'])

    def _write_state(self, arguments, operation_session):
        # The durable engine already claimed this exact operation. Session IDs
        # are private replay identities, never delegated model authority.
        value = self.state.compare_and_replace(json.loads(arguments['text']), arguments['revision'])
        return {'verified': True, 'note': self._read_state(STATE_ID),
            'preferences': value['preferences'], 'revision': value['revision']}

    def _call(self, callback):
        with self._lifecycle:
            if self._closed:
                raise PreferenceActionError('Preference review is shutting down.', 503)
            try:
                return callback()
            except NoteActionError as error:
                message = str(error).replace('note action', 'preference action').replace('note edit', 'preference change')
                message = message.replace('note', 'preference memory').replace('edit', 'change')
                raise PreferenceActionError(message, error.status) from None

    def _public(self, action, details=False):
        before, after = json.loads(action['before']['text']), json.loads(action['after']['text'])
        keys = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
        if len(keys) != 1:
            raise PreferenceActionError('The stored preference review needs attention.', 503)
        key = keys[0]
        operation = 'set' if key in after else 'forget'
        result = {name: action[name] for name in ('id', 'status', 'fingerprint', 'created_at',
            'updated_at', 'expires_at', 'can_undo')}
        result.update(kind='preferences', tool='preferences_' + operation, operation=operation,
            key=key, title='Change preference' if operation == 'set' else 'Forget preference')
        for name in ('decided_at', 'applied_at', 'undo_expires_at', 'undone_at'):
            if name in action:
                result[name] = action[name]
        if details:
            result['before'] = {'preferences': before, 'revision': action['before']['revision']}
            result['after'] = {'preferences': after, 'revision': self.state.revision(after)}
        return result

    def snapshot(self):
        return self._call(self.state.snapshot)

    def propose(self, operation, key, value=None, revision=None, request_key=None, scope='local'):
        def propose():
            if (operation not in ('set', 'forget') or not isinstance(key, str)
                    or not isinstance(revision, str) or not note_actions.REVISION.fullmatch(revision)):
                raise PreferenceActionError('Choose set or forget with an exact preference-state revision.', 400)
            name = key.strip()
            if operation == 'set':
                _validate({name: value})
                replacement = value.strip()
            else:
                if value is not None:
                    raise PreferenceActionError('Forget needs a preference name only.', 400)
                # The existing validator also rejects credential-named keys.
                _validate({name: 'Forget this explicit preference'})
                replacement = None
            self._engine._scope(scope)
            if request_key is not None and (not isinstance(request_key, str) or not note_actions.HEX_ID.fullmatch(request_key)):
                raise PreferenceActionError('Use a valid preference request key.', 400)
            # A replay must reconstruct the original reviewed map, rather than
            # accidentally basing a second operation on the now-modified state.
            def prior():
                row = self._engine.control.db.execute(
                    'SELECT data FROM note_actions WHERE scope=? AND request_key=?', (scope, request_key)).fetchone()
                return json.loads(row[0]) if row is not None else None
            saved = self._engine.control.transaction(prior) if request_key is not None else None
            before = json.loads(saved['before_text']) if saved else self.state.snapshot()['preferences']
            after = dict(before)
            if operation == 'forget':
                if name not in after:
                    raise PreferenceActionError('That preference is already absent.', 400)
                del after[name]
            else:
                after[name] = replacement
            _validate(after)
            return self._public(self._engine.propose(STATE_ID, _encode(after), revision, request_key, scope), True)
        return self._call(propose)

    def get(self, action_id, scope='local'):
        return self._call(lambda: self._public(self._engine.get(action_id, scope), True))

    def catalog(self, scope='local'):
        def catalog():
            source = self._engine.catalog(scope)
            return {'items': [self._public(self._engine.get(item['id'], scope)) for item in source['items']],
                'pending_count': source['pending_count'],
                'capabilities': {'preferences_set': True, 'preferences_forget': True,
                    'exact_state_undo': True, 'inferred_memory': False, 'external_actions': False},
                'limitations': list(LIMITATIONS)}
        return self._call(catalog)

    def _result(self, response):
        result = response['result']
        return {'approval': self._public(response['approval'], True), 'result': None if result is None else
            {'verified': result['verified'], 'preferences': result['preferences'], 'revision': result['revision']}}

    def decide(self, action_id, fingerprint, decision, scope='local'):
        return self._call(lambda: self._result(self._engine.decide(action_id, fingerprint, decision, scope)))

    def undo(self, action_id, fingerprint, scope='local'):
        return self._call(lambda: self._result(self._engine.undo(action_id, fingerprint, scope)))

    def close(self):
        # A shutdown cannot close SQLite between a claimed effect and receipt.
        with self._lifecycle:
            if not self._closed:
                self._engine.close()
                self._closed = True

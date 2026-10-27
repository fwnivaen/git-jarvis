"""Durable local review and exact-revision undo for existing note edits only.

HTTP callers must enforce the local viewer origin. Neither the model registry nor
the optional executors receive this service. The trusted write callback uses the
existing native tool journal; a lost outcome is never automatically replayed.
"""
import json
import re
import secrets
import time

import note_store
from execution_control import ControlStore, digest, encode, identifier

APPROVAL_TTL_S = 300
UNDO_TTL_S = 3600
MAX_ACTIONS = 64
MAX_PENDING = 8
MAX_QUEUE_BYTES = 64 * 1024 * 1024
MAX_TITLE_CHARS = 160
MAX_SCOPE_CHARS = 128
HEX_ID = re.compile(r'[a-f0-9]{24}\Z')
REVISION = re.compile(r'[a-f0-9]{64}\Z')
SCOPE = re.compile(r'[A-Za-z0-9_.:-]{1,' + str(MAX_SCOPE_CHARS) + r'}\Z')
RECOVERED = {'applying': 'uncertain', 'undoing': 'undo_uncertain'}
LIMITATIONS = (
    'Only edits to existing notes can be reviewed here. No external actions are available.',
    'Approval authorizes the displayed text for one exact note revision and expires after five minutes.',
    'Undo is available for one hour and only while the note still has the exact saved revision.',
    'An interrupted or unconfirmed write is never replayed automatically. Inspect the note before another edit.',
)


class NoteActionError(ValueError):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


class NoteActions:
    def __init__(self, path, read_note, write_note, clock=time.time):
        self.control = ControlStore(path, clock=clock)
        self.clock, self.read_note, self.write_note = clock, read_note, write_note
        with self.control.lock:
            self.control.db.execute('''CREATE TABLE IF NOT EXISTS note_actions(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_key TEXT,
                request_fingerprint TEXT NOT NULL, data TEXT NOT NULL,
                UNIQUE(scope,request_key))''')
        # A prior process may have stopped after the effect but before its
        # receipt. Preserve the record and deny any automatic replay.
        self.recover()

    @staticmethod
    def _scope(scope):
        if not isinstance(scope, str) or not SCOPE.fullmatch(scope):
            raise NoteActionError('Invalid approval scope.', 400)
        return scope

    @staticmethod
    def _id(action_id):
        if not isinstance(action_id, str) or not HEX_ID.fullmatch(action_id):
            raise NoteActionError('Unknown note action.', 404)

    @staticmethod
    def _text(text):
        if (not isinstance(text, str) or len(text) > note_store.MAX_NOTE_CHARS):
            raise NoteActionError('The note exceeds the editor limit.', 400)
        try:
            size = len(text.encode('utf-8'))
        except UnicodeEncodeError:
            raise NoteActionError('The note contains invalid text.', 400) from None
        if size > note_store.MAX_NOTE_BYTES:
            raise NoteActionError('The note exceeds the editor limit.', 400)
        return text

    def _current(self, note_id):
        try:
            note = self.read_note(note_id)
            if (not isinstance(note, dict) or note.get('id') != note_id
                    or not isinstance(note.get('stable_id'), str)
                    or not REVISION.fullmatch(note['stable_id'])
                    or not isinstance(note.get('revision'), str)
                    or not REVISION.fullmatch(note['revision'])):
                raise ValueError
            self._text(note.get('text'))
            return note
        except Exception:
            raise NoteActionError('The note is unavailable or changed. Reopen it before proposing an edit.') from None

    def _get(self, action_id, scope):
        row = self.control.db.execute('SELECT data FROM note_actions WHERE id=? AND scope=?',
            (action_id, scope)).fetchone()
        if row is None:
            raise NoteActionError('Unknown note action.', 404)
        return json.loads(row[0])

    def _save(self, action):
        self.control.db.execute('UPDATE note_actions SET data=? WHERE id=? AND scope=?',
            (encode(action), action['id'], action['scope']))

    def _expire(self, action):
        if action['status'] == 'pending' and self.clock() >= action['expires_at']:
            action.update(status='expired', updated_at=self.clock())
            self._save(action)
        return action

    def _public(self, action, details=False):
        result = {key: action[key] for key in ('id', 'status', 'fingerprint', 'created_at',
            'updated_at', 'expires_at', 'note_id', 'title')}
        result.update(tool='note_write', can_undo=action['status'] == 'applied'
            and self.clock() < action.get('undo_expires_at', 0))
        for key in ('decided_at', 'applied_at', 'undo_expires_at', 'undone_at'):
            if key in action:
                result[key] = action[key]
        if details:
            result.update(note={'id': action['note_id'], 'title': action['title'],
                'stable_id': action['stable_id']}, before={'text': action['before_text'],
                'revision': action['before_revision']}, after={'text': action['after_text']})
            if action.get('applied_revision'):
                result['after']['revision'] = action['applied_revision']
        return result

    def _existing(self, scope, request_key, request_fingerprint):
        if request_key is None:
            return None
        row = self.control.db.execute('SELECT id,request_fingerprint FROM note_actions WHERE scope=? AND request_key=?',
            (scope, request_key)).fetchone()
        if row is None:
            return None
        if row[1] != request_fingerprint:
            raise NoteActionError('That request key belongs to a different edit.')
        return self._expire(self._get(row[0], scope))

    def propose(self, note_id, text, revision, request_key=None, scope='local'):
        scope = self._scope(scope)
        self._text(text)
        if (type(note_id) is not int or note_id < 0 or not isinstance(revision, str)
                or not REVISION.fullmatch(revision) or request_key is not None
                and (not isinstance(request_key, str) or not HEX_ID.fullmatch(request_key))):
            raise NoteActionError('Supply a note, its current revision, and the proposed text.', 400)
        request_fingerprint = digest([scope, note_id, text, revision])
        def existing():
            action = self._existing(scope, request_key, request_fingerprint)
            return self._public(action, True) if action else None
        saved = self.control.transaction(existing)
        if saved is not None:
            return saved
        current = self._current(note_id)
        if current['revision'] != revision:
            raise NoteActionError('The note changed. Reopen it before proposing an edit.')
        if current['text'] == text:
            raise NoteActionError('The proposed text already matches this note.', 400)
        def create():
            old = existing()
            if old is not None:
                return old
            actions = [self._expire(json.loads(row[0])) for row in
                self.control.db.execute('SELECT data FROM note_actions').fetchall()]
            if len(actions) >= MAX_ACTIONS:
                raise NoteActionError('The private note action store is full. Archive it while Jarvis is stopped.', 503)
            if sum(action['status'] == 'pending' for action in actions) >= MAX_PENDING:
                raise NoteActionError('Review or reject an existing note edit before adding another.', 429)
            now = self.clock()
            action = dict(id=identifier(), scope=scope, note_id=note_id,
                stable_id=current['stable_id'], title=str(current.get('title') or 'Note')[:MAX_TITLE_CHARS],
                before_text=current['text'], before_revision=revision, after_text=text,
                status='pending', created_at=now, updated_at=now, expires_at=now+APPROVAL_TTL_S,
                operation_session=identifier(), undo_session=identifier())
            action['fingerprint'] = digest(['note_write', action['id'], scope, note_id,
                current['stable_id'], revision, text])
            encoded = encode(action)
            if self.control.path.stat().st_size + len(encoded.encode()) > MAX_QUEUE_BYTES:
                raise NoteActionError('The private note action storage budget is exhausted.', 503)
            self.control.db.execute('INSERT INTO note_actions VALUES (?,?,?,?,?)',
                (action['id'], scope, request_key, request_fingerprint, encoded))
            return self._public(action, True)
        return self.control.transaction(create)

    def get(self, action_id, scope='local'):
        self._id(action_id)
        scope = self._scope(scope)
        return self.control.transaction(lambda: self._public(self._expire(self._get(action_id, scope)), True))

    def catalog(self, scope='local'):
        scope = self._scope(scope)
        def read():
            ids = [row[0] for row in self.control.db.execute('SELECT id FROM note_actions WHERE scope=?', (scope,))]
            actions = [self._expire(self._get(action_id, scope)) for action_id in ids]
            actions.sort(key=lambda action: action['created_at'], reverse=True)
            return {'items': [self._public(action) for action in actions],
                'pending_count': sum(action['status'] == 'pending' for action in actions),
                'capabilities': {'note_write': True, 'exact_revision_undo': True,
                    'external_actions': False}, 'limitations': list(LIMITATIONS)}
        return self.control.transaction(read)

    def _claim(self, action_id, fingerprint, scope, undo=False, decision='approve'):
        self._id(action_id)
        self._scope(scope)
        if not isinstance(fingerprint, str) or not REVISION.fullmatch(fingerprint):
            raise NoteActionError('Supply the exact note action fingerprint.', 400)
        def claim():
            action = self._expire(self._get(action_id, scope))
            if not secrets.compare_digest(action['fingerprint'], fingerprint):
                raise NoteActionError('This decision belongs to a different edit.')
            if undo:
                if action['status'] != 'applied' or self.clock() >= action['undo_expires_at']:
                    raise NoteActionError('This edit can no longer be undone here. Reopen the note.')
                status = 'undoing'
            else:
                if action['status'] != 'pending':
                    raise NoteActionError('This note action has already been decided, expired, or interrupted.')
                status = 'rejected' if decision == 'reject' else 'applying'
                action.update(decision=decision, decided_at=self.clock())
            action.update(status=status, updated_at=self.clock())
            self._save(action)
            return action
        return self.control.transaction(claim)

    def _finish(self, action, expected, status, **values):
        def finish():
            saved = self._get(action['id'], action['scope'])
            if saved['status'] != expected:
                raise NoteActionError('The write outcome could not be confirmed. Inspect the note before another edit.')
            saved.update(status=status, updated_at=self.clock(), **values)
            self._save(saved)
            return self._public(saved, True)
        return self.control.transaction(finish)

    def _apply(self, action, undo=False):
        expected = 'undoing' if undo else 'applying'
        revision = action['applied_revision'] if undo else action['before_revision']
        before = action['after_text'] if undo else action['before_text']
        text = action['before_text'] if undo else action['after_text']
        try:
            current = self._current(action['note_id'])
            if (current['stable_id'] != action['stable_id'] or current['revision'] != revision
                    or current['text'] != before):
                raise NoteActionError('The note changed after review. Reopen it before another edit.')
        except Exception:
            self._finish(action, expected, 'undo_stale' if undo else 'stale')
            raise NoteActionError('The note changed or is unavailable. Reopen it before another edit.') from None
        try:
            result = self.write_note({'id': action['note_id'], 'text': text, 'revision': revision},
                action['undo_session'] if undo else action['operation_session'])
            current = self._current(action['note_id'])
            saved_note = result.get('note') if isinstance(result, dict) else None
            if (not isinstance(saved_note, dict) or result.get('verified') is not True
                    or saved_note.get('stable_id') != action['stable_id']
                    or saved_note.get('revision') != current['revision']
                    or current['stable_id'] != action['stable_id'] or current['text'] != text):
                raise ValueError
            now = self.clock()
            detail = self._finish(action, expected, 'undone' if undo else 'applied',
                **({'undone_at': now, 'undo_revision': current['revision']} if undo else
                   {'applied_at': now, 'applied_revision': current['revision'], 'undo_expires_at': now+UNDO_TTL_S}))
            return {'approval': detail, 'result': result}
        except Exception:
            try:
                self._finish(action, expected, 'undo_uncertain' if undo else 'uncertain')
            except Exception:
                pass
            raise NoteActionError('The write outcome could not be confirmed. Inspect the note before another edit.', 503) from None

    def decide(self, action_id, fingerprint, decision, scope='local'):
        if decision not in ('approve', 'reject'):
            raise NoteActionError('Choose approve or reject.', 400)
        action = self._claim(action_id, fingerprint, scope, decision=decision)
        if decision == 'reject':
            return {'approval': self._public(action, True), 'result': None}
        return self._apply(action)

    def undo(self, action_id, fingerprint, scope='local'):
        return self._apply(self._claim(action_id, fingerprint, scope, undo=True), undo=True)

    def recover(self):
        def recover():
            count = 0
            for row in self.control.db.execute('SELECT data FROM note_actions').fetchall():
                action = json.loads(row[0])
                if action['status'] in RECOVERED:
                    action.update(status=RECOVERED[action['status']], updated_at=self.clock())
                    self._save(action)
                    count += 1
            return count
        return self.control.transaction(recover)

    def close(self):
        self.control.close()

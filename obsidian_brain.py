"""Local Obsidian-compatible memory: incremental FTS5 and bounded source passages.

No community plugin, hosted embedding, app automation, model call or credential is
needed. Vault selection is persisted privately and applied at server startup.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import urllib.parse

import note_store
from ultra_memory import atomic_json
import vault_files

SCHEMA_VERSION = 1
DEFAULT_CONTEXT_CHARS = 6000
HISTORY_CHARS = 3000
MAX_DIRECT_ANSWER_CHARS = 8000
MAX_SOURCE_TITLE_CHARS = 160
MIN_CONTEXT_CHARS = 1000
MAX_CONTEXT_CHARS = 12000
MAX_PASSAGE_CHARS = 1800
MIN_PASSAGE_CHARS = 120
MAX_RESULTS = 6
MAX_QUERY_CHARS = 2000
MAX_QUERY_WORDS = 32
MAX_SETTINGS_BYTES = 8192
REFRESH_INTERVAL_S = 2.0
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
SQL_TIMEOUT_S = 3
STOP_WORDS = frozenset('a an and are as at be by for from how i in is it me my of on or that the this to was what with you your please sir jarvis notes brain'.split())
LOOKUP = re.compile(r'^(?:find in my notes|search my (?:notes|brain)(?: for)?|look up in my (?:notes|brain))\s*[:,-]?\s+(.+)$', re.I)
READ_NOTE = re.compile(r'^(?:read|show|open) (?:my )?note\s+[\"\']?(.+?)[\"\']?[.!?]?$', re.I)
STARTER_FILES = {
    'Start here.md': '# Jarvis Brain\n\nThis folder is your shared Markdown memory for Obsidian and Jarvis.\n\n'
        '- [[Knowledge/About me]]\n- [[Projects/Jarvis]]\n- [[Decisions/Memory rules]]\n'
        '\nUse Inbox for quick captures, Knowledge for durable facts, Projects for active work, '
        'Decisions for agreed choices, and Daily for your own daily notes.\n\n'
        'Jarvis uses selected relevant passages, not this entire folder, for model requests. '
        'Explicit local note searches can run without a model. This folder is not a transcript recorder.\n',
    'Knowledge/About me.md': '---\naliases: [Nivaen, Sir]\ntags: [profile]\n---\n# About me\n\n'
        '- Name: Nivaen Giritharan.\n- Preferred address from Jarvis: Sir.\n'
        '- Location: New York, NY.\n- Timezone: America/New_York, including daylight saving.\n'
        '- GitHub: fwnivaen.\n',
    'Projects/Jarvis.md': '# Jarvis\n\nPersonal local voice assistant for [[Knowledge/About me|Nivaen]].\n\n'
        'Use the existing ChatGPT/Codex login for reasoning. No paid API key is required.\n'
        'Keep proposed actions reviewable and verify their results.\n',
    'Decisions/Memory rules.md': '# Memory rules\n\nSave facts only when explicitly requested or reviewed. '
        'Do not store passwords, login tokens, or API keys in this vault.\n\n'
        'Jarvis excludes hidden folders, Private, Secrets, and notes whose top properties include '
        '`jarvis_private: true` or `jarvis_exclude: true`. Move an excluded note out of the shared scope '
        'or use that property before asking Jarvis to refresh. Deleting a file removes it from the local search index '
        'at the next successful refresh. It does not erase previously approved exports or old task checkpoints.\n',
}
STARTER_FOLDERS = ('Inbox', 'Knowledge', 'Projects', 'Decisions', 'Daily', 'Private')


class BrainError(ValueError):
    pass


def query_words(query):
    if not isinstance(query, str) or not 0 < len(query.strip()) <= MAX_QUERY_CHARS:
        raise BrainError('Use a short, nonempty note search.')
    return list(dict.fromkeys(word for word in re.findall(r'\w+', query.casefold())
        if len(word) > 1 and word not in STOP_WORDS))[:MAX_QUERY_WORDS]


def passage(text, terms, limit):
    """Extract verbatim text around a real hit, including hits late in a long note."""
    folded = text.casefold()
    starts = [folded.find(word) for word in terms if word in folded]
    start = max(0, min(starts) - limit // 4) if starts else 0
    if start:
        boundary = text.find('\n', start, min(len(text), start + MIN_PASSAGE_CHARS))
        if boundary >= 0:
            start = boundary + 1
    return text[start:start + limit]


class BrainIndex:
    def __init__(self, path):
        self.path = Path(path)
        self.db = None
        self.lock = threading.RLock()
        self.indexed = 0
        self.last_sync = {'changed': 0, 'removed': 0, 'unchanged': 0}

    def _open(self):
        if self.db is not None:
            return self.db
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise BrainError('The private brain index must be a regular local file.')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, PRIVATE_FILE_MODE)
        os.fchmod(fd, PRIVATE_FILE_MODE)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=SQL_TIMEOUT_S, check_same_thread=False)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS manifest (identity TEXT PRIMARY KEY, revision TEXT NOT NULL)')
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(identity UNINDEXED, title, aliases, tags, body)')
            db.commit()
        except Exception:
            db.close()
            raise
        self.db = db
        return db

    def sync(self, notes):
        with self.lock:
            db = self._open()
            previous = dict(db.execute('SELECT identity, revision FROM manifest'))
            current = {note['_path']: note for note in notes if note.get('_path') and not note.get('capture_hub')}
            changed, unchanged = 0, 0
            with db:
                for identity, note in current.items():
                    fingerprint = hashlib.sha256(json.dumps([note['label'], note['text'], note.get('aliases', []), note.get('tags', [])], ensure_ascii=True).encode()).hexdigest()
                    if previous.get(identity) == fingerprint:
                        unchanged += 1
                        continue
                    db.execute('DELETE FROM passages WHERE identity=?', (identity,))
                    db.execute('INSERT INTO passages VALUES (?,?,?,?,?)', (identity, note['label'], ' '.join(note.get('aliases', [])), ' '.join(note.get('tags', [])), note['text']))
                    db.execute('INSERT OR REPLACE INTO manifest VALUES (?,?)', (identity, fingerprint))
                    changed += 1
                for identity in previous.keys() - current.keys():
                    db.execute('DELETE FROM passages WHERE identity=?', (identity,))
                    db.execute('DELETE FROM manifest WHERE identity=?', (identity,))
            self.indexed = len(current)
            self.last_sync = {'changed': changed, 'removed': len(previous.keys() - current.keys()), 'unchanged': unchanged}
            return dict(self.last_sync)

    def search(self, query, notes, budget=DEFAULT_CONTEXT_CHARS, limit=MAX_RESULTS):
        terms = query_words(query)
        if type(budget) is not int or not MIN_CONTEXT_CHARS <= budget <= MAX_CONTEXT_CHARS or type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
            raise BrainError('Invalid local context budget.')
        with self.lock:
            self.sync(notes)
            if not terms:
                return []
            expression = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
            rows = self.db.execute('SELECT identity FROM passages WHERE passages MATCH ? ORDER BY bm25(passages,0,6,3,2,1), identity LIMIT ?', (expression, limit)).fetchall()
            by_path = {note.get('_path'): note for note in notes}
            result, remaining = [], budget
            for (identity,) in rows:
                if remaining < MIN_PASSAGE_CHARS:
                    break
                note = by_path.get(identity)
                if note is None:
                    continue
                text = passage(note['text'], terms, min(MAX_PASSAGE_CHARS, remaining))
                remaining -= len(text)
                result.append({'id': note['id'], 'title': note['label'], 'text': text,
                    'stable_id': note_store.stable_id(identity), 'revision': note_store.revision(note['text'], identity)})
            return result

    def close(self):
        with self.lock:
            if self.db is not None:
                self.db.close()
                self.db = None


class BrainService:
    def __init__(self, root, default_vault=None):
        self.root = Path(root)
        self.settings_path = self.root / 'settings.json'
        self.default_vault = Path(default_vault) if default_vault is not None else Path.home() / 'Documents' / 'Jarvis Brain'
        self.lock = threading.RLock()
        self.active_path = None
        self.index = None
        self.cache = {}
        self.last_scan = None
        self.last_refresh = None
        self.metrics = {'retrievals': 0, 'direct_lookups': 0, 'model_calls_avoided': 0,
            'selected_source_chars': 0, 'matching_full_note_chars': 0}

    def settings(self):
        if not self.settings_path.exists():
            return {'schema_version': SCHEMA_VERSION, 'vault_path': None, 'context_chars': DEFAULT_CONTEXT_CHARS}
        try:
            if self.root.is_symlink() or self.settings_path.is_symlink():
                raise BrainError('The private brain settings need attention.')
            descriptor = os.open(self.settings_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as handle:
                metadata = os.fstat(handle.fileno())
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_SETTINGS_BYTES
                        or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
                    raise BrainError('The private brain settings need attention.')
                encoded = handle.read(MAX_SETTINGS_BYTES + 1)
            if len(encoded) > MAX_SETTINGS_BYTES:
                raise BrainError('The private brain settings need attention.')
            value = json.loads(encoded)
        except (OSError, ValueError, TypeError):
            raise BrainError('The private brain settings need attention.') from None
        if (not isinstance(value, dict) or set(value) != {'schema_version', 'vault_path', 'context_chars'}
                or type(value['schema_version']) is not int or value['schema_version'] != SCHEMA_VERSION
                or value['vault_path'] is not None and (not isinstance(value['vault_path'], str) or not Path(value['vault_path']).is_absolute())
                or type(value['context_chars']) is not int or not MIN_CONTEXT_CHARS <= value['context_chars'] <= MAX_CONTEXT_CHARS):
            raise BrainError('The private brain settings need attention.')
        return value

    @staticmethod
    def prepare_vault(path):
        """Create new owner-only notes; an existing folder is never overwritten."""
        path = Path(path)
        if not path.is_absolute() or path.exists() or path.is_symlink():
            raise BrainError('Choose a new folder for Jarvis Brain; existing files are preserved.')
        if not path.parent.is_dir() or path.parent.is_symlink():
            raise BrainError('The selected parent folder must already exist and be a regular directory.')
        path.mkdir(mode=PRIVATE_DIR_MODE)
        for folder in STARTER_FOLDERS:
            (path / folder).mkdir(mode=PRIVATE_DIR_MODE)
        for relative, text in STARTER_FILES.items():
            with (path / relative).open('x', encoding='utf-8') as handle:
                os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        return {'prepared': True, 'folder': str(path), 'notes': len(STARTER_FILES), 'model_calls': 0}

    def configure(self, *, vault_path=None, context_chars=None):
        with self.lock:
            value = self.settings()
            if vault_path is not None:
                path = Path(vault_path)
                if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                    raise BrainError('Choose a regular existing vault folder.')
                if path == Path(path.anchor) or path == Path.home():
                    raise BrainError('Choose the specific shared vault, not your home folder.')
                # Validate the shared scope before persisting a connection.
                vault_files.read_notes(path)
                value['vault_path'] = str(path.resolve())
            if context_chars is not None:
                if type(context_chars) is not int or not MIN_CONTEXT_CHARS <= context_chars <= MAX_CONTEXT_CHARS:
                    raise BrainError('Choose a source budget between 1000 and 12000 characters.')
                value['context_chars'] = context_chars
            atomic_json(self.settings_path, value)
            return self.status()

    def disconnect(self):
        with self.lock:
            value = self.settings()
            value['vault_path'] = None
            atomic_json(self.settings_path, value)
            # Active operations keep their original source scope until restart.
            return self.status()

    def activate(self):
        """Called only during server startup, before tasks/requests can exist."""
        with self.lock:
            configured = self.settings()['vault_path']
            if configured is None:
                return None
            path = Path(configured)
            if not path.is_dir() or path.is_symlink():
                raise BrainError('The configured brain folder is unavailable. Restore it or disconnect it explicitly.')
            self.active_path = path.resolve()
            namespace = hashlib.sha256(str(self.active_path).encode()).hexdigest()
            self.index = BrainIndex(self.root / 'indexes' / (namespace + '.sqlite3'))
            return self.active_path

    def refresh(self, force=False):
        with self.lock:
            if self.active_path is None:
                return None
            now = time.monotonic()
            if not force and self.last_refresh is not None and now - self.last_refresh < REFRESH_INTERVAL_S:
                return None
            notes, cache, info = vault_files.read_notes(self.active_path, self.cache)
            changed = self.last_scan is None or self.last_scan['fingerprint'] != info['fingerprint']
            self.index.sync(notes)
            self.cache, self.last_scan, self.last_refresh = cache, info, now
            return notes if changed else None

    def search(self, query, notes):
        with self.lock:
            if self.active_path is None or self.index is None:
                return None
            results = self.index.search(query, notes, budget=self.settings()['context_chars'])
            by_id = {note['id']: note for note in notes}
            verified = []
            for item in results:
                note = by_id[item['id']]
                try:
                    current, _ = vault_files.read_file(self.active_path, note['_path'])
                except FileNotFoundError:
                    continue
                if vault_files.properties(current)['private']:
                    continue
                if note_store.revision(current, note['_path']) != item['revision']:
                    raise BrainError('A shared note changed. Refresh before using its passages.')
                verified.append(item)
            results = verified
            self.metrics['retrievals'] += 1
            self.metrics['selected_source_chars'] += sum(len(note['text']) for note in results)
            self.metrics['matching_full_note_chars'] += sum(len(by_id[item['id']]['text']) for item in results)
            return results

    def source(self, note):
        identity = note.get('_path')
        if self.active_path is None or not isinstance(identity, str) or not vault_files.allowed_path(identity):
            return None
        return {'id': note['id'], 'stable_id': note_store.stable_id(identity), 'title': note.get('label', note.get('title', 'Note')),
            'uri': 'obsidian://open?' + urllib.parse.urlencode({'path': str(self.active_path / identity)})}

    def direct_answer(self, question, notes):
        if self.active_path is None:
            return None
        lookup, read = LOOKUP.fullmatch(question.strip()), READ_NOTE.fullmatch(question.strip())
        if lookup is None and read is None:
            return None
        if read is not None:
            title = read.group(1).strip().strip('\"\'').casefold()
            matched = [note for note in notes if title in {note['label'].casefold(),
                str(Path(note.get('_path', '')).with_suffix('')).casefold(), *[alias.casefold() for alias in note.get('aliases', [])]}]
            if len(matched) != 1:
                result = {'answer': 'I could not identify one exact note, sir. Use its full title or folder path.', 'nodes': [], 'about_notes': False}
            else:
                note = matched[0]
                current, _ = vault_files.read_file(self.active_path, note['_path'])
                if vault_files.properties(current)['private']:
                    raise BrainError('That note is outside the shared brain scope.')
                if note_store.revision(current, note['_path']) != note_store.revision(note['text'], note['_path']):
                    raise BrainError('That note changed. Refresh before reading it.')
                title_text = note['label'][:MAX_SOURCE_TITLE_CHARS]
                text = current[:min(self.settings()['context_chars'], MAX_DIRECT_ANSWER_CHARS - len(title_text) - 2)]
                result = {'answer': title_text + '\n\n' + text, 'nodes': [note['id']], 'about_notes': True,
                    'truncated': len(text) < len(note['text']), 'brain_sources': [self.source(note)]}
        else:
            found = self.search(lookup.group(1), notes)
            by_id = {note['id']: note for note in notes}
            result = {'answer': ('From your local notes, sir:\n\n' + '\n\n'.join(item['title'][:MAX_SOURCE_TITLE_CHARS] + '\n' + item['text'] for item in found))[:MAX_DIRECT_ANSWER_CHARS]
                if found else 'No matching shared notes were found, sir.',
                'nodes': [item['id'] for item in found], 'about_notes': bool(found),
                'brain_sources': [self.source(by_id[item['id']]) for item in found]}
        with self.lock:
            self.metrics['direct_lookups'] += 1
            self.metrics['model_calls_avoided'] += 1
        result['local_memory'] = {'model_calls': 0, 'route': 'explicit_note_lookup', 'source': 'obsidian'}
        return result

    @staticmethod
    def bounded_history(history):
        """Keep a bounded recent verbatim window; no hidden summarizer/model call."""
        remaining, selected = HISTORY_CHARS, []
        for turn in reversed(history):
            if not isinstance(turn, dict) or not isinstance(turn.get('question'), str) or not isinstance(turn.get('answer'), str):
                continue
            question, answer = turn['question'], turn['answer']
            if len(question) >= remaining:
                break
            answer = answer[:remaining - len(question)]
            selected.append({'question': question, 'answer': answer})
            remaining -= len(question) + len(answer)
            if remaining == 0:
                break
        return list(reversed(selected))

    def status(self):
        with self.lock:
            value = self.settings()
            configured = value['vault_path']
            return {'schema_version': SCHEMA_VERSION, 'configured': configured is not None,
                'active': self.active_path is not None, 'vault_name': Path(configured).name if configured else None,
                'proposed_folder': str(self.default_vault),
                'restart_required': configured != (str(self.active_path) if self.active_path else None),
                'context_chars': value['context_chars'], 'index_engine': 'sqlite-fts5',
                'history_chars': HISTORY_CHARS,
                'index': {'notes': self.index.indexed if self.index else 0,
                    'last_sync': dict(self.index.last_sync) if self.index else None},
                'metrics': dict(self.metrics), 'metrics_scope': 'this_server_process; source characters, not billed tokens',
                'limits': {'notes': vault_files.MAX_NOTES, 'note_bytes': vault_files.MAX_NOTE_BYTES,
                    'vault_bytes': vault_files.MAX_VAULT_BYTES},
                'privacy': {'model_calls_for_search': 0, 'whole_vault_in_prompt': False,
                    'continuous_transcripts': False, 'hidden_and_private_notes_excluded': True}}

    def close(self):
        with self.lock:
            if self.index:
                self.index.close()

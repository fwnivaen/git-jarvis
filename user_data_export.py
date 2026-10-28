"""Explicit in-memory export of allowlisted user data, never config directories.

Only selected providers are called. Notes come from the trusted index inside one
configured vault; this module does not discover files, load settings files, copy
databases, inspect a process, or contact a service. Known credential forms in user
text are redacted; this is a portable selected-data export, not a full backup.
"""
from collections.abc import Mapping
from dataclasses import is_dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import zipfile

import activity_feed
import note_store
import vault_files

SCHEMA_VERSION = 1
CONTENT_TYPE = 'application/zip'
FILENAME = 'Jarvis user data.zip'
SECTIONS = frozenset({'notes', 'preferences', 'settings', 'workflows', 'activity'})
MAX_NOTES = 500
MAX_INDEX_RECORDS = 20000
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_PATH_CHARS = 512
MAX_PREFERENCES = 64
MAX_PREFERENCE_KEY = 80
MAX_PREFERENCE_VALUE = 2000
MAX_WORKFLOWS = 50
MAX_QUESTION_CHARS = 2000
MAX_ANSWER_CHARS = 8000
READ_CHUNK_BYTES = 65536
ZIP_COMPRESSION_LEVEL = 6
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
PRIVATE_FILE_MODE = 0o600
HEX_ID = re.compile(r'[a-f0-9]{24}\Z')
HEX_DIGEST = re.compile(r'[a-f0-9]{64}\Z')
SECRET_KEY = re.compile(r'(?:api.?key|password|authorization|secret|access.?token|refresh.?token)', re.I)
SECRET_PATTERNS = (
    re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|\Z)'),
    re.compile(r'\b(?:sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9_]{8,}|github_pat_[a-z0-9_]{8,})', re.I),
    re.compile(r'\bbearer\s+[a-z0-9._~+/-]+=*', re.I),
    re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'),
    re.compile(r'\bxox[baprs]-[0-9A-Za-z-]{10,}\b'),
    re.compile(r'''["']?\b(?:api[_ -]?key|authorization|password|secret|access[_ -]?token|refresh[_ -]?token)["']?\s*[:=]\s*["']?[^\s,;"']+''', re.I),
)
BOOL_SETTINGS = frozenset({'enabled', 'adaptive_routing_enabled', 'routing_enabled',
    'parallel_workers_enabled', 'live_monitor_enabled', 'terminal_monitor_enabled',
    'crew_profiles_enabled', 'crew_visual_enabled', 'collaboration_enabled',
    'structured_reports_enabled', 'workflows_enabled', 'checkpoint_enabled',
    'evaluation_enabled', 'memory_enabled', 'semantic_search_enabled',
    'document_import_enabled', 'preferences_enabled', 'tools_enabled', 'mcp_enabled',
    'cheap_vision_enabled', 'skip_unchanged_frames_enabled'})
INT_SETTINGS = {'max_workers': (1, 16), 'worker_retries': (0, 2),
    'worker_timeout_s': (1, 45), 'max_escalations': (0, 1), 'request_timeout_s': (1, 300),
    'model_call_limit': (1, 100), 'observed_token_limit': (1, 1_000_000)}
STRING_SETTINGS = {'quick_model': frozenset({'gpt-6-luna'}),
    'brain_model': frozenset({'gpt-6.1-sol'}), 'manual_model': frozenset({'gpt-6-astra'}),
    'effort': frozenset({'high'})}
OPERATIONS_BOOL_SETTINGS = frozenset({'daily_health_enabled', 'local_signals_enabled',
    'external_power_required', 'network_required', 'quiet_hours_enabled',
    'speak_background_updates'})
OPERATIONS_TIME_SETTINGS = frozenset({'daily_health_time', 'quiet_start', 'quiet_end'})
LOCAL_TIME = re.compile(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z')
OPERATIONS_TIMEZONE = 'America/New_York'
MIN_CONTEXT_CHARS = 1000
MAX_CONTEXT_CHARS = 12000
EXCLUSIONS = (
    'No config files, credentials, hidden files, Private/Secrets folders, private notes, symlinks, process data, checkpoints, or tool journals are copied.',
    'Only the chosen user-data sections are included. This archive cannot restore a complete installation.',
    'Recognized credential forms in selected text are replaced with [redacted]. Review user-authored text before sharing it.',
)


class ExportError(ValueError):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _redact(text):
    count = 0
    for pattern in SECRET_PATTERNS:
        text, replaced = pattern.subn('[redacted]', text)
        count += replaced
    return text, count


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
        allow_nan=False).encode('utf-8')


def _relative(value):
    if (not isinstance(value, str) or not 0 < len(value) <= MAX_PATH_CHARS
            or '\\' in value or any(ord(character) < 32 for character in value)):
        return None
    path = PurePosixPath(value)
    if (path.is_absolute() or any(part in ('.', '..') or part.startswith('.') or ':' in part for part in path.parts)
            or path.suffix.lower() != '.md' or _redact(value)[1]
            or not vault_files.allowed_path(value)):
        return None
    # Reject alternate spellings rather than normalizing a traversal or alias.
    if path.as_posix() != value:
        return None
    return path


def _read_markdown(root_fd, relative):
    parent = os.dup(root_fd)
    file_fd = None
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        file_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > note_store.MAX_NOTE_BYTES:
            return None
        chunks, size = [], 0
        while size <= note_store.MAX_NOTE_BYTES:
            chunk = os.read(file_fd, min(READ_CHUNK_BYTES, note_store.MAX_NOTE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(file_fd)
        if (size > note_store.MAX_NOTE_BYTES or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            return None
        text = b''.join(chunks).decode('utf-8')
        if len(text) > note_store.MAX_NOTE_CHARS:
            return None
        return text
    except (OSError, UnicodeError):
        return None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent)


class UserDataExport:
    def __init__(self, vault, indexed_notes, preferences, settings, workflows=None,
                 activity=None, clock=time.time, *, brain_settings=None, operations_settings=None):
        self.vault = Path(vault)
        self.providers = {'notes': indexed_notes, 'preferences': preferences,
            'settings': settings, 'workflows': workflows, 'activity': activity}
        self.clock = clock
        self.optional_settings = {'brain': brain_settings, 'operations': operations_settings}

    def create(self, sections):
        if (not isinstance(sections, list) or not sections or len(sections) > len(SECTIONS)
                or any(not isinstance(section, str) or section not in SECTIONS for section in sections)
                or len(set(sections)) != len(sections)):
            raise ExportError('Choose one or more known user-data sections.')
        now = self.clock()
        if not _number(now):
            raise ExportError('The export clock is unavailable.', 503)
        files, total = {}, 0
        report = {'redactions': 0, 'skipped_notes': 0, 'skipped_preferences': 0}
        counts = {}

        def add(name, data):
            nonlocal total
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ExportError('The selected data exceeds the export size limit.', 413)
            files[name] = data

        def clean(text):
            cleaned, replacements = _redact(text)
            report['redactions'] += replacements
            return cleaned

        for section in sorted(sections):
            provider = self.providers[section]
            if provider is None:
                raise ExportError('A selected user-data source is unavailable.', 503)
            try:
                source = provider()
                if section == 'notes':
                    if not isinstance(source, list) or len(source) > MAX_INDEX_RECORDS:
                        raise ValueError
                    if self.vault.is_symlink():
                        raise ValueError
                    root_fd = os.open(self.vault, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    seen, count = set(), 0
                    try:
                        for note in source:
                            relative = _relative(note.get('_path')) if isinstance(note, dict) else None
                            if relative is None or relative.as_posix() in seen:
                                report['skipped_notes'] += 1
                                continue
                            seen.add(relative.as_posix())
                            text = _read_markdown(root_fd, relative)
                            if text is None or vault_files.properties(text)['private']:
                                report['skipped_notes'] += 1
                                continue
                            count += 1
                            if count > MAX_NOTES:
                                raise ExportError('The selected data exceeds the export note limit.', 413)
                            add('notes/' + relative.as_posix(), clean(text).encode('utf-8'))
                    finally:
                        os.close(root_fd)
                    counts[section] = count
                elif section == 'preferences':
                    if not isinstance(source, dict) or len(source) > MAX_PREFERENCES:
                        raise ValueError
                    exported = {}
                    for key, value in source.items():
                        if (not isinstance(key, str) or not 0 < len(key.strip()) <= MAX_PREFERENCE_KEY
                                or SECRET_KEY.search(key) or _redact(key)[1]
                                or not isinstance(value, str) or len(value) > MAX_PREFERENCE_VALUE):
                            report['skipped_preferences'] += 1
                            continue
                        exported[key] = clean(value)
                    add('preferences.json', _json(exported))
                    counts[section] = len(exported)
                elif section == 'settings':
                    if not isinstance(source, Mapping) and not is_dataclass(source):
                        raise ValueError
                    get = source.get if isinstance(source, Mapping) else lambda key: getattr(source, key, None)
                    exported = {}
                    for key in BOOL_SETTINGS:
                        value = get(key)
                        if type(value) is bool:
                            exported[key] = value
                    for key, (minimum, maximum) in INT_SETTINGS.items():
                        value = get(key)
                        if type(value) is int and minimum <= value <= maximum:
                            exported[key] = value
                    for key, choices in STRING_SETTINGS.items():
                        value = get(key)
                        if isinstance(value, str) and value in choices:
                            exported[key] = value
                    add('settings.json', _json(exported))
                    counts[section] = len(exported)
                    for name, additional in self.optional_settings.items():
                        if additional is None:
                            continue
                        extra = additional()
                        if not isinstance(extra, Mapping) and not is_dataclass(extra):
                            raise ValueError
                        extra_get = extra.get if isinstance(extra, Mapping) else lambda key: getattr(extra, key, None)
                        exported = {}
                        if name == 'brain':
                            value = extra_get('context_chars')
                            if type(value) is int and MIN_CONTEXT_CHARS <= value <= MAX_CONTEXT_CHARS:
                                exported['context_chars'] = value
                        elif name == 'operations':
                            for key in OPERATIONS_BOOL_SETTINGS:
                                value = extra_get(key)
                                if type(value) is bool:
                                    exported[key] = value
                            for key in OPERATIONS_TIME_SETTINGS:
                                value = extra_get(key)
                                if isinstance(value, str) and LOCAL_TIME.fullmatch(value):
                                    exported[key] = value
                            value = extra_get('timezone')
                            if isinstance(value, str) and value == OPERATIONS_TIMEZONE:
                                exported['timezone'] = value
                            value = extra_get('minimum_battery_percent')
                            if type(value) is int and 0 <= value <= 100:
                                exported['minimum_battery_percent'] = value
                        add(name + '-settings.json', _json(exported))
                        counts[section] += len(exported)
                elif section == 'workflows':
                    rows = source.get('tasks', source.get('runs')) if isinstance(source, dict) else None
                    if not isinstance(rows, list) or len(rows) > MAX_WORKFLOWS:
                        raise ValueError
                    exported = []
                    for run in rows:
                        if (not isinstance(run, dict) or not isinstance(run.get('id'), str)
                                or not HEX_ID.fullmatch(run['id']) or run.get('status') not in activity_feed.STATUSES
                                or run.get('workflow') not in activity_feed.WORKFLOWS):
                            continue
                        item = {'id': run['id'], 'status': run['status'], 'workflow': run['workflow']}
                        if _number(run.get('created_at')):
                            item['created_at'] = run['created_at']
                        question = run.get('question')
                        if isinstance(question, str):
                            item['question'] = clean(question[:MAX_QUESTION_CHARS])
                        result = run.get('result')
                        if isinstance(result, dict) and isinstance(result.get('answer'), str):
                            item['answer'] = clean(result['answer'][:MAX_ANSWER_CHARS])
                        exported.append(item)
                    add('workflows.json', _json(exported))
                    counts[section] = len(exported)
                else:
                    rows = source.get('items') if isinstance(source, dict) else None
                    if not isinstance(rows, list) or len(rows) > activity_feed.MAX_ITEMS:
                        raise ValueError
                    exported = []
                    maps = {'workflows': activity_feed.WORKFLOW_EVENTS,
                        'approvals': dict(activity_feed.APPROVAL_EVENTS, proposed='Note edit proposed',
                            approved='Note edit approved'), 'tools': activity_feed.TOOL_EVENTS}
                    for event in rows:
                        if (not isinstance(event, dict) or event.get('source') not in maps
                                or not isinstance(event.get('id'), str) or not HEX_DIGEST.fullmatch(event['id'])
                                or not isinstance(event.get('subject_id'), str) or not HEX_ID.fullmatch(event['subject_id'])
                                or not _number(event.get('at'))):
                            continue
                        source_name, kind = event['source'], event.get('kind')
                        label = maps[source_name].get(kind) if isinstance(kind, str) else None
                        if kind == 'status' and source_name == 'workflows' and event.get('status') in activity_feed.STATUSES:
                            label = 'Work status: ' + event['status']
                        if label is None:
                            continue
                        exported.append({'id': event['id'], 'source': source_name,
                            'subject_id': event['subject_id'], 'kind': kind, 'label': label, 'at': event['at']})
                    add('activity.json', _json({'items': exported,
                        'limitations': list(activity_feed.LIMITATIONS)}))
                    counts[section] = len(exported)
            except ExportError:
                raise
            except Exception:
                raise ExportError('A selected user-data source could not be safely exported.', 503) from None
        try:
            created_at = datetime.fromtimestamp(now, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            raise ExportError('The export clock is unavailable.', 503) from None
        manifest = {'schema_version': SCHEMA_VERSION, 'created_at': created_at,
            'sections': sorted(sections), 'counts': counts, **report,
            'files': [{'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                for name, data in sorted(files.items())], 'limitations': list(EXCLUSIONS)}
        add('manifest.json', _json(manifest))
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED,
                compresslevel=ZIP_COMPRESSION_LEVEL) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
                info.create_system = 3
                info.external_attr = PRIVATE_FILE_MODE << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        result = output.getvalue()
        if len(result) > MAX_ARCHIVE_BYTES:
            raise ExportError('The selected data exceeds the archive size limit.', 413)
        return result

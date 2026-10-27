"""Bounded, on-demand local readiness facts without activating any capability.

This module never reads private configuration or note contents. It does not
execute tools, open SQLite databases, call a network service, request a macOS
permission, install a model, write files, or schedule work. Callers supply only
normalized backend/gate facts and any already obtained permission result.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import plistlib
import shutil
import stat
from xml.parsers.expat import ExpatError

SCHEMA_VERSION = 1
MAX_PLIST_BYTES = 65536
SQLITE_HEADER = b'SQLite format 3\x00'
ALLOWED_BACKENDS = frozenset({'codex-cli', 'codex-app-server'})
GATE_NAMES = ('enabled', 'workflows_enabled', 'tools_enabled', 'checkpoint_enabled',
    'memory_enabled', 'semantic_search_enabled', 'document_import_enabled', 'preferences_enabled')
TOOL_NAMES = ('codex', 'textutil', 'pdftotext')
PERMISSIONS = frozenset({'allowed', 'denied', 'restricted', 'not requested', 'unknown'})
LIMITS = (
    'This overview checks local metadata and supplied settings, not a live model answer or account allowance.',
    'A present SQLite header does not prove database integrity, recovery, or source freshness.',
    'A native bundle is an installed artifact; the running helper, hotkeys, microphone and speech are not exercised.',
    'No battery, network, foreground, call, sharing, presence or Do Not Disturb signal is collected.',
    'Guidance does not change settings or permissions. Setup, activation, data export and daily monitoring remain separate actions.',
)


def _metadata(path, directory=False):
    """Only regular files/directories qualify; errors never expose paths."""
    try:
        value = Path(path).lstat()
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(value.st_mode):
            return None, 'unsafe'
        return value, None
    except FileNotFoundError:
        return None, 'missing'
    except (OSError, TypeError, ValueError):
        return None, 'unavailable'


def _private(value):
    return value is not None and value.st_uid == os.getuid() and not stat.S_IMODE(value.st_mode) & 0o077


def _read_prefix(path, length):
    # NOFOLLOW also closes the check/open race for the final path component.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError('Not a regular file')
        return handle.read(length)


def _executable(path):
    if path is None:
        return False
    try:
        # Installed command paths may themselves be symlinks (for example a
        # package-manager command). We inspect executability, never run them.
        return Path(path).is_file() and os.access(path, os.X_OK)
    except (OSError, TypeError, ValueError):
        return False


def _path(value):
    try:
        return Path(value)
    except (TypeError, ValueError):
        return None


def _schema_ready(schema):
    try:
        properties = schema['properties']
        return (schema['type'] == 'object' and schema.get('additionalProperties') is False
            and set(schema['required']) == {'answer', 'about_notes', 'nodes'}
            and properties['answer']['type'] == 'string'
            and properties['about_notes']['type'] == 'boolean'
            and properties['nodes']['type'] == 'array'
            and properties['nodes']['items']['type'] == 'integer')
    except (KeyError, TypeError, AttributeError):
        return False


def snapshot(root, *, server_running=False, backend=None, gates=None, settings_notice=None,
             answer_schema=None, codex_path=None, workflow_root=None, native_bundle=None,
             native_status=None, tool_paths=None):
    """Return fixed-language facts, capability states and bounded setup guidance.

    ``gates`` accepts only known boolean feature flags, never a raw config.
    ``native_status`` is optional: microphone/speech strings from PERMISSIONS
    and an optional boolean ``on_device_model_ready`` from a read-only probe.
    Unknown inputs are reported as unchecked/invalid without being reflected.
    Paths are trusted server-owned locations and never appear in the result.
    """
    checks = []
    def check(identifier, title, status, detail, action=None, required=False):
        checks.append({'id': identifier, 'title': title, 'status': status,
            'detail': detail, 'action': action, 'required': required})

    base = _path(root)
    _, base_error = _metadata(base, directory=True)
    project_ok = base_error is None
    check('project', 'Project files', 'ok' if project_ok else 'blocked',
        'The local project directory is present.' if project_ok else 'The project directory cannot be inspected safely.',
        None if project_ok else 'Restore the local project directory before setup.', True)
    check('server', 'Local server', 'ok' if server_running is True else 'not_checked',
        'This overview was requested from the running local server.' if server_running is True else
        'The caller did not confirm a running local server.', required=True)

    def location(*parts):
        return base.joinpath(*parts) if project_ok else None
    artifacts = ('server.py', 'viewer/index.html', 'viewer/graph-data.js')
    build_ok = project_ok and all(_metadata(location(*name.split('/')))[1] is None for name in artifacts)
    check('build', 'Server and viewer artifacts', 'ok' if build_ok else 'blocked',
        'Expected server and viewer artifacts are present; their contents were not read.' if build_ok else
        'One or more expected server/viewer artifacts are unavailable.',
        None if build_ok else 'Restore or build the server and viewer artifacts.', True)
    schema_ok = _schema_ready(answer_schema)
    check('answer_schema', 'Answer contract', 'ok' if schema_ok else 'not_checked' if answer_schema is None else 'blocked',
        'The supplied answer contract has the expected typed fields.' if schema_ok else
        'No answer contract was supplied.' if answer_schema is None else 'The supplied answer contract is not the expected strict shape.',
        None if schema_ok else 'Check the server answer contract before using model replies.', True)

    config_stat, config_error = _metadata(location('config.json'))
    config_ok = config_error is None and _private(config_stat)
    check('private_config', 'Private configuration', 'ok' if config_ok else 'blocked' if config_error else 'warning',
        'The configuration file is owned by this user and has private permissions; its contents were not read.' if config_ok else
        'The configuration file is absent or cannot be inspected safely.' if config_error else
        'Configuration permissions or ownership need review; its contents were not read.',
        None if config_ok else 'Use the existing setup process and keep configuration private.', True)
    notes_ok = _metadata(location('notes'), directory=True)[1] is None
    check('notes_directory', 'Notes folder', 'ok' if notes_ok else 'blocked',
        'The notes directory is present. No note names or contents were read.' if notes_ok else
        'The notes directory is absent or cannot be inspected safely.',
        None if notes_ok else 'Restore the notes folder before asking about local notes.', True)

    normalized_backend = backend if isinstance(backend, str) and backend in ALLOWED_BACKENDS else None
    backend_known = isinstance(backend, str) and bool(backend)
    check('backend', 'Included model route', 'ok' if normalized_backend else 'blocked' if backend_known else 'not_checked',
        'A permitted signed-in Codex route is configured. Login and allowance were not queried.' if normalized_backend else
        'The supplied backend is outside the permitted included-usage routes.' if backend_known else
        'No normalized backend fact was supplied; private configuration was not opened.',
        'Confirm Codex is signed in with ChatGPT before the first model request.' if normalized_backend else
        'Select a permitted signed-in Codex route in the existing settings.', True)

    gate_input_ok = isinstance(gates, dict) and not set(gates) - set(GATE_NAMES) and all(type(value) is bool for value in gates.values())
    safe_gates = dict(gates) if gate_input_ok else {}
    notice_ok = settings_notice is None
    check('feature_settings', 'Feature settings', 'ok' if gate_input_ok and notice_ok else 'warning' if settings_notice is not None else 'not_checked',
        'Known boolean feature gates were supplied; no settings values or secrets are returned.' if gate_input_ok and notice_ok else
        'Feature settings need review; defaults may be active.' if settings_notice is not None else
        'No valid normalized feature-gate snapshot was supplied.',
        None if gate_input_ok and notice_ok else 'Review the existing feature settings before enabling optional capabilities.')

    supplied_tools = tool_paths if isinstance(tool_paths, dict) else {}
    installed = {}
    for name in TOOL_NAMES:
        # An explicit mapping makes fixture/offline checks independent of PATH.
        candidate = supplied_tools.get(name) if tool_paths is not None else shutil.which(name)
        if name == 'codex' and codex_path is not None:
            candidate = codex_path
        installed[name] = _executable(candidate)
        check('tool_' + name, {'codex': 'Codex command', 'textutil': 'Word text reader', 'pdftotext': 'PDF text reader'}[name],
            'ok' if installed[name] else 'blocked' if name == 'codex' else 'not_checked',
            'The installed command is executable. It was not run.' if installed[name] else
            'The command was not found or is not executable.',
            None if installed[name] else 'Install or locate Codex before using the signed-in model route.' if name == 'codex' else
            'This optional reader can be installed when its document format is needed.', name == 'codex')

    store = _path(workflow_root) if workflow_root is not None else location('.local', 'graph-workflows')
    store_stat, store_error = _metadata(store, directory=True)
    catalog_path = store / 'catalog.sqlite' if store is not None else None
    catalog_stat, catalog_error = _metadata(catalog_path)
    store_state = 'not_initialized'
    if store_error == 'missing' and catalog_error == 'missing':
        detail = 'No Work store exists yet. A first task may initialize private storage; this overview did not.'
        status, action = 'not_checked', None
    elif store_error or not _private(store_stat):
        store_state, status = 'unsafe', 'warning'
        detail = 'Work storage ownership, permissions or location need review.'
        action = 'Review private Work storage before starting or resuming a task.'
    elif catalog_error == 'missing':
        detail = 'The private Work directory is present; no catalog has been initialized.'
        status, action = 'not_checked', None
    else:
        try:
            header_ok = catalog_error is None and _read_prefix(catalog_path, len(SQLITE_HEADER)) == SQLITE_HEADER
        except OSError:
            header_ok = False
        if header_ok and _private(catalog_stat):
            store_state, status, action = 'present', 'ok', None
            detail = 'A private Work catalog has an SQLite header. No database was opened or queried.'
        else:
            store_state, status = 'unverified', 'warning'
            detail = 'The Work catalog cannot be verified from its header or private permissions.'
            action = 'Review or recover saved Work storage before resuming a task.'
    check('work_store', 'Saved Work storage', status, detail, action)

    bundle = _path(native_bundle) if native_bundle is not None else location('.local', 'Jarvis Desktop.app')
    native_ok = False
    if _metadata(bundle, directory=True)[1] is None:
        info = bundle / 'Contents' / 'Info.plist'
        info_stat, error = _metadata(info)
        try:
            if error is None and 0 < info_stat.st_size <= MAX_PLIST_BYTES:
                manifest = plistlib.loads(_read_prefix(info, MAX_PLIST_BYTES + 1))
                native_ok = (isinstance(manifest, dict) and manifest.get('CFBundleExecutable') == 'JarvisDesktop'
                    and manifest.get('CFBundleIdentifier') == 'local.fwnivaen.jarvis.desktop'
                    and _metadata(bundle / 'Contents' / 'MacOS' / 'JarvisDesktop')[1] is None
                    and _executable(bundle / 'Contents' / 'MacOS' / 'JarvisDesktop'))
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException, OverflowError, ExpatError):
            pass
    check('native_artifact', 'Native voice helper artifact', 'ok' if native_ok else 'not_checked',
        'The expected native bundle and executable are present. Running state and signature were not checked.' if native_ok else
        'A matching native helper artifact was not found or could not be verified.',
        None if native_ok else 'Build or choose the native helper before activating global voice.')

    supplied_native_ok = (isinstance(native_status, dict) and
        not set(native_status) - {'microphone', 'speech', 'on_device_model_ready'} and
        all(isinstance(native_status.get(name, 'unknown'), str) and
            native_status.get(name, 'unknown') in PERMISSIONS for name in ('microphone', 'speech')) and
        ('on_device_model_ready' not in native_status or type(native_status['on_device_model_ready']) is bool))
    native = native_status if supplied_native_ok else {}
    microphone = native.get('microphone', 'unknown')
    speech_ready = native.get('speech') == 'allowed' or native.get('on_device_model_ready') is True
    voice_ready = native_ok and microphone == 'allowed' and speech_ready
    permission_blocked = microphone in {'denied', 'restricted'} or (
        native.get('speech') in {'denied', 'restricted'} and not native.get('on_device_model_ready'))
    check('voice_permissions', 'Supplied voice readiness', 'ok' if voice_ready else 'warning' if permission_blocked else 'not_checked',
        'A supplied read-only result reports microphone permission and an on-device speech path. No live voice check was run.' if voice_ready else
        'A supplied voice permission result needs attention.' if permission_blocked else
        'Voice permissions and speech readiness were not verified by this overview.',
        None if voice_ready else 'Use the native helper’s read-only permission check; grant microphone access only when choosing voice.')
    for identifier, title in (('battery', 'Battery scheduling'), ('network', 'Network scheduling')):
        check(identifier, title, 'not_checked',
            'No trustworthy opt-in local signal probe is configured. No signal was collected.',
            None)

    work_enabled = all(safe_gates.get(name) is True for name in ('enabled', 'workflows_enabled', 'tools_enabled'))
    if not work_enabled:
        check('work_activation', 'Work feature gates', 'disabled' if gate_input_ok else 'not_checked',
            'Work requires its existing Ultra, workflow and tool gates. This overview changed none of them.',
            'Review the existing Work settings if you choose to enable background tasks.')
    guidance = [{'id': item['id'], 'title': item['title'], 'status': item['status'],
        'detail': item['detail'], 'action': item['action']} for item in checks if item['action']]
    blocked = any(item['status'] in {'blocked', 'warning'} for item in checks)
    return {
        'schema_version': SCHEMA_VERSION, 'on_demand': True,
        'observed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'status': 'attention' if blocked else 'limited_check', 'checks': checks,
        'capabilities': {
            'local_notes_metadata': notes_ok and build_ok,
            'included_model_route_configured': bool(normalized_backend and installed['codex']),
            'model_login_verified': False, 'model_allowance_verified': False,
            'work_enabled': work_enabled, 'work_store': store_state,
            'native_artifact_present': native_ok, 'native_running_verified': False,
            'voice_readiness_supplied': voice_ready, 'live_voice_verified': False,
            'battery_signal_collected': False, 'network_signal_collected': False,
        },
        'first_run': {'complete': False, 'guidance': guidance,
            'remaining': ['Live model login and allowance are not checked here',
                'Voice and shortcuts for the active installed build are not exercised here',
                'Optional permissions remain user choices', 'Unified settings and complete data export remain separate']},
        'side_effects': {'config_reads': False, 'note_reads': False, 'database_opens': False,
            'model_calls': False, 'tool_execution': False, 'network_requests': False,
            'permission_prompts': False, 'file_writes': False, 'scheduled_work': False},
        'limits': list(LIMITS),
    }

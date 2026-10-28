"""Opt-in daily local health checks owned by the current server process.

No OS service is registered. Disabled startup does not create storage or a
thread. The caller supplies a read-only, model-free health snapshot callback.
Only fixed settings, timestamps and aggregate check counts are persisted.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from zoneinfo import ZoneInfo

from delivery_policy import TIMEZONE, aware_now, local_time, resource_gate

MAX_STATE_BYTES = 16384
POLL_SECONDS = 30
FAILURE_BACKOFF_SECONDS = 300
CHECK_STATUSES = ('ok', 'warning', 'blocked', 'not_checked', 'disabled')


@dataclass(frozen=True)
class OperationsSettings:
    daily_health_enabled: bool = False
    daily_health_time: str = '09:00'
    timezone: str = TIMEZONE
    local_signals_enabled: bool = False
    minimum_battery_percent: int = 20
    external_power_required: bool = False
    network_required: bool = False
    quiet_hours_enabled: bool = False
    quiet_start: str = '22:00'
    quiet_end: str = '08:00'
    speak_background_updates: bool = False

    @classmethod
    def from_values(cls, values):
        if not isinstance(values, dict) or set(values) - {item.name for item in fields(cls)}:
            raise ValueError('Unknown local operations setting.')
        result = cls(**values)
        for item in fields(cls):
            if type(getattr(result, item.name)) is not item.type:
                raise ValueError('Invalid local operations setting type.')
        local_time(result.daily_health_time)
        if local_time(result.quiet_start) == local_time(result.quiet_end):
            raise ValueError('Quiet hours need different start and end times.')
        if result.timezone != TIMEZONE or not 0 <= result.minimum_battery_percent <= 100:
            raise ValueError('Invalid local operations time or battery setting.')
        if (result.external_power_required or result.network_required) and not result.local_signals_enabled:
            raise ValueError('Enable local signals before requiring power or network configuration.')
        return result


def _private(value):
    return value.st_uid == os.getuid() and not stat.S_IMODE(value.st_mode) & 0o077


class OperationsStore:
    def __init__(self, root):
        self.root = Path(root)

    def _directory(self, create=False):
        # The dedicated directory and its immediate parent cannot be links.
        if self.root.parent.is_symlink() or self.root.is_symlink():
            raise ValueError('Unsafe operations storage.')
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = self.root.lstat()
        if not stat.S_ISDIR(value.st_mode) or not _private(value):
            raise ValueError('Private operations storage is required.')

    def read(self, name):
        try:
            self._directory()
            path = self.root / name
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(descriptor, 'rb') as handle:
                value = os.fstat(handle.fileno())
                if not stat.S_ISREG(value.st_mode) or not _private(value) or value.st_size > MAX_STATE_BYTES:
                    raise ValueError('Unsafe operations file.')
                result = json.loads(handle.read(MAX_STATE_BYTES + 1))
            if not isinstance(result, dict):
                raise ValueError('Invalid operations file.')
            return result
        except FileNotFoundError:
            return None

    def write(self, name, value):
        self._directory(create=True)
        path = self.root / name
        if path.exists() or path.is_symlink():
            existing = path.lstat()
            if not stat.S_ISREG(existing.st_mode) or not _private(existing):
                raise ValueError('Unsafe operations file.')
        encoded = json.dumps(value, allow_nan=False, separators=(',', ':')).encode()
        if len(encoded) > MAX_STATE_BYTES:
            raise ValueError('Operations state exceeded its bound.')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as handle:
                temporary = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @contextmanager
    def owner(self):
        self._directory(create=True)
        descriptor = os.open(self.root / 'health.lock', os.O_RDWR | os.O_CREAT |
            getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), 0o600)
        with os.fdopen(descriptor, 'r+b') as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not _private(metadata):
                raise ValueError('Unsafe operations lock.')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _state(value):
    empty = {'last_success_date': None, 'last_attempt_at': None, 'last_outcome': None, 'last_result': None}
    if value is None:
        return empty
    if set(value) != set(empty):
        raise ValueError('Invalid health state.')
    if value['last_success_date'] is not None:
        date.fromisoformat(value['last_success_date'])
    if value['last_attempt_at'] is not None:
        aware_now(datetime.fromisoformat(value['last_attempt_at']))
    if value['last_outcome'] not in (None, 'running', 'complete', 'failed'):
        raise ValueError('Invalid health state.')
    result = value['last_result']
    if result is not None:
        if (not isinstance(result, dict) or set(result) != {'status', 'checked_at', 'counts'}
                or result['status'] not in ('ok', 'attention', 'unknown')
                or not isinstance(result['counts'], dict) or set(result['counts']) != set(CHECK_STATUSES)
                or any(type(count) is not int or not 0 <= count <= 256 for count in result['counts'].values())):
            raise ValueError('Invalid health summary.')
        aware_now(datetime.fromisoformat(result['checked_at']))
    return value


def _summary(value, now):
    counts = dict.fromkeys(CHECK_STATUSES, 0)
    if isinstance(value, dict) and isinstance(value.get('checks'), list):
        for check in value['checks'][:256]:
            status = check.get('status') if isinstance(check, dict) else None
            if isinstance(status, str) and status in counts:
                counts[status] += 1
    status = 'attention' if counts['warning'] or counts['blocked'] else 'ok' if sum(counts.values()) else 'unknown'
    return {'status': status, 'checked_at': now.astimezone(timezone.utc).isoformat(timespec='seconds'), 'counts': counts}


def daily_due(day, configured_time='09:00'):
    """First occurrence on fall-back; spring gaps normalize forward by the gap."""
    moment = datetime.combine(day, local_time(configured_time), ZoneInfo(TIMEZONE))
    return moment.astimezone(timezone.utc)


class DailyHealthScheduler:
    def __init__(self, root, snapshot_factory, signal_provider=None, clock=None):
        self.store = OperationsStore(root)
        self.snapshot_factory = snapshot_factory
        self.signal_provider = signal_provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._operation = threading.Lock()
        self._configuration = threading.Lock()
        self._lifecycle = threading.Lock()
        self._stop = threading.Event()
        self._running = threading.Event()
        self._thread = None
        self.notice = None
        self.last_deferral = None
        try:
            self.settings = OperationsSettings.from_values(self.store.read('settings.json') or {})
        except (OSError, ValueError, TypeError):
            self.settings = OperationsSettings()
            self.notice = 'invalid_operations_settings'

    def configure(self, changes):
        if not isinstance(changes, dict):
            raise ValueError('Supply local operations settings.')
        with self._configuration:
            settings = OperationsSettings.from_values({**asdict(self.settings), **changes})
            self.store.write('settings.json', asdict(settings))
            self.settings, self.notice = settings, None
        if settings.daily_health_enabled:
            self.start()
        else:
            self.stop()
        return self.status()

    def start(self):
        with self._lifecycle:
            if not self.settings.daily_health_enabled:
                return False
            self._stop.clear()
            if self._thread is not None and self._thread.is_alive():
                return False
            self._thread = threading.Thread(target=self._loop, daemon=True, name='jarvis-daily-health')
            self._thread.start()
            return True

    def stop(self, timeout=2):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0, min(timeout, 5)))
        return not self._running.is_set() and (thread is None or not thread.is_alive())

    def _loop(self):
        try:
            while not self._stop.is_set() and self.settings.daily_health_enabled:
                try:
                    self.run_pending()
                except Exception:
                    # A supplied clock/provider failure must not crash-loop
                    # replacement threads or echo its exception payload.
                    self.notice = 'health_scheduler_unavailable'
                self._stop.wait(POLL_SECONDS)
        finally:
            with self._lifecycle:
                if self._thread is threading.current_thread():
                    self._thread = None
                restart = self.settings.daily_health_enabled and not self._stop.is_set()
            if restart:
                self.start()

    def _read_state(self):
        return _state(self.store.read('health.json'))

    def status(self):
        notice = self.notice
        try:
            saved = self._read_state()
        except (OSError, ValueError, TypeError):
            saved, notice = _state(None), 'invalid_health_state'
        return {'schema_version': 1, 'settings': asdict(self.settings), 'notice': notice,
            'running': self._running.is_set(), 'scheduled': self._thread is not None and self._thread.is_alive(),
            'last_deferral': self.last_deferral, **saved,
            'limits': {'server_process_only': True, 'os_service_registered': False,
                'model_calls': False, 'snapshot_contents_saved': False,
                'network_reachability_verified': False, 'presence_inferred': False}}

    def run_now(self, now=None):
        return self._run(aware_now(self.clock() if now is None else now), manual=True)

    def run_pending(self, now=None):
        if not self.settings.daily_health_enabled:
            return {'status': 'disabled'}
        return self._run(aware_now(self.clock() if now is None else now), manual=False)

    def _run(self, now, manual):
        if not self._operation.acquire(blocking=False):
            return {'status': 'busy'}
        self._running.set()
        try:
            settings = self.settings
            # A disabled due check must not even create an ownership file.
            if not manual and not settings.daily_health_enabled:
                return {'status': 'disabled'}
            with self.store.owner() as owned:
                if not owned:
                    return {'status': 'busy'}
                state = self._read_state()
                today = now.astimezone(ZoneInfo(TIMEZONE)).date()
                if not manual:
                    if state['last_success_date'] is not None and date.fromisoformat(state['last_success_date']) >= today:
                        return {'status': 'already_checked'}
                    if now < daily_due(today, settings.daily_health_time):
                        return {'status': 'not_due'}
                    if state['last_attempt_at'] is not None and state['last_outcome'] in ('failed', 'running'):
                        attempted = datetime.fromisoformat(state['last_attempt_at'])
                        if now - attempted < timedelta(seconds=FAILURE_BACKOFF_SECONDS):
                            return {'status': 'backoff'}
                    if settings.local_signals_enabled:
                        try:
                            signals = self.signal_provider() if self.signal_provider is not None else None
                        except Exception:
                            signals = None
                        gate = resource_gate(signals, minimum_battery_percent=settings.minimum_battery_percent,
                            external_power_required=settings.external_power_required, network_required=settings.network_required)
                        if not gate['allow']:
                            self.last_deferral = gate['reason']
                            return {'status': 'deferred', 'reason': gate['reason']}
                state.update(last_attempt_at=now.astimezone(timezone.utc).isoformat(timespec='seconds'), last_outcome='running')
                self.store.write('health.json', state)
                try:
                    result = _summary(self.snapshot_factory(), now)
                except Exception:
                    state['last_outcome'] = 'failed'
                    self.store.write('health.json', state)
                    return {'status': 'failed', 'reason': 'health_check_unavailable'}
                state.update(last_success_date=today.isoformat(), last_outcome='complete', last_result=result)
                self.store.write('health.json', state)
                self.last_deferral = None
                return {'status': 'complete', 'result': result}
        except (OSError, ValueError, TypeError):
            return {'status': 'unavailable', 'reason': 'private_health_storage_or_signal_unavailable'}
        finally:
            self._running.clear()
            self._operation.release()

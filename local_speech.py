"""Standard-library bridge to the isolated, resident local speech worker.

The HTTP server never imports MLX or audio packages. Only opaque, owned WAV
results are exposed; configuration, prompts and model paths are not served.
"""
import collections
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave

from speech_plan import spoken_text, speaking_plan

MAX_JOBS = 128
MAX_QUEUE = 8
JOB_TTL_S = 3600
JOB_TIMEOUT_S = 180
WORKER_IDLE_S = 300
ACTIVE_IDLE_S = 600            # avoid paying cold startup between conversational turns
ACTIVE_WINDOW_S = 900
ACTIVE_REPLIES = 2
BACKGROUND_DELAY_S = 2.0        # background renders start only after the voice has been idle this long
MAX_BACKGROUND = 32             # acknowledgements plus the local social replies (quick_replies.py)
WAKE = ''
SHUTDOWN_WAIT_S = 2
MAX_PACKET_BYTES = 65536
MAX_CACHE_BYTES = 256 * 1024**2
MAX_CACHE_FILES = 128
ID_PATTERN = re.compile(r'[a-f0-9]{32}')
REQUEST_PATTERN = re.compile(r'[A-Za-z0-9_-]{16,100}')
TERMINAL = frozenset(('ready', 'failed', 'cancelled'))
ACTIVE_STATES = frozenset(('loading', 'generating', 'streaming', 'finishing', 'cleaning'))
MAX_PARTS = 4096
MAX_TIMELINE = 512
WORKER_ENV = ('HOME', 'USER', 'LOGNAME', 'PATH', 'TMPDIR', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
VOICE_ERROR = 'I could not prepare the voice. The reply is still on screen.'


def valid_timeline(value):
    if not isinstance(value, list) or not 0 < len(value) <= MAX_TIMELINE:
        return None
    clean, last = [], (-1.0, -1.0)
    for item in value:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in item)):
            return None
        seconds, fraction = float(item[0]), float(item[1])
        if not (0 <= seconds < 3600 and 0 <= fraction <= 1 and seconds >= last[0] and fraction >= last[1]):
            return None
        clean.append([seconds, fraction]); last = (seconds, fraction)
    return clean


class LocalSpeech:
    def __init__(self, root, *, worker_command=None, cache_directory=None):
        self.root = Path(root)
        self.profile_path = self.root / 'voice-profile.json'
        self.profile = json.loads(self.profile_path.read_text())
        self.pilot = (self.root / self.profile['pilot_directory']).resolve()
        self.cache = Path(cache_directory or self.root / '.local/m1-voice-cache').resolve()
        self.worker_command = worker_command
        self.lock = threading.Condition(threading.RLock())
        self.write_lock = threading.Lock()
        self.jobs = collections.OrderedDict()
        self.requests = {}
        self.cancelled_requests = {}
        self.queue = queue.Queue(MAX_QUEUE)
        self.process = None
        self.worker_ready = False
        self.active = None
        self.closed = False
        self.thread = None
        self.fingerprint = self._fingerprint()
        self.recent = collections.deque(maxlen=ACTIVE_REPLIES)
        self.last_activity = time.monotonic()
        # Short phrases (acknowledgements) rendered only while the voice is idle;
        # any real reply cancels one in progress and it is retried later.
        self.background = collections.OrderedDict()
        # Diagnostics may import this controller while the live server is using
        # the same cache. Never erase another instance's in-flight audio.
        for folder in (self.cache / 'stream').glob('*'):
            try:
                if ID_PATTERN.fullmatch(folder.name) and time.time() - folder.stat().st_mtime > JOB_TTL_S:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass

    def _fingerprint(self):
        digest = hashlib.sha256(self.profile_path.read_bytes())
        for path in (self.root / 'local_speech_worker.py', self.root / 'speech_plan.py', self.root / 'speech_timing.py',
                     self.root / 'm1_voice_runtime.py', self.pilot / 'runtime-install.json', self.pilot / self.profile['reference']):
            if path.is_file():
                digest.update(path.read_bytes())
        digest.update(str(self.pilot).encode())
        return digest.hexdigest()

    def available(self):
        if self.worker_command:
            return True
        required = [self.pilot / '.venv/bin/python', self.pilot / 'runtime-install.json',
                    self.pilot / self.profile['model_directory'] / 'model.safetensors',
                    self.pilot / self.profile['model_directory'] / 'speech_tokenizer/model.safetensors',
                    self.pilot / self.profile['reference']]
        return all(path.is_file() for path in required)

    def status(self):
        with self.lock:
            return {'available': self.available(), 'profile': self.profile['name'],
                    'version': self.profile['version'], 'local': True,
                    'warm': self.process is not None and self.process.poll() is None,
                    'ready': self.worker_ready,
                    'queued': sum(row['state'] == 'queued' for row in self.jobs.values()),
                    'active': bool(self.active), 'paid_fallback': False, 'engine': self.profile['engine'], 'preview': True}

    def _prune(self):
        now = time.monotonic()
        for key, row in list(self.jobs.items()):
            if row['state'] in TERMINAL and now - row['created'] > JOB_TTL_S:
                self.jobs.pop(key)
                self._drop_parts(key)
                self.requests.pop(row['request_id'], None)
        self.cancelled_requests = {key: value for key, value in self.cancelled_requests.items()
                                   if now - value < JOB_TTL_S}

    def _valid_cache(self, key):
        audio, metadata = self.cache / (key + '.wav'), self.cache / (key + '.json')
        try:
            with metadata.open() as handle:
                info = json.load(handle)
            if info.get('fingerprint') != self.fingerprint:
                return False
            with wave.open(str(audio), 'rb') as handle:
                if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getframerate() != 44100 or not handle.getnframes():
                    return False
            return hashlib.sha256(audio.read_bytes()).hexdigest() == info.get('sha256')
        except (OSError, ValueError, EOFError, wave.Error, TypeError, AttributeError):
            return False

    def submit(self, text, request_id, hint=None, cached_only=False):
        text = spoken_text(text)
        if not isinstance(request_id, str) or not REQUEST_PATTERN.fullmatch(request_id):
            raise ValueError('Invalid speech request.')
        plan = speaking_plan(text, hint)
        identity = json.dumps({'text': text, 'plan': plan, 'profile': self.fingerprint}, sort_keys=True)
        key = hashlib.sha256(identity.encode()).hexdigest()
        with self.lock:
            self._prune()
            if self.closed:
                raise ValueError('Voice is stopping.')
            if request_id in self.requests:
                row = self.jobs[self.requests[request_id]]
                if row['key'] != key:
                    raise ValueError('That speech request is already in use.')
                return self._snapshot(row)
            if len(self.jobs) >= MAX_JOBS:
                for old_id, old in list(self.jobs.items()):
                    if old['state'] in TERMINAL:
                        self.jobs.pop(old_id)
                        self.requests.pop(old['request_id'], None)
                        self._drop_parts(old_id)
                        break
                else:
                    raise ValueError('Voice is busy. Please try again shortly.')
            if not self.available():
                raise ValueError('The M1 voice preview is not available on this Mac.')
            if cached_only and not self._valid_cache(key):
                # Never a generation wait: schedule it for idle time and answer now.
                self._prepare(text, plan, key)
                return {'request_id': request_id, 'state': 'not_cached', 'cached': False, 'profile': self.profile['name']}
            if not cached_only:
                self.last_activity = time.monotonic()
                self.recent.append(time.monotonic())
            job_id = secrets.token_hex(16)
            row = {'id': job_id, 'request_id': request_id, 'key': key, 'text': text,
                   'delivery': plan, 'created': time.monotonic(), 'state': 'queued',
                   'cached': False, 'metrics': {}}
            if request_id in self.cancelled_requests:
                row['state'] = 'cancelled'
            elif self._valid_cache(key):
                row.update(state='ready', cached=True, timeline=self._cached_timeline(key), timeline_complete=True)
            elif self.queue.full():
                raise ValueError('Voice is busy. Please try again shortly.')
            self.jobs[job_id] = row
            self.requests[request_id] = job_id
            if row['state'] == 'queued':
                self.queue.put_nowait(job_id)
                active = self.jobs.get(self.active)
                if active and active.get('background'):
                    active['state'] = 'cancelled'
                    self._write({'op': 'cancel', 'id': active['id']})
                if self.thread is None or not self.thread.is_alive():
                    self.thread = threading.Thread(target=self._run, daemon=True, name='jarvis-voice')
                    self.thread.start()
            return self._snapshot(row)

    def _prepare(self, text, plan, key):
        if key in self.background or len(self.background) >= MAX_BACKGROUND:
            return
        self.background[key] = (text, plan)
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name='jarvis-voice')
            self.thread.start()
        try:
            self.queue.put_nowait(WAKE)          # re-plan the dispatcher's wait
        except queue.Full:
            pass

    def _key(self, text):
        plan = speaking_plan(text, None)
        return plan, hashlib.sha256(json.dumps({'text': text, 'plan': plan, 'profile': self.fingerprint}, sort_keys=True).encode()).hexdigest()

    def cached(self, text):
        """True if this phrase, with its default delivery, would play from cache at once."""
        try:
            _, key = self._key(spoken_text(text))
        except ValueError:
            return False
        with self.lock:
            return self._valid_cache(key)

    def prepare(self, texts):
        """Queue short phrases for idle-time rendering. Returns how many are already cached."""
        ready = 0
        for text in texts[:MAX_BACKGROUND]:
            text = spoken_text(text)
            plan, key = self._key(text)
            with self.lock:
                if self._valid_cache(key):
                    ready += 1
                elif self.available() and not self.closed:
                    self._prepare(text, plan, key)
        return ready

    def _background_job(self):
        key, (text, plan) = self.background.popitem(last=False)
        if self._valid_cache(key):
            return None
        job_id = secrets.token_hex(16)
        self.jobs[job_id] = {'id': job_id, 'request_id': 'background-' + job_id, 'key': key, 'text': text,
                             'delivery': plan, 'created': time.monotonic(), 'state': 'queued',
                             'cached': False, 'metrics': {}, 'background': True}
        return job_id

    def _cached_timeline(self, key):
        try:
            with (self.cache / (key + '.json')).open() as handle:
                return valid_timeline(json.load(handle).get('metrics', {}).get('timeline'))
        except (OSError, ValueError, AttributeError):
            return None

    def _drop_parts(self, job_id):
        shutil.rmtree(self.cache / 'stream' / job_id, ignore_errors=True)

    def warm(self, prepare=()):
        """Load the resident model ahead of a reply; the idle timer still unloads it."""
        if prepare:
            self.prepare(list(prepare))
        with self.lock:
            if self.closed or not self.available():
                return {'warm': False}
            self.last_activity = time.monotonic()
            if not (self.process is not None and self.process.poll() is None):
                self._start()
                self._write({'op': 'warm', 'id': secrets.token_hex(16)})
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, daemon=True, name='jarvis-voice')
                self.thread.start()
            try:
                self.queue.put_nowait(WAKE)
            except queue.Full:
                pass
            return {'warm': True}

    def part(self, job_id, index):
        with self.lock:
            row = self.jobs.get(job_id)
            if not row or row['state'] == 'cancelled' or not 0 <= index < row.get('parts', 0):
                return None
            path = self.cache / 'stream' / job_id / ('%d.wav' % index)
            return path if path.is_file() else None

    def _snapshot(self, row):
        result = {key: row[key] for key in ('id', 'request_id', 'state', 'cached')}
        result.update(status_url='/speech/jobs/' + row['id'], profile=self.profile['name'])
        if row.get('parts') and row['state'] != 'cancelled':
            # Ordered, gapless playback parts. `complete` marks the final count.
            result.update(parts=row['parts'], part_url='/speech/audio/' + row['id'] + '/',
                          complete=row['state'] == 'ready', estimated_seconds=row.get('estimated_seconds'))
        if row.get('timeline') and row['state'] != 'cancelled':
            # Audio-anchored reading progress: [[seconds, fraction of the text], ...].
            result['timeline'] = row['timeline']
            result['timeline_complete'] = bool(row.get('timeline_complete'))
        if row['state'] == 'ready':
            result['audio_url'] = '/speech/audio/' + row['id'] + '.wav'
            result['metrics'] = row['metrics']
        if row['state'] == 'failed':
            result['message'] = VOICE_ERROR
        return result

    def get(self, job_id):
        with self.lock:
            self._prune()
            row = self.jobs.get(job_id)
            return self._snapshot(row) if row else None

    def audio(self, job_id):
        with self.lock:
            row = self.jobs.get(job_id)
            if not row or row['state'] != 'ready' or not self._valid_cache(row['key']):
                return None
            return self.cache / (row['key'] + '.wav')

    def cancel(self, request_id):
        if not isinstance(request_id, str) or not REQUEST_PATTERN.fullmatch(request_id):
            raise ValueError('Invalid speech request.')
        with self.lock:
            self._prune()
            if len(self.cancelled_requests) >= MAX_JOBS:
                self.cancelled_requests.pop(next(iter(self.cancelled_requests)))
            self.cancelled_requests[request_id] = time.monotonic()
            job_id = self.requests.get(request_id)
            row = self.jobs.get(job_id)
            if row:
                row['state'] = 'cancelled'
                if self.active == job_id:
                    self._write({'op': 'cancel', 'id': job_id})
            self.lock.notify_all()
        return {'cancelled': True}

    def _write(self, value):
        with self.write_lock:
            process = self.process
            if process is None or process.poll() is not None:
                return False
            try:
                process.stdin.write(json.dumps(value) + '\n')
                process.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = self.worker_command or [str(self.pilot / '.venv/bin/python'), '-u',
            str(self.root / 'local_speech_worker.py'), '--profile', str(self.profile_path),
            '--pilot', str(self.pilot), '--cache', str(self.cache)]
        if not self.worker_command and sys.platform == 'darwin':
            command = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)(deny network*)', *command]
        environment = {key: os.environ[key] for key in WORKER_ENV if key in os.environ}
        environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
            HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1', PYTHONDONTWRITEBYTECODE='1')
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=self.root, env=environment,
            start_new_session=True)
        self.worker_ready = False
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()

    def _read(self, process):
        try:
            while True:
                line = process.stdout.readline(MAX_PACKET_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_PACKET_BYTES:
                    break
                try:
                    packet = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(packet, dict) or not isinstance(packet.get('id'), str):
                    continue
                with self.lock:
                    if self.process is not process:
                        return
                    if packet.get('state') == 'warm_ready':
                        self.worker_ready = True
                        self.lock.notify_all()
                        continue
                    row = self.jobs.get(packet.get('id'))
                    if not row or row['id'] != self.active:
                        continue
                    state = packet.get('state')
                    if row['state'] == 'cancelled':
                        if state in TERMINAL:
                            row['worker_done'] = True
                    elif state in ACTIVE_STATES:
                        row['state'] = state
                        if state == 'generating':
                            self.worker_ready = True
                        parts = packet.get('parts')
                        if state == 'streaming' and isinstance(parts, int) and row.get('parts', 0) < parts <= MAX_PARTS:
                            row['parts'] = parts
                            row['timeline'] = valid_timeline(packet.get('timeline')) or row.get('timeline')
                            estimate = packet.get('estimated_seconds')
                            if isinstance(estimate, (int, float)) and 0 < estimate < 3600:
                                row['estimated_seconds'] = float(estimate)
                    elif state in TERMINAL:
                        if state == 'ready' and not self._valid_cache(row['key']):
                            state = 'failed'
                        row['state'] = state
                        row['worker_done'] = True
                        if state == 'ready' and isinstance(packet.get('metrics'), dict):
                            row['metrics'] = packet['metrics']
                            row['timeline'] = valid_timeline(packet['metrics'].get('timeline')) or row.get('timeline')
                            row['timeline_complete'] = True
                    self.lock.notify_all()
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None
                    self.worker_ready = False
                    if self.active in self.jobs:
                        row = self.jobs[self.active]
                        if row['state'] != 'cancelled':
                            row['state'] = 'failed'
                        row['worker_done'] = True
                        self.lock.notify_all()
            self._dispose_process(process)

    def _run(self):
        idle_since = time.monotonic()
        while not self.closed:
            queued = True
            with self.lock:
                idle_since = max(idle_since, self.last_activity)
                idle = time.monotonic() - idle_since
                wait = self._idle_timeout() - idle
                warm = self.process is not None and self.process.poll() is None
                if self.background and warm:
                    wait = min(wait, BACKGROUND_DELAY_S - idle)
            try:
                job_id = self.queue.get(timeout=max(0.01, wait))
            except queue.Empty:
                with self.lock:
                    if not self.queue.empty():
                        continue
                    idle_since = max(idle_since, self.last_activity)
                    idle = time.monotonic() - idle_since
                    warm = self.process is not None and self.process.poll() is None
                    if self.background and warm and idle >= BACKGROUND_DELAY_S:
                        job_id, queued = self._background_job(), False
                        if job_id is None:
                            continue
                    elif idle >= self._idle_timeout():
                        self._stop_process()
                        # Submission starts a new dispatcher after this one exits.
                        self.thread = None
                        return
                    else:
                        continue
            if job_id == WAKE:
                self.queue.task_done()
                continue
            with self.lock:
                row = self.jobs.get(job_id)
                if not row or row['state'] == 'cancelled':
                    if queued:
                        self.queue.task_done()
                    continue
                self.active = job_id
                row['state'] = 'loading'
            try:
                with self.lock:
                    self._start()
                    if not self._write({'op': 'render', 'id': job_id, 'text': row['text'],
                            'delivery': row['delivery'], 'key': row['key'], 'fingerprint': self.fingerprint}):
                        raise OSError()
                    deadline = time.monotonic() + JOB_TIMEOUT_S
                    while not row.get('worker_done') and not self.closed:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise TimeoutError()
                        self.lock.wait(min(left, SHUTDOWN_WAIT_S))
            except (OSError, ValueError, TimeoutError):
                with self.lock:
                    if row['state'] != 'cancelled':
                        row['state'] = 'failed'
                    self._stop_process()
            finally:
                with self.lock:
                    self.active = None
                    if row['state'] != 'ready':
                        row['parts'] = 0
                        self._drop_parts(job_id)
                    if row.get('background'):
                        self.jobs.pop(job_id, None)
                        if row['state'] == 'cancelled' and not self.closed:
                            self.background.setdefault(row['key'], (row['text'], row['delivery']))
                    self.lock.notify_all()
                idle_since = time.monotonic()
                if queued:
                    self.queue.task_done()
                self._trim_cache()

    def _idle_timeout(self):
        # Reloading costs ~4 s; during a working session that cost lands on the
        # user's next reply, so the resident model outlives a short pause.
        now = time.monotonic()
        active = len(self.recent) == ACTIVE_REPLIES and now - self.recent[0] < ACTIVE_WINDOW_S
        return ACTIVE_IDLE_S if active else WORKER_IDLE_S

    def _trim_cache(self):
        try:
            files = sorted(self.cache.glob('*.wav'), key=lambda path: path.stat().st_mtime, reverse=True)
            total = 0
            for index, path in enumerate(files):
                total += path.stat().st_size
                if index >= MAX_CACHE_FILES or total > MAX_CACHE_BYTES:
                    path.unlink(missing_ok=True)
                    path.with_suffix('.json').unlink(missing_ok=True)
                    with self.lock:
                        for row in self.jobs.values():
                            if row['key'] == path.stem and row['state'] == 'ready':
                                row['state'] = 'failed'
        except OSError:
            pass

    def _stop_process(self):
        process, self.process = self.process, None
        self.worker_ready = False
        if process is None:
            return
        self._dispose_process(process)

    @staticmethod
    def _dispose_process(process):
        try:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except PermissionError:
                    # Some macOS execution contexts restrict group signalling.
                    # Fall back only to this owned Popen child, never a search
                    # for or signal to another process.
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=SHUTDOWN_WAIT_S)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except PermissionError:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait()
        except ProcessLookupError:
            pass
        finally:
            for stream in (process.stdin, process.stdout):
                if stream:
                    try: stream.close()
                    except (OSError, ValueError): pass

    def close(self):
        with self.lock:
            self.closed = True
            for row in self.jobs.values():
                if row['state'] not in TERMINAL:
                    row['state'] = 'cancelled'
            self._stop_process()
            self.lock.notify_all()
            try:
                self.queue.put_nowait(None)
            except queue.Full:
                pass

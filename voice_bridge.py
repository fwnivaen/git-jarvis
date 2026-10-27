"""Mac-only speech bridge. Audio stays in the native helper; Python uses stdlib."""
import atexit
import fcntl
import json
import math
import os
import secrets
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parent / '.local' / 'Jarvis Voice.app'
SESSION_TOKEN_BYTES = 24
SESSION_LEASE_S = 10
REAPER_INTERVAL_S = 2
STOP_WAIT_S = 3
AUDIO_LEASE_S = 15
AUDIO_RENDER_TIMEOUT_S = 15
AUDIO_TEXT_CHARS = 8000
AUDIO_MAX_BYTES = 32_000_000
AUDIO_MAX_SESSIONS = 2
AUDIO_TOKEN_BYTES = 24
AUDIO_SPECTRUM_BANDS = 24
AUDIO_RATE_HZ = 22050
AUDIO_PRIVATE_MODE = 0o600
AUDIO_DEVICE_ID_CHARS = 256
# LaunchAgents and desktop apps may receive different TMPDIR values.
VOICE_LOCK_PATH = Path('/tmp') / f'jarvis-voice-owner-{os.getuid()}.lock'
VOICE_LOCK_PROTOCOL = 'recording-owner-v1'
VOICE_LOCK_PROTOCOL_ARGUMENT = '--voice-lock-protocol'
LOCK = threading.RLock()
SESSION = None
AUDIO_LOCK = threading.RLock()
AUDIO = {}
AUDIO_SLOTS = threading.BoundedSemaphore(AUDIO_MAX_SESSIONS)


def acquire_voice_lock(path=VOICE_LOCK_PATH):
    """Probe shared ownership; the recording helper holds its own lifetime lock."""
    fd = None
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise OSError('Unsafe voice ownership file')
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        if fd is not None:
            os.close(fd)
        return None


def release_voice_lock(fd):
    if fd is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def helper_owns_voice_lock():
    """Do not launch an older helper that cannot enforce recording ownership."""
    executable = APP / 'Contents' / 'MacOS' / 'JarvisVoice'
    try:
        process = subprocess.run([str(executable), VOICE_LOCK_PROTOCOL_ARGUMENT],
                                 capture_output=True, text=True, timeout=STOP_WAIT_S)
        return process.returncode == 0 and process.stdout.strip() == VOICE_LOCK_PROTOCOL
    except (OSError, subprocess.TimeoutExpired):
        return False


def _expire_audio():
    now = time.monotonic()
    with AUDIO_LOCK:
        for token, state in list(AUDIO.items()):
            if state['expires'] <= now:
                state['scratch'].cleanup()
                del AUDIO[token]
                AUDIO_SLOTS.release()


def render_audio(text):
    """Render the existing Daniel voice locally; tokens expose one temporary WAV only."""
    if not isinstance(text, str) or not text.strip() or len(text) > AUDIO_TEXT_CHARS:
        return {'status': 'error', 'text': 'Speech text is empty or too long.'}
    _expire_audio()
    if not AUDIO_SLOTS.acquire(blocking=False):
        return {'status': 'error', 'text': 'Local speech playback is busy.'}
    scratch = None
    try:
        scratch = tempfile.TemporaryDirectory(prefix='jarvis-speech-')
        path = Path(scratch.name) / 'speech.wav'
        process = subprocess.run(['/usr/bin/say', '-v', 'Daniel', '-o', str(path),
                                  '--file-format=WAVE', f'--data-format=LEI16@{AUDIO_RATE_HZ}'],
                                 input=text, text=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=AUDIO_RENDER_TIMEOUT_S)
        if process.returncode or not path.is_file() or not 0 < path.stat().st_size <= AUDIO_MAX_BYTES:
            raise OSError
        path.chmod(AUDIO_PRIVATE_MODE)
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        if scratch:
            scratch.cleanup()
        AUDIO_SLOTS.release()
        return {'status': 'error', 'text': 'Local Daniel audio is unavailable; the browser voice remains available.'}
    token = secrets.token_hex(AUDIO_TOKEN_BYTES)
    with AUDIO_LOCK:
        AUDIO[token] = {'scratch': scratch, 'path': path, 'expires': time.monotonic() + AUDIO_LEASE_S}
    return {'status': 'ready', 'token': token, 'url': '/voice/audio?token=' + token,
            'voice': 'Daniel', 'spectrum': True}


def read_audio(token):
    """Return bytes for an unexpired opaque token, never an arbitrary filesystem path."""
    _expire_audio()
    if not isinstance(token, str):
        return None
    with AUDIO_LOCK:
        state = AUDIO.get(token)
        if state is None:
            return None
        try:
            return state['path'].read_bytes()
        except OSError:
            return None


def release_audio(token):
    with AUDIO_LOCK:
        state = AUDIO.pop(token, None) if isinstance(token, str) else None
        if state:
            state['scratch'].cleanup()
            AUDIO_SLOTS.release()
    return {'status': 'released'}


def audio_devices():
    executable = APP / 'Contents' / 'MacOS' / 'JarvisVoice'
    try:
        process = subprocess.run([str(executable), '--devices'], capture_output=True,
                                 text=True, timeout=STOP_WAIT_S)
        devices = json.loads(process.stdout) if process.returncode == 0 else []
        if not isinstance(devices, list):
            raise ValueError
        safe = [{'id': d['id'], 'name': d['name']} for d in devices
                if isinstance(d, dict) and isinstance(d.get('id'), str)
                and isinstance(d.get('name'), str)]
        return {'devices': safe, 'available': True, 'backend': 'native'}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'devices': [], 'available': False, 'backend': 'native',
                'reason': 'Microphone selection needs the updated Mac voice helper.'}


def stop():
    global SESSION
    with LOCK:
        if SESSION is None:
            return
        state = SESSION
        SESSION = None
        folder = Path(state['scratch'].name)
        (folder / 'stop').touch()
        try:
            state['process'].wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            # The recording helper owns the lock until its own exit, including
            # when it stops after this bridge's heartbeat disappears.
            pass
        state['scratch'].cleanup()


def handle(action, token=None, device_id=None):
    global SESSION
    with LOCK:
        if action == 'start':
            if device_id is not None and (not isinstance(device_id, str) or len(device_id) > AUDIO_DEVICE_ID_CHARS):
                return {'status': 'error', 'text': 'The selected microphone identifier is invalid.'}
            if not APP.is_dir():
                return {'status': 'error', 'text': 'The Mac voice helper needs to be built. Run native/build_voice.sh in the Jarvis folder.'}
            if not helper_owns_voice_lock():
                return {'status': 'error', 'text': 'The Mac voice helper needs a microphone ownership update. Run native/build_voice.sh in the Jarvis folder.'}
            if SESSION:
                return {'status': 'error', 'text': 'Another Jarvis tab is using the Mac microphone. Turn its Mic off first.'}
            voice_lock = acquire_voice_lock()
            if voice_lock is None:
                return {'status': 'error', 'text': 'Another Jarvis voice session is using the microphone.'}
            # This is only a fast availability check. The launched helper must
            # acquire ownership itself before requesting or opening audio, so
            # ownership survives this server exiting or being killed.
            release_voice_lock(voice_lock)
            try:
                scratch = tempfile.TemporaryDirectory(prefix='jarvis-voice-')
            except OSError:
                return {'status': 'error', 'text': 'The Mac voice helper could not prepare a private session.'}
            folder = Path(scratch.name)
            try:
                (folder / 'heartbeat').touch()
                arguments = ['/usr/bin/open', '-n', '-W', str(APP), '--args', '--session', str(folder)]
                if isinstance(device_id, str) and device_id:
                    arguments += ['--device', device_id]
                process = subprocess.Popen(arguments,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                scratch.cleanup()
                return {'status': 'error', 'text': 'The Mac voice helper could not start.'}
            token = secrets.token_hex(SESSION_TOKEN_BYTES)
            SESSION = dict(token=token, scratch=scratch, process=process, touched=time.monotonic())
            return {'token': token, 'status': 'starting'}
        if not SESSION or not isinstance(token, str) or not secrets.compare_digest(token, SESSION['token']):
            return {'status': 'error', 'text': 'The microphone session ended. Click Mic to retry.'}
        if action == 'stop':
            stop()
            return {'status': 'stopped'}
        SESSION['touched'] = time.monotonic()
        folder = Path(SESSION['scratch'].name)
        (folder / 'heartbeat').touch()
        try:
            state = json.loads((folder / 'state.json').read_text())
        except (OSError, ValueError):
            state = {'status': 'starting'}
        audio = state.get('audio')
        if isinstance(audio, dict):
            bands = audio.get('bands')
            if (isinstance(bands, list) and len(bands) == AUDIO_SPECTRUM_BANDS
                    and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in bands)
                    and type(audio.get('rms')) in (int, float) and math.isfinite(audio['rms'])
                    and 0 <= audio['rms'] <= 1
                    and type(audio.get('sample_rate')) in (int, float) and math.isfinite(audio['sample_rate'])
                    and type(audio.get('timestamp')) in (int, float) and math.isfinite(audio['timestamp'])):
                state['audio'] = {'bands': bands, 'rms': audio['rms'], 'source': 'native-microphone',
                                  'sample_rate': audio.get('sample_rate'), 'timestamp': audio.get('timestamp')}
            else:
                state.pop('audio', None)
        if SESSION['process'].poll() is not None and state['status'] != 'error':
            state = {'status': 'error', 'text': 'Mac voice stopped. Click Mic to retry.'}
        return state


def reap():
    while True:
        time.sleep(REAPER_INTERVAL_S)
        _expire_audio()
        with LOCK:
            if SESSION and time.monotonic() - SESSION['touched'] > SESSION_LEASE_S:
                stop()


threading.Thread(target=reap, daemon=True).start()
atexit.register(stop)
atexit.register(lambda: [release_audio(token) for token in list(AUDIO)])

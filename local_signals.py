"""Opt-in fixed macOS probes; no internet requests, identifiers or raw output."""
import os
import platform
import re
import selectors
import subprocess
import time
from datetime import datetime, timezone

MAX_OUTPUT_BYTES = 8192
PROBE_TIMEOUT_S = 1.5
COMMANDS = {'battery': ('/usr/bin/pmset', '-g', 'batt'),
    'network': ('/usr/sbin/scutil', '--nwi')}


def _run_probe(command):
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'})
        deadline, data = time.monotonic() + PROBE_TIMEOUT_S, bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if not selector.select(remaining):
                    raise TimeoutError
                chunk = os.read(process.stdout.fileno(), min(4096, MAX_OUTPUT_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_OUTPUT_BYTES:
                    raise ValueError('Probe output exceeded its bound')
        if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            raise OSError('Probe unavailable')
        return bytes(data)
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
            if process.stdout:
                process.stdout.close()


def _text(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT_BYTES:
        return ''
    return raw.decode('utf-8', errors='replace')


def parse_battery(raw):
    text = _text(raw)
    source = 'external' if "drawing from 'AC Power'" in text else 'battery' if "drawing from 'Battery Power'" in text else 'unknown'
    matches = re.findall(r'(?<!\d)(\d{1,3})%;', text)
    if source == 'unknown' or len(matches) != 1 or not 0 <= int(matches[0]) <= 100:
        return {'status': 'unknown', 'power_source': 'unknown', 'percent': None, 'charging': None}
    state = text.lower()
    charging = False if 'not charging' in state or 'discharging' in state else True if '; charging;' in state else False if '; charged;' in state else None
    return {'status': 'known', 'power_source': source, 'percent': int(matches[0]), 'charging': charging}


def parse_network(raw):
    text = _text(raw)
    if 'No network information' in text:
        return {'status': 'known', 'configured': False, 'internet_verified': False}
    if ('Network information' in text and re.search(r'\bReachable\b', text)
            and re.search(r'Network interfaces:\s*\S+', text)):
        return {'status': 'known', 'configured': True, 'internet_verified': False}
    return {'status': 'unknown', 'configured': None, 'internet_verified': False}


def collect(*, enabled=False, runner=None, system=None):
    if type(enabled) is not bool:
        raise ValueError('Local signal collection needs a boolean setting.')
    result = {'schema_version': 1, 'enabled': enabled, 'collected': False,
        'observed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'battery': parse_battery(b''), 'network': parse_network(b''),
        'privacy': {'network_requests': False, 'credentials_read': False,
            'interface_identifiers_returned': False, 'presence_inferred': False}}
    if not enabled:
        result['reason'] = 'disabled'
        return result
    if (platform.system() if system is None else system) != 'Darwin':
        result['reason'] = 'unsupported_platform'
        return result
    execute = _run_probe if runner is None else runner
    for name, command in COMMANDS.items():
        try:
            raw = execute(command)
            result[name] = parse_battery(raw) if name == 'battery' else parse_network(raw)
        except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
            pass
    result['collected'] = True
    result['reason'] = 'local_os_snapshot'
    return result

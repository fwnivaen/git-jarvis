"""Live Jarvis checks. Start server.py first; no mocks or replacement brain.

--quick skips model inference. Capture creation/cleanup is opt-in in every mode.
Only PASS/FAIL/WARN verdicts count; skipped chains are never passes.
"""
import argparse
import base64
import contextlib
import hashlib
import io
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path

import build
import server

ROOT = Path(__file__).resolve().parent
HTTP_TIMEOUT_S = server.SOCKET_TIMEOUT_S
TRANSPORT_GRACE_S = 15
CHAT_TIMEOUT_S = server.BRAIN_TIMEOUT_S + TRANSPORT_GRACE_S
SIGHT_TIMEOUT_S = server.SEE_TIMEOUT_S + server.CLI_CHECK_TIMEOUT_S + TRANSPORT_GRACE_S
LOCAL_TIMEOUT_S = server.CLI_CHECK_TIMEOUT_S
MAX_RESPONSE_BYTES = 20_000_000
SUMMARY_PRECISION = 2
EXPECTED_FINISH_MS = 900
# The complete isolated suite now takes about 44 seconds on the discovered M1.
OFFLINE_TIMEOUT_S = 180
OPENAI_MODELS_URL = 'https://api.openai.com/v1/models'
PLACEHOLDER_KEY = 'PUT-YOUR-KEY-HERE'
BRAIN_QUESTION = 'Say ready in one short sentence.'
CHAT_QUESTION = 'What are the main facts in the note titled {title}?'
SIGHT_QUESTION = 'What is the dominant color in this image? Be specific and brief.'
SIGHT_EXPECTED = re.compile(r'\b(red|crimson|scarlet)\b', re.IGNORECASE)
SECRET_PATHS = ('/config.json', '/config.json?preflight=1', '/%63onfig.json',
                '/viewer/../config.json', '/%2e%2e/config.json', '/viewer/config.json')
# A real, decodable 96x96 red JPEG. The question never supplies its expected color.
JPEG_BASE64 = (
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/'
    '2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/'
    'wAARCABgAGADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/'
    '8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/'
    '9oADAMBAAIRAxEAPwDzeiiivhz+qAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKAP//Z'
)


class CheckFailure(Exception):
    """Messages must contain no credentials, response bodies, or note content."""


class CheckWarning(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'img', 'source') and attrs.get('src'):
            self.urls.add(attrs['src'])
        if tag == 'link' and attrs.get('href'):
            self.urls.add(attrs['href'])


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def parse_graph(raw):
    match = re.fullmatch(rb'\s*const GRAPH = (.*);\s*', raw, re.S)
    require(match is not None, 'graph-data.js is not the expected graph script')
    try:
        graph = json.loads(match.group(1))
    except ValueError:
        raise CheckFailure('graph-data.js contains invalid JSON') from None
    require(isinstance(graph, dict) and isinstance(graph.get('nodes'), list)
            and isinstance(graph.get('links'), list), 'graph structure is invalid')
    return graph


def valid_answer(value, allowed=()):
    require(isinstance(value, dict), 'answer is not a JSON object')
    require(not value.get('error'), 'brain returned an error; check the active backend locally')
    require(isinstance(value.get('answer'), str) and bool(value['answer'].strip()), 'answer is empty')
    require(len(value['answer']) <= server.MAX_ANSWER_CHARS, 'answer exceeds the server limit')
    require(type(value.get('about_notes')) is bool, 'about_notes must be a boolean')
    require(isinstance(value.get('nodes'), list), 'answer has no nodes array')
    require(all(type(node) is int and node in allowed for node in value['nodes']), 'answer cites invalid node IDs')
    require(value['about_notes'] or not value['nodes'], 'non-note answer cites notes')
    return value


class Harness:
    def __init__(self, url, quick, *, allow_capture_cleanup=False):
        self.url = url.rstrip('/')
        self.quick = quick
        self.allow_capture_cleanup = allow_capture_cleanup
        self.counts = dict.fromkeys(('pass', 'fail', 'warn'), 0)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.page = None
        self.config = None
        self.config_error = False
        self.graph = None

    def run(self, label, function, brain=False):
        started = time.monotonic()
        print(f'… {label}', flush=True)
        state, detail = 'pass', ''
        try:
            if brain and self.quick:
                raise CheckWarning('skipped: --quick (real brain call)')
            detail = function() or 'OK'
        except CheckWarning as error:
            state, detail = 'warn', str(error)
        except CheckFailure as error:
            state, detail = 'fail', str(error)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            state, detail = 'fail', 'connection failed or timed out; check the running server/backend'
        except subprocess.TimeoutExpired:
            state, detail = 'fail', 'local command timed out'
        except Exception as error:
            # Do not stringify arbitrary exceptions: diagnostics can include secrets.
            state, detail = 'fail', f'{type(error).__name__}; inspect local setup (details withheld)'
        self.counts[state] += 1
        mark = {'pass': '✓', 'fail': '✗', 'warn': '⚠'}[state]
        elapsed = time.monotonic() - started
        print(f'{mark} {state.upper()} {label} ({elapsed:.{SUMMARY_PRECISION}f}s): {detail}', flush=True)

    def request(self, path, body=None, timeout=HTTP_TIMEOUT_S, method=None):
        headers = {'Cache-Control': 'no-cache', 'Origin': self.url}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(self.url + path, headers=headers, method=method,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            require(len(raw) <= MAX_RESPONSE_BYTES, 'HTTP response exceeds preflight limit')
            return response.status, response.headers, raw

    def json_request(self, path, body=None, timeout=HTTP_TIMEOUT_S):
        status, headers, raw = self.request(path, body, timeout)
        require(headers.get_content_type() == 'application/json', f'{path} did not return JSON (HTTP {status})')
        try:
            return status, json.loads(raw)
        except ValueError:
            raise CheckFailure(f'{path} returned invalid JSON') from None

    def settings(self):
        if self.config_error:
            raise CheckFailure('configuration could not be loaded; inspect it locally')
        if self.config is None:
            try:
                self.config = server.load_config()
            except server.BrainError:
                self.config_error = True
                raise CheckFailure('configuration could not be loaded; inspect it locally') from None
        backend = self.config.get('brain', 'codex-cli')
        require(backend in ('codex-cli', 'openai-api', 'openrouter'), 'unsupported brain backend in configuration')
        return self.config

    def notes_directory(self):
        """Read the selected scope without activating a checker-side Brain/index."""
        try:
            selected = server.BRAIN.settings()['vault_path']
        except (ValueError, OSError, KeyError):
            raise CheckFailure('local Brain selection is unavailable; no capture was written') from None
        status, _, raw = self.request('/brain')
        if status == 404:
            require(selected is None, 'running server cannot verify the selected Brain; no capture was written')
            return build.NOTES_DIR
        require(status == 200, 'running Brain status is unavailable; no capture was written')
        try:
            live = json.loads(raw)
        except (ValueError, TypeError):
            raise CheckFailure('running Brain status is invalid; no capture was written') from None
        require(isinstance(live, dict) and type(live.get('schema_version')) is int
                and live['schema_version'] == server.obsidian_brain.SCHEMA_VERSION
                and all(type(live.get(key)) is bool for key in ('configured', 'active', 'restart_required')),
                'running Brain status is invalid; no capture was written')
        require(live['restart_required'] is False,
                'Brain selection has a pending restart; no capture was written')
        if selected is None:
            require(live['configured'] is False and live['active'] is False and live.get('vault_name') is None,
                    'local and running Brain scopes differ; no capture was written')
            return build.NOTES_DIR
        folder = Path(selected)
        require(folder.is_absolute() and folder.is_dir() and not folder.is_symlink()
                and folder.resolve() == folder,
                'selected Brain folder is unavailable or changed; no capture was written')
        require(live['configured'] is True and live['active'] is True and live.get('vault_name') == folder.name,
                'local and running Brain scopes differ; no capture was written')
        # The public status deliberately omits the vault's absolute path. Verify
        # an existing source URI as well: two different folders can share a name.
        if self.graph is None:
            self.load_graph()
        source = next((node for node in self.graph['nodes'] if not node.get('capture_hub')
                       and type(node.get('id')) is int and isinstance(node.get('stable_id'), str)), None)
        require(source is not None, 'selected Brain has no verifiable live source; no capture was written')
        status, note = self.json_request('/note?id=' + str(source['id']))
        require(status == 200 and isinstance(note, dict),
                'live Brain source cannot be verified; no capture was written')
        uri = urllib.parse.urlsplit(note.get('obsidian_uri', '') if isinstance(note.get('obsidian_uri'), str) else '')
        paths = urllib.parse.parse_qs(uri.query).get('path', [])
        require(uri.scheme == 'obsidian' and uri.netloc == 'open' and len(paths) == 1,
                'live Brain source scope is unavailable; no capture was written')
        try:
            source_path = Path(paths[0])
            relative = source_path.relative_to(folder).as_posix()
            matches = (source_path.is_absolute() and source_path.resolve() == source_path
                       and folder in source_path.parents and source_path.is_file()
                       and server.note_store.stable_id(relative) == source['stable_id']
                       and note.get('stable_id') == source['stable_id'])
        except (OSError, ValueError):
            matches = False
        require(matches, 'local and running Brain folders differ; no capture was written')
        return folder

    def viewer(self):
        status, headers, raw = self.request('/')
        require(status == 200, f'viewer returned HTTP {status}')
        require(headers.get_content_type() == 'text/html', 'viewer is not HTML')
        require(b'graph-data.js' in raw and b'<html' in raw.lower(), 'response is not the Jarvis viewer')
        self.page = raw
        return 'running server serves the viewer'

    def load_graph(self):
        status, _, raw = self.request('/graph-data.js')
        require(status == 200, f'graph returned HTTP {status}')
        graph = parse_graph(raw)
        require(len(graph['nodes']) > 0, 'graph contains zero nodes')
        ids = [n.get('id') for n in graph['nodes']]
        require(all(type(node) is int for node in ids) and len(ids) == len(set(ids)), 'invalid or duplicate graph IDs')
        self.graph = graph
        return f"{len(graph['nodes'])} nodes loaded"

    def chat(self):
        if self.graph is None:
            self.load_graph()
        first = self.graph['nodes'][0]
        status, value = self.json_request('/chat', {'question': CHAT_QUESTION.format(title=first['label'])}, CHAT_TIMEOUT_S)
        require(status == 200, f'/chat returned HTTP {status}; no successful live answer')
        valid_answer(value, {n['id'] for n in self.graph['nodes']})
        require(value['about_notes'] and bool(value['nodes']), 'note question returned no supporting source')
        return 'real note question returned a valid answer and source nodes'

    def cli_login(self):
        executable = server.find_codex()
        require(executable is not None, 'Codex CLI was not found in PATH or its installed app location')
        result = subprocess.run([executable, 'login', 'status'], capture_output=True, text=True, timeout=LOCAL_TIMEOUT_S)
        require(result.returncode == 0, 'Codex is not logged in; run codex login locally')
        server.CODEX_USAGE.executable = executable
        try:
            server.CODEX_USAGE.require_included_usage(time.monotonic()+LOCAL_TIMEOUT_S)
        except server.ultra.BridgeError:
            raise CheckFailure('ChatGPT sign-in and included Codex allowance could not be verified; no paid fallback') from None
        return 'ChatGPT authentication and included Codex allowance verified; paid API providers disabled'

    def minimal_brain(self):
        config = self.settings()
        if config.get("brain") == "openrouter":
            server.refresh_router_catalog(config)
        # Call the exact boundary used by /chat; never substitute a fake model.
        with contextlib.redirect_stdout(io.StringIO()):
            value = server.ask_brain(server.SYSTEM_PROMPT, [], [], BRAIN_QUESTION)
        valid_answer(value)
        require(value['about_notes'] is False, 'minimal greeting was incorrectly classified as a note answer')
        return 'real ask_brain() returned valid JSON using the configured model'

    def key_check(self):
        config = self.settings()
        if config.get('brain', 'codex-cli') not in server.usage_policy.ALLOWED_BACKENDS:
            raise CheckFailure('Paid API brain blocked by the included-usage policy; select a Codex model')
        if config.get('brain', 'codex-cli') == 'codex-cli':
            raise CheckWarning('skipped: codex-cli brain active')
        if self.quick:
            raise CheckWarning('skipped: --quick (live API authentication call)')
        router = config.get('brain') == 'openrouter'
        key = config.get('openrouter_api_key' if router else 'openai_api_key')
        require(isinstance(key, str) and bool(key.strip()) and key != PLACEHOLDER_KEY,
                'OpenAI API key is missing or still the placeholder')
        request = urllib.request.Request(server.OPENROUTER_MODELS_URL if router else OPENAI_MODELS_URL, headers={'Authorization': 'Bearer ' + key})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=server.BRAIN_TIMEOUT_S) as response:
                value = json.loads(response.read(server.MAX_BRAIN_BYTES))
            require(isinstance(value, dict) and isinstance(value.get('data'), list), 'API key probe returned malformed data')
        except urllib.error.HTTPError as error:
            raise CheckFailure(f'OpenAI key probe rejected (HTTP {error.code}); inspect key permissions locally') from None
        return 'authenticated minimal GET /v1/models succeeded'

    def model_check(self):
        config = self.settings()
        if config.get('brain', 'codex-cli') == 'codex-cli':
            raise CheckWarning('skipped: codex-cli brain active')
        if self.quick:
            raise CheckWarning('skipped: --quick (real brain call)')
        return self.minimal_brain()

    def model_swaps(self):
        config = self.settings()
        try:
            status, before = self.json_request('/model')
            require(status == 200, 'model status unavailable; restart the server')
            status, value = self.json_request('/model', {'question': 'try on opus 5'})
            require(status == 400 and value.get('error') is True, 'fake version was not refused')
            require(value.get('brain') == before.get('brain'), 'refusal changed the active brain')
            status, value = self.json_request('/model', {'question': 'try on Claude Fable 5.1'})
            require(status == 400 and value.get('error') is True and value.get('answer') ==
                    server.usage_policy.ERROR_MESSAGES['subscription_required'], 'paid provider was not honestly refused')
            require(value.get('brain') == before.get('brain'), 'billing refusal changed the active brain')
            status, value = self.json_request('/model', {'question': 'switch to Luna'})
            require(status == 200 and value.get('brain', {}).get('backend') == 'codex-cli'
                    and value['brain']['model'] == 'gpt-6-luna', 'exact Codex swap failed')
            if not self.quick:
                status, answer = self.json_request('/chat', {'question': BRAIN_QUESTION}, CHAT_TIMEOUT_S)
                require(status == 200, 'swapped Codex model could not answer')
                valid_answer(answer)
        finally:
            status, value = self.json_request('/model', {'question': 'go back to your normal brain'})
            agent_status, agents = self.json_request('/agents')
            automatic = agent_status == 200 and agents.get('routing_enabled') is True
            expected_backend = 'codex-app-server' if automatic else config.get('brain', 'codex-cli')
            expected_model = 'ultra' if automatic else config['model']
            require(status == 200 and value.get('brain', {}).get('backend') == expected_backend
                    and value['brain']['model'] == expected_model, 'reset did not restore the normal route')
        if self.quick:
            raise CheckWarning('exact refusals, swap and reset passed; swapped inference skipped: --quick')
        return 'fake version/paid-provider refusals, real Codex swap and answer, reset to config passed'

    def capture(self):
        if not self.allow_capture_cleanup:
            raise CheckWarning('skipped: capture creation/cleanup requires --allow-capture-cleanup; notes preserved')
        notes_directory = self.notes_directory()
        # Refuse to write a probe to a stale server without guaranteed cleanup support.
        status, capability = self.json_request('/preflight/status')
        require(status == 200 and capability.get('protocol') == server.PREFLIGHT_PROTOCOL,
                'restart server.py: safe preflight cleanup is unavailable')
        token = uuid.uuid4().hex
        secret = uuid.uuid4().hex
        pattern = f'Preflight {token} *.md'
        folder = notes_directory / 'captures'
        question = f'remember that Preflight {token} verification code is {secret}.'
        try:
            status, value = self.json_request('/remember', {'question': question, 'preflight_token': token})
            require(status == 200 and not value.get('error'), f'/remember failed (HTTP {status})')
            files = list(folder.glob(pattern))
            require(len(files) == 1 and files[0].is_file() and not files[0].is_symlink(), 'capture was not written in the verified notes folder')
            require(secret in files[0].read_text(encoding='utf-8'), 'capture file does not contain the saved fact')
            self.load_graph()
            captured = value.get('captured')
            require(type(captured) is int and any(n['id'] == captured and token in n['label'] for n in self.graph['nodes']),
                    'capture is absent from the live index')
            status, _, raw = self.request('/graph-data.js')
            require(status == 200 and raw == build.GRAPH_PATH.read_bytes(), 'capture left the on-disk graph stale')
            if not self.quick:
                # No cookie/history and no secret in this question: only retrieval can supply it.
                status, recalled = self.json_request('/chat', {'question': f'What is the verification code for Preflight {token}?'}, CHAT_TIMEOUT_S)
                require(status == 200, f'capture recall failed (HTTP {status})')
                valid_answer(recalled, {n['id'] for n in self.graph['nodes']})
                require(secret in recalled['answer'] and captured in recalled['nodes'], 'immediate recall did not return the saved fact with its source')
        finally:
            status, cleaned = self.json_request('/preflight/cleanup', {'token': token})
            require(status == 200 and cleaned.get('cleaned') is True, 'CAPTURE CLEANUP FAILED; inspect the preflight capture locally')
            require(not list(folder.glob(pattern)), 'CAPTURE CLEANUP FAILED: test file remains')
            self.load_graph()
            require(not any(token in n.get('label', '') or token in n.get('excerpt', '') for n in self.graph['nodes']),
                    'CAPTURE CLEANUP FAILED: test note remains in live index')
            require(token.encode() not in build.GRAPH_PATH.read_bytes(), 'CAPTURE CLEANUP FAILED: test note remains in graph file')
        if self.quick:
            raise CheckWarning('real capture write/index/cleanup passed; /chat recall skipped: --quick')
        return 'real file saved, immediately recalled with source, then removed from disk and live index'

    def sight(self):
        backend = self.settings().get('brain', 'codex-cli')
        no_images = False
        if backend == 'codex-cli':
            executable = server.find_codex()
            if executable is not None:
                try:
                    result = subprocess.run([executable, 'exec', '--help'], capture_output=True, text=True, timeout=LOCAL_TIMEOUT_S)
                    no_images = result.returncode == 0 and '--image' not in result.stdout
                except (OSError, subprocess.TimeoutExpired):
                    pass  # Still exercise /see; an unknown capability cannot earn a WARN.
        # The envelope and MIME match canvas.toDataURL in the actual viewer.
        page = (server.VIEWER_DIR / 'index.html').read_text(encoding='utf-8')
        require("toDataURL('image/jpeg'" in page and "media_type:'image/jpeg'" in page, 'viewer JPEG contract changed; update the live probe')
        base64.b64decode(JPEG_BASE64, validate=True)
        status, value = self.json_request('/see', {'question': SIGHT_QUESTION, 'image': JPEG_BASE64,
                                                  'media_type': server.JPEG_MIME}, SIGHT_TIMEOUT_S)
        if no_images:
            require(status == 503 and value.get('error') is True and value.get('answer') == server.SIGHT_UNSUPPORTED
                    and value.get('nodes') == [], 'unsupported CLI did not return the explicit sight limitation')
            raise CheckWarning(server.SIGHT_UNSUPPORTED)
        require(status == 200, f'/see returned HTTP {status}; image-capable backend must answer')
        valid_answer(value)
        require(value['about_notes'] is False and bool(SIGHT_EXPECTED.search(value['answer'])), 'sight did not identify the real JPEG content')
        return 'real image/jpeg answered correctly (dominant color identified)'

    def served_files(self):
        status, _, page = self.request('/')
        require(status == 200, 'cannot fetch viewer for served-file comparison')
        parser = Assets()
        parser.feed(page.decode('utf-8'))
        paths = set(server.PUBLIC_FILES)
        for asset in parser.urls:
            resolved = urllib.parse.urlsplit(urllib.parse.urljoin(self.url + '/', asset))
            if resolved.netloc == urllib.parse.urlsplit(self.url).netloc:
                paths.add(resolved.path + (('?' + resolved.query) if resolved.query else ''))
        for path in sorted(paths):
            route = urllib.parse.urlsplit(path).path
            relative = server.PUBLIC_FILES.get(route, (route.lstrip('/'),))[0]
            disk = server.VIEWER_DIR / relative
            require(disk.is_file() and not disk.is_symlink() and server.VIEWER_DIR in disk.resolve().parents,
                    'served asset has no safe matching file on disk')
            status, headers, raw = self.request(path)
            require(status == 200, f'served file failed: {route} (HTTP {status})')
            require(hashlib.sha256(raw).digest() == hashlib.sha256(disk.read_bytes()).digest(),
                    f'STALE SERVED FILE: {route}; restart the correct checkout')
            require('no-store' in headers.get('Cache-Control', ''), f'{route} permits stale browser caching')
        # Detect a disk note edit missing from the running in-memory graph too.
        self.load_graph()
        actual = sorted((n['label'], n['group'], n['excerpt']) for n in self.graph['nodes'])
        expected = sorted((n['label'], n['group'], n['excerpt']) for n in build.read_notes(self.notes_directory()))
        require(actual == expected, 'live index differs from notes on disk; restart server.py')
        return f'{len(paths)} served routes match disk byte-for-byte; live note index matches disk'

    def private_config(self):
        for path in SECRET_PATHS:
            for method in ('GET', 'HEAD'):
                status, _, _ = self.request(path, method=method)
                require(status in (403, 404), f'SECURITY FAILURE: config.json is reachable or redirected (HTTP {status}); stop the server')
        return 'config.json and encoded/traversal aliases blocked for GET and HEAD'

    def ignored_config(self):
        for name in ('config.json', 'viewer/graph-data.js'):
            result = subprocess.run(['git', 'check-ignore', '-q', '--', name], cwd=ROOT, capture_output=True, timeout=LOCAL_TIMEOUT_S)
            require(result.returncode == 0, f'{name} is not ignored by git')
            tracked = subprocess.run(['git', 'ls-files', '--', name], cwd=ROOT, capture_output=True, timeout=LOCAL_TIMEOUT_S)
            require(tracked.returncode == 0 and not tracked.stdout, f'{name} is tracked by git')
        return 'config.json and generated graph are ignored and untracked'

    def finish_constant(self):
        page = (server.VIEWER_DIR / 'index.html').read_text(encoding='utf-8')
        scripts = '\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>', page, re.S | re.I))
        scripts = re.sub(r'/\*.*?\*/|//[^\n]*', '', scripts, flags=re.S)
        match = re.search(r'\bconst\s+FINISH_MS\s*=\s*(\d+(?:\.\d+)?)\s*;', scripts)
        require(match is not None, 'FINISH_MS must exist and be a numeric constant')
        require(re.search(r'setTimeout\([\s\S]*?,\s*FINISH_MS\s*\)', scripts) is not None,
                'FINISH_MS is not used by the finish timer')
        if float(match.group(1)) != EXPECTED_FINISH_MS:
            raise CheckWarning(f'FINISH_MS is {match.group(1)} ms; recommended default is {EXPECTED_FINISH_MS} ms')
        return 'numeric FINISH_MS is 900 ms and drives the finish timer'

    def offline(self):
        result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                                 '-p', 'test*.py'], cwd=ROOT, capture_output=True,
                                text=True, timeout=OFFLINE_TIMEOUT_S)
        require(result.returncode == 0, 'offline regression tests failed; run the README test command for details')
        return 'offline regression tests passed without network or real brain calls'

    def retired_features(self):
        for path in ('/focus', '/focus/diag', '/assistant', '/assistant.js', '/assistant.css'):
            require(self.request(path)[0] == 404, 'Removed feature is still served: ' + path)
        status, value = self.json_request('/system')
        require(status == 200 and value['capabilities']['notes'], 'Original Jarvis services unavailable')
        require(not any(value['capabilities'].get(key) for key in ('focus', 'shortcuts', 'dictation')), 'Removed feature still advertised')
        return 'Original Jarvis services available; Focus and Mac assistant removed'

    def execute(self):
        print('Jarvis preflight — ' + ('QUICK: live brain chains will be WARN' if self.quick else 'FULL LIVE RUN'), flush=True)
        self.run('Offline regressions', self.offline)
        self.run('Server and viewer', self.viewer)
        self.run('Removed features', self.retired_features)
        self.run('Graph data', self.load_graph)
        self.run('/model exact swaps and reset', self.model_swaps)
        self.run('/chat live answer', self.chat, brain=True)
        try:
            backend = self.settings().get('brain', 'codex-cli')
        except CheckFailure:
            backend = None
        if backend == 'codex-cli':
            self.run('Codex PATH and login', self.cli_login)
            self.run('Codex configured model / ask_brain()', self.minimal_brain, brain=True)
        self.run('OpenAI API key', self.key_check)
        self.run('OpenAI configured model', self.model_check)
        self.run('/remember → file → immediate /chat → cleanup', self.capture)
        self.run('/see real JPEG', self.sight, brain=True)
        self.run('Served files match disk', self.served_files)
        self.run('SECURITY: config.json not browser-accessible', self.private_config)
        self.run('Git ignores local configuration', self.ignored_config)
        self.run('Viewer FINISH_MS constant', self.finish_constant)
        print(f"{self.counts['pass']} pass, {self.counts['fail']} fail, {self.counts['warn']} warn", flush=True)
        return int(self.counts['fail'] > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='skip real brain calls')
    parser.add_argument('--allow-capture-cleanup', action='store_true',
                        help='allow creating and deleting one disposable verification note in the selected vault')
    parser.add_argument('--url', default=f'http://{server.HOST}:{server.PORT}', help='running local Jarvis URL')
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.url)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost') or not url.port
            or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment):
        parser.error('--url must be an http://127.0.0.1:PORT or http://localhost:PORT origin')
    return Harness(args.url.rstrip('/'), args.quick,
                   allow_capture_cleanup=args.allow_capture_cleanup).execute()


if __name__ == '__main__':
    sys.exit(main())

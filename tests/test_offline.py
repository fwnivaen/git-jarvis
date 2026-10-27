"""Offline regressions: temporary notes, no network, no credentials, no real brain."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import contextlib
import io
import json
import re
import subprocess
REAL_POPEN = subprocess.Popen
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build
import server

ROOT = Path(__file__).resolve().parents[1]
MIN_SAMPLE_NOTES = 30
MIN_GROUPS = 5
TEST_PORT = 4700
TEST_NOTE_ID = 2
TEST_TIMEOUT_S = 10
REQUEST_LIMIT_EXCESS = 1
TEST_CONFIG = {"brain": "codex-cli", "model": "gpt-6-astra"}


class FakeSocket:
    def __init__(self, data):
        self.input = io.BytesIO(data)
        self.output = io.BytesIO()

    def makefile(self, *_args):
        return self.input

    def sendall(self, data):
        self.output.write(data)

    def settimeout(self, _timeout):
        pass


def request(path, method="GET", body=None, headers=None):
    class TestServer:
        server_address = ("127.0.0.1", TEST_PORT)
    fields = {"Host": f"127.0.0.1:{TEST_PORT}"}
    data = b"" if body is None else json.dumps(body).encode()
    if body is not None:
        fields.update({"Content-Type": "application/json", "Content-Length": str(len(data))})
    fields.update(headers or {})
    raw = f"{method} {path} HTTP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
    sock = FakeSocket(raw.encode() + data)
    server.Handler(sock, ("127.0.0.1", TEST_PORT), TestServer())
    response_headers, response_body = sock.output.getvalue().split(b"\r\n\r\n", 1)
    return int(response_headers.split()[1]), response_headers.decode(), response_body


class Preflight(unittest.TestCase):
    def test_chat_knows_real_eyes_and_only_receives_boolean_status(self):
        with patch.object(server, 'ask_brain', return_value={'answer':'Eyes exist.', 'nodes':[], 'about_notes':False}) as brain:
            status, _, _ = request('/chat', 'POST', {'question':'Are your eyes merely a prop?'})
            self.assertEqual(status, 200)
            system=brain.call_args.args[0]
            self.assertIn('working Eyes organ, not a prop', system)
            self.assertIn('Current local posture status (no image)', system)
            self.assertEqual(set(json.loads(system.split('Current local posture status (no image): ')[1])), {'eyes_active','present','head_down','slouched'})
            self.assertNotIn('image', brain.call_args.kwargs)

    def test_webcam_route_is_one_image_with_separate_prompt(self):
        import base64
        body = dict(question='look at me', media_type='image/jpeg', image=base64.b64encode(b'\xff\xd8\xfftest\xff\xd9').decode())
        with patch.object(server, 'ask_brain', return_value={'answer': 'A shirt, sir.', 'nodes': []}) as brain:
            status, _, raw = request('/look', 'POST', body)
            self.assertEqual(status, 200)
            self.assertEqual(brain.call_count, 1)
            self.assertEqual(brain.call_args.args[0], server.LOOK_PROMPT)
            self.assertTrue(brain.call_args.kwargs['webcam'])
            self.assertEqual(brain.call_args.kwargs['image'], b'\xff\xd8\xfftest\xff\xd9')
        with patch.object(server, 'active_settings', side_effect=AssertionError('webcam must pin Astra')), patch.object(server, 'find_codex', return_value=None):
            self.assertTrue(server.ask_brain('system', [], [], 'look', image=b'jpeg', webcam=True)['error'])

    def setUp(self):
        config = patch.object(server, 'load_config', return_value=dict(TEST_CONFIG))
        config.start()
        self.addCleanup(config.stop)
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        root = Path(self.scratch.name)
        notes = root / 'notes'
        notes.mkdir()
        for i in range(MIN_SAMPLE_NOTES):
            group = notes / f'Group{i % MIN_GROUPS}'
            group.mkdir(exist_ok=True)
            (group / f'Example {i}.md').write_text('Example 0 [[Shared Topic]]')
        (notes / 'Menu Pricing.md').write_text('Menu Pricing: bowls cost ten pounds.')
        (notes / 'Cash Flow Forecast.md').write_text('Cash Flow Forecast: the owner updates it.')
        for target, name, value in ((build, 'NOTES_DIR', notes), (build, 'GRAPH_PATH', root / 'graph.js'),
                                    (server, 'NOTES', []), (server, 'SESSIONS', {}),
                                    (server, 'PREFLIGHT_CAPTURES', {}), (server, 'PREFLIGHT_CLOSED', {})):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        writer = build.write_graph
        patcher = patch.object(build, 'write_graph', side_effect=lambda graph: writer(graph, root / 'graph.js'))
        patcher.start()
        self.addCleanup(patcher.stop)
        server.NOTES = build.read_notes(notes)
        self.graph = build.make_graph(server.NOTES)
        # Unexpected network or real model invocation must fail the test.
        for target in ('server.urllib.request.urlopen', 'server.subprocess.Popen'):
            patcher = patch(target, side_effect=(lambda *args, **kwargs: REAL_POPEN(*args, **kwargs) if args and args[0][0] == 'git' else (_ for _ in ()).throw(AssertionError('real network/brain forbidden'))))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_retired_focus_endpoints(self):
        for path in ('/focus', '/focus/diag', '/focus.js', '/focus-v2.css', '/focus-probe.js'):
            self.assertEqual(request(path)[0], 404)
        self.assertEqual(request('/focus', 'POST', {'action': 'start'})[0], 404)

    def test_model_labels(self):
        self.assertEqual(server.model_label('gpt-6-astra'), 'GPT-6 Astra')
        self.assertEqual(server.model_label('fable-5-1'), 'Fable 5.1')
        self.assertEqual(server.model_label('anthropic/claude-fable-5.1'), 'Claude Fable 5.1')

    def test_model_exact_refusal_key_and_reset(self):
        config = {'brain': 'codex-cli', 'model': 'gpt-6-astra', 'openrouter_api_key': server.PLACEHOLDER_KEY}
        with patch.object(server, 'load_config', return_value=config), patch.object(server, 'ACTIVE_BRAIN', None), patch.object(server, 'ask_brain', return_value={'error': True}):
            for name in ('opus 5', 'opus 4.1', 'astra 99', 'fable 5.2', 'claude fable 5.1 please use opus', 'gpt 6 luna 9'):
                status, _, raw = request('/model', 'POST', {'question': 'try on ' + name})
                self.assertEqual(status, 400, name)
                self.assertTrue(json.loads(raw)['error'])
                self.assertIsNone(server.ACTIVE_BRAIN)
            result = server.swap_model('try on Claude Fable 5.1')
            self.assertTrue(result['error'])
            self.assertEqual(result['answer'], server.usage_policy.ERROR_MESSAGES['subscription_required'])
            self.assertFalse(server.swap_model('switch to Luna')['error'])
            self.assertEqual(server.active_settings()['model'], 'gpt-6-luna')
            self.assertEqual(server.load_config(), config)
            status, _, raw = request('/model', 'POST', {'question': 'go back to your normal brain'})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['brain']['model'], config['model'])
            self.assertEqual(server.active_settings()['brain'], config['brain'])
            self.assertIsNone(server.ACTIVE_BRAIN)

    def test_curated_swap_paths_rotate_and_pin_aliases(self):
        config = {'brain': 'codex-cli', 'model': 'gpt-6-astra'}
        with patch.object(server, 'load_config', return_value=config), patch.object(server, 'ACTIVE_BRAIN', None), patch.object(server, 'INTRO_POSITIONS', {}), patch.object(server, 'ask_brain', side_effect=AssertionError('curated intro called model')):
            button = json.loads(request('/model', 'POST', {'question': 'astra'})[2])
            voice = json.loads(request('/chat', 'POST', {'question': 'switch to GPT-6 Astra'})[2])
            tag = server.apply_model_control({'answer': '[[SWITCH_MODEL: gpt-6]]'})
            lines = [button['answer'], voice['answer'], tag['answer']]
            lines += [server.swap_model('astra')['answer'] for _ in range(3)]
            self.assertEqual(len(set(lines)), 6)
            self.assertEqual(server.swap_model('astra')['answer'], lines[0])
            for alias in ('astra', 'gpt-6 astra', 'gpt 6 astra', 'gpt-6'):
                self.assertEqual(server.resolve_model(alias), ('codex-cli', 'gpt-6-astra'))
            self.assertIsNone(server.resolve_model('gpt-6-astra-mini'))
        with patch.object(server, 'load_config', return_value=config), patch.object(server, 'CURATED_LINES', []), patch.object(server, 'ask_brain', return_value={'answer': '   '}):
            self.assertIn('GPT-6 Astra', server.brain_introduction())
        with patch.object(server, 'load_config', return_value=config), patch.object(server, 'CURATED_LINES', []), patch.object(server, 'ask_brain', return_value={'answer': 'At your service, sir.'}):
            self.assertEqual(server.brain_introduction(), 'At your service, sir.')

    def test_model_commands_route_without_inference(self):
        config = {'brain': 'codex-cli', 'model': 'gpt-6-astra', 'openrouter_api_key': server.PLACEHOLDER_KEY}
        with patch.object(server, 'load_config', return_value=config), patch.object(server, 'ACTIVE_BRAIN', None), patch.object(server, 'ask_brain', return_value={'error': True}):
            for command in ('Luna', 'switch to Luna', 'Please switch to Luna', 'Jarvis, change your model to Luna'):
                status, _, raw = request('/chat', 'POST', {'question': command})
                self.assertEqual(status, 200, command)
                self.assertEqual(json.loads(raw)['brain']['model'], 'gpt-6-luna')
            self.assertFalse(server.is_model_command('What does Luna mean?'))
            self.assertFalse(server.is_model_command('Use my notes to explain staffing'))
            self.assertTrue(server.is_model_command('opus 5'))
            status, _, raw = request('/chat', 'POST', {'question': 'opus 5'})
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(raw)['brain']['model'], 'gpt-6-luna')
            catalog = json.loads(request('/model')[2])
            self.assertEqual(len(catalog['choices']), len(set(server.SPOKEN_MODELS.values())))
            for choice in catalog['choices']:
                self.assertEqual(choice['available'], choice['backend'] == 'codex-cli')
                result = server.swap_model('switch to ' + choice['name'])
                self.assertEqual(result['error'], not choice['available'])
            self.assertNotIn('openrouter_api_key', json.dumps(catalog))

    def test_router_catalog_fails_closed_and_drops_unknown(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'data': [
            {'id': 'openai/gpt-6-astra', 'architecture': {'input_modalities': ['text', 'image']}},
            {'id': 'invented/model'}]}).encode()
        config = {'brain': 'codex-cli', 'model': 'gpt-6-astra', 'openrouter_api_key': 'test-only'}
        # Preserve legacy catalog/schema assertions under an explicit test-only
        # policy override. Separate usage-policy tests enforce the shipped lock.
        with patch.object(server.usage_policy, 'ALLOWED_BACKENDS', {'codex-cli','openrouter'}), patch.object(server, 'OPENROUTER_AVAILABLE', frozenset()), patch.object(server, 'OPENROUTER_IMAGES', frozenset()), patch.object(server, 'ACTIVE_BRAIN', None), patch.object(server, 'load_config', return_value=config):
            with patch.object(server.urllib.request, 'urlopen', return_value=response):
                server.refresh_router_catalog(config)
            self.assertEqual(server.OPENROUTER_AVAILABLE, {'openai/gpt-6-astra'})
            self.assertEqual(server.OPENROUTER_IMAGES, {'openai/gpt-6-astra'})
            self.assertTrue(server.swap_model('try on fable 5.1')['error'])
            with patch.object(server.urllib.request, 'urlopen', side_effect=OSError), contextlib.redirect_stdout(io.StringIO()):
                server.refresh_router_catalog(config)
            self.assertFalse(server.OPENROUTER_AVAILABLE)

    def test_openrouter_text_image_and_no_substitute(self):
        from unittest.mock import MagicMock
        model = 'openai/gpt-6-astra'
        config = {'brain': 'openrouter', 'model': model, 'openrouter_api_key': 'test-only'}
        response = MagicMock()
        value = {'model': model, 'choices': [{'message': {'content': json.dumps({'answer':'Ready.', 'nodes':[], 'about_notes':False})}}]}
        response.__enter__.return_value.read.return_value = json.dumps(value).encode()
        with patch.object(server.usage_policy, 'ALLOWED_BACKENDS', {'codex-cli','openrouter'}), patch.object(server, 'load_config', return_value=config), patch.object(server, 'ACTIVE_BRAIN', None), patch.object(server, 'OPENROUTER_AVAILABLE', {model}), patch.object(server, 'OPENROUTER_IMAGES', {model}), patch.object(server.urllib.request, 'urlopen', return_value=response) as send, contextlib.redirect_stdout(io.StringIO()):
            for image in (None, b'jpeg'):
                self.assertFalse(server.ask_brain('system', [], [], 'question', image=image).get('error', False))
                body = json.loads(send.call_args.args[0].data)
                self.assertEqual(body['model'], model)
                self.assertNotIn('models', body)
                self.assertFalse(body['provider']['allow_fallbacks'])
                if image:
                    self.assertEqual(body['messages'][1]['content'][1]['type'], 'image_url')
            value['model'] = 'wrong-model'
            response.__enter__.return_value.read.return_value = json.dumps(value).encode()
            self.assertTrue(server.ask_brain('system', [], [], 'question')['error'])
            with patch.object(server, 'OPENROUTER_IMAGES', set()):
                send.reset_mock()
                self.assertTrue(server.ask_brain('system', [], [], 'question', image=b'jpeg')['error'])
                send.assert_not_called()

    def test_graph_indices_and_edges(self):
        nodes, links = self.graph["nodes"], self.graph["links"]
        self.assertGreaterEqual(len(nodes), MIN_SAMPLE_NOTES)
        self.assertGreaterEqual(len({n["group"] for n in nodes}), MIN_GROUPS)
        self.assertEqual([n["id"] for n in nodes], list(range(len(nodes))))
        self.assertTrue(all(len(n["excerpt"]) <= build.EXCERPT_CHARS for n in nodes))
        self.assertTrue(links)
        for link in links:
            self.assertTrue(0 <= link["source"] < link["target"] < len(nodes))
        self.assertEqual(len(links), len({(l["source"], l["target"]) for l in links}))

    def test_title_mentions_shared_wikilinks_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "notes"
            root.mkdir()
            (root / "Alpha.md").write_text("Beta is useful. [[Shared Topic|alias]]")
            (root / "Beta.md").write_text("Independent text")
            (root / "Gamma.md").write_text("[[Shared Topic#Heading]]")
            outside = Path(directory) / "outside.md"
            outside.write_text("Must not be indexed")
            (root / "Link.md").symlink_to(outside)
            graph = build.make_graph(build.read_notes(root))
            self.assertEqual([n["label"] for n in graph["nodes"]], ["Alpha", "Beta", "Gamma"])
            self.assertEqual(graph["links"], [{"source": 0, "target": 1}, {"source": 0, "target": 2}])

    def test_title_weighting_and_followup(self):
        selection = server.retrieve("What does Menu Pricing say?", [])
        self.assertEqual(selection[0]["title"], "Menu Pricing")
        self.assertLessEqual(len(selection), server.TOP_NOTES)
        followup = server.retrieve("Who updates it?", [{"question": "Explain Cash Flow Forecast", "answer": ""}])
        self.assertIn("Cash Flow Forecast", [n["title"] for n in followup])
        self.assertEqual(server.retrieve("quasar nucleosynthesis", []), [])

    def test_answer_validation(self):
        notes = [{"id": TEST_NOTE_ID}]
        self.assertEqual(server.validate_answer('{"answer":"A supported answer.","nodes":[2],"about_notes":true}', notes)["nodes"], [TEST_NOTE_ID])
        invalid = ('plain prose', '{"answer":"","nodes":[],"about_notes":false}', '{"answer":"x","nodes":[999],"about_notes":true}',
                   '{"answer":"x","nodes":[true],"about_notes":true}', '{"answer":"x","nodes":[],"extra":1}')
        for raw in invalid:
            with self.assertRaises(server.BrainError):
                server.validate_answer(raw, notes)

    def test_public_routes_and_secrets_boundary(self):
        self.assertEqual(request("/")[0], 200)
        self.assertEqual(request("/graph-data.js")[0], 200)
        for path in ("/config.json", "/../config.json", "/%2e%2e/config.json", "/%63onfig.json",
                     "/.git/config", "/server.py", "/AGENTS.md", "/notes/", "/viewer/", "/anything.json"):
            self.assertEqual(request(path)[0], 404, path)
        self.assertEqual(request("/", headers={"Host": "attacker.example"})[0], 403)
        status, headers, body = request("/", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertIn("X-Content-Type-Options: nosniff", headers)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "index.html").symlink_to(ROOT / "README.md")
            with patch.object(server, "VIEWER_DIR", Path(directory)):
                self.assertEqual(request("/")[0], 404)

    def test_chat_input_and_origin_validation(self):
        for body in ({}, {"question": ""}, {"question": []}, [], {"question": "x" * (server.MAX_QUESTION_CHARS + REQUEST_LIMIT_EXCESS)}):
            self.assertEqual(request("/chat", "POST", body)[0], 400)
        self.assertEqual(request("/chat", "POST", {"question": "test"}, {"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(request("/chat", "POST", {"question": "test"}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(request("/chat", "POST", {"question": "test"}, {"Content-Length": str(server.MAX_REQUEST_BYTES + REQUEST_LIMIT_EXCESS)})[0], 400)

    def test_session_history_isolation_and_brain_errors(self):
        server.SESSIONS.clear()
        histories = []
        def fake_brain(_system, notes, history, _question):
            histories.append(history)
            return {"answer": "The notes cover this. Here is the summary.", "nodes": [notes[0]["id"]], "about_notes": True}
        with patch.object(server, "ask_brain", side_effect=fake_brain):
            status, headers, _ = request("/chat", "POST", {"question": "Menu Pricing"})
            self.assertEqual(status, 200)
            cookie = re.search(r"Set-Cookie: ([^;]+)", headers).group(1)
            request("/chat", "POST", {"question": "What about it?"}, {"Cookie": cookie})
            request("/chat", "POST", {"question": "Cash Flow Forecast"})
        self.assertEqual(len(histories[0]), 0)
        self.assertEqual(len(histories[1]), 1)
        self.assertEqual(len(histories[2]), 0)
        with patch.object(server, "ask_brain", return_value={"answer": "Unavailable.", "nodes": [], "error": True}):
            self.assertEqual(request("/chat", "POST", {"question": "Menu Pricing"})[0], 503)

    def test_codex_arguments_are_restricted(self):
        args = server.cli_command("codex", "gpt-6-astra", "/tmp/empty", "/tmp/schema.json", "/tmp/answer.txt")
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn("--ignore-user-config", args)
        self.assertIn("--strict-config", args)
        self.assertIn("--ephemeral", args)
        self.assertIn('web_search="disabled"', args)
        for feature in ("shell_tool", "apps", "plugins", "browser_use", "computer_use", "multi_agent"):
            self.assertIn(feature, args)
        self.assertEqual(args[-1], "-")

    def test_missing_cli_and_timeout_are_safe(self):
        safe_config = {"brain": "codex-cli", "model": "gpt-6-astra"}
        with patch.object(server, "load_config", return_value=safe_config), patch.object(server, "find_codex", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            result = server.ask_brain("system", [], [], "test")
            self.assertTrue(result["error"])
            self.assertEqual(result["nodes"], [])
        class TimedOut:
            pid = 123
            returncode = None
            def __init__(self, *_args, **_kwargs):
                self.first = True
            def communicate(self, *_args, **_kwargs):
                if self.first:
                    self.first = False
                    raise subprocess.TimeoutExpired("codex", server.BRAIN_TIMEOUT_S)
                return None, ""
        with patch.object(server.CODEX_USAGE, 'require_included_usage'), patch.object(server, "load_config", return_value=safe_config), patch.object(server, "find_codex", return_value="codex"), patch.object(server.subprocess, "Popen", TimedOut), patch.object(server.os, "killpg"), contextlib.redirect_stdout(io.StringIO()):
            result = server.ask_brain("system", [], [], "test")
            self.assertTrue(result["error"])
            self.assertIn("too long", result["answer"])

    def test_ignored_files_are_untracked(self):
        for name in ("config.json", "viewer/graph-data.js"):
            result = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT, timeout=TEST_TIMEOUT_S)
            self.assertEqual(result.returncode, 0)
            tracked = subprocess.run(["git", "ls-files", "--error-unmatch", name], cwd=ROOT, capture_output=True, timeout=TEST_TIMEOUT_S)
            self.assertNotEqual(tracked.returncode, 0)

    def test_shared_persona_and_no_forced_sir(self):
        self.assertTrue(server.SYSTEM_PROMPT.startswith(server.PERSONA_PROMPT))
        self.assertTrue(server.SEE_PROMPT.startswith(server.PERSONA_PROMPT))
        answer = server.validate_answer(json.dumps(dict(answer='Ready.', nodes=[], about_notes=False)), [])
        self.assertEqual(answer['answer'], 'Ready.')

    def test_capture_hub_persists_and_is_single(self):
        with patch.object(server, 'ask_brain', side_effect=AssertionError('capture called brain')):
            first = server.remember('remember that zxqvunique')
            second = server.remember('log this ytrwunique')
        hubs = [n for n in server.NOTES if n['label'] == build.CAPTURE_HUB_TITLE]
        self.assertEqual(len(hubs), 1)
        self.assertEqual(sum(bool(n.get('capture_hub')) for n in second['graph']['nodes']), 1)
        for result in (first, second):
            self.assertTrue(any({edge['source'], edge['target']} == {hubs[0]['id'], result['captured']}
                                for edge in second['graph']['links']))
        rebuilt = build.read_notes(build.NOTES_DIR)
        self.assertEqual(sum(n['label'] == build.CAPTURE_HUB_TITLE for n in rebuilt), 1)
        self.assertTrue(server.retrieve('zxqvunique', []))

    def test_probe_cleans_capture_and_unused_hub(self):
        token = 'a' * 32
        before = list(server.NOTES)
        server.remember(f'remember that Preflight {token} verification code is zxqw.', token)
        server.cleanup_preflight(token)
        self.assertEqual(server.NOTES, before)
        self.assertFalse(list((build.NOTES_DIR / 'captures').glob('*.md')))

    def test_missing_notes_is_clear(self):
        with patch.object(build, 'NOTES_DIR', Path(self.scratch.name) / 'missing'):
            with self.assertRaisesRegex(server.BrainError, 'notes folder is missing'):
                server.retrieve('question', [])
            with self.assertRaisesRegex(FileNotFoundError, 'notes folder is missing'):
                build.read_notes(build.NOTES_DIR)

    def test_native_voice_requires_same_origin(self):
        import voice_bridge
        with patch.object(voice_bridge, 'handle', return_value={'status': 'starting'}) as bridge:
            self.assertEqual(request('/voice/start', 'POST', {})[0], 403)
            self.assertEqual(request('/voice/start', 'POST', {}, {'Origin': 'http://attacker.example'})[0], 403)
            bridge.assert_not_called()
            self.assertEqual(request('/voice/start', 'POST', {}, {'Origin': f'http://127.0.0.1:{TEST_PORT}'})[0], 200)
            bridge.assert_called_once_with('start', None)

    def test_native_voice_token_and_cleanup(self):
        import voice_bridge
        from unittest.mock import MagicMock
        process = MagicMock()
        process.poll.return_value = None
        with patch.object(voice_bridge, 'SESSION', None), patch.object(voice_bridge, 'APP', Path(self.scratch.name)), patch.object(voice_bridge, 'helper_owns_voice_lock', return_value=True), patch.object(voice_bridge.subprocess, 'Popen', return_value=process):
            try:
                state = voice_bridge.handle('start')
                folder = Path(voice_bridge.SESSION['scratch'].name)
                self.assertTrue(folder.exists())
                self.assertEqual(voice_bridge.handle('poll', 'wrong')['status'], 'error')
                self.assertEqual(voice_bridge.handle('start')['status'], 'error')
                self.assertEqual(voice_bridge.handle('stop', state['token'])['status'], 'stopped')
                self.assertFalse(folder.exists())
                self.assertIsNone(voice_bridge.SESSION)
            finally:
                voice_bridge.stop()

    def test_finish_window_numeric_and_tunable(self):
        import preflight
        with patch.object(Path, 'read_text', return_value='<script>const FINISH_MS = 750; setTimeout(fn, FINISH_MS)</script>'):
            with self.assertRaises(preflight.CheckWarning):
                preflight.Harness('http://127.0.0.1:4700', True).finish_constant()
        with patch.object(Path, 'read_text', return_value='<script>const FINISH_MS = "900";</script>'):
            with self.assertRaises(preflight.CheckFailure):
                preflight.Harness('http://127.0.0.1:4700', True).finish_constant()


if __name__ == '__main__':
    unittest.main()

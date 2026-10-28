"""Local durable workflow service. Graph scheduling belongs to LangGraph.

Only bounded, read-only analysis tools are offered here. Existing chat retains
its original tools. No hosted tracing, arbitrary graph/config uploads or pickle.
"""
import json
import hashlib
import os
import queue
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from codex_bridge import BridgeError
import usage_policy
import adaptive_routing as routing
from ultra_tools import ToolSession
from ultra import CHECKED_SCHEMA, REPORT_SCHEMA, STAGE_ONE_BOUNDARY, TOOLS_BOUNDARY, checked, checked_report, decode
from ultra_workflows import workflow_template
from workflow_graph import compile_workflow, GRAPH_RECURSION_LIMIT
from task_limits import TASK_MAX_CONCURRENT, TASK_LIMIT_NOTICE, parse_task_limit
import workflow_retention as retention

ROOT = Path(__file__).resolve().parent / '.local' / 'graph-workflows'
MAX_RUNS = 50
MAX_DB_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 256
MAX_QUESTION_CHARS = 2000
MAX_TEXT_CHARS = 8000
MAX_TASK_CHARS = 900
MAX_WORKERS = 3
MAX_RESTARTS = 2
MAX_TOOL_CALLS = 24
SQL_TIMEOUT_S = 5
TOOL_POLL_S = 0.1
BRIDGE_CLEANUP_RETRY_INITIAL_S = 0.5
BRIDGE_CLEANUP_RETRY_MAX_S = 30
BRIDGE_CLEANUP_RETRY_BACKOFF = 2
STREAM_WAIT_S = 1
STREAM_CONNECTION_S = 20
MAX_STREAMS = 4
SECONDS_DIGITS = 3
ID_BYTES = 12
READ_TOOLS = frozenset({'notes_search', 'note_read', 'preferences_get'})
LEGACY_TOOL_BOUNDARY = 'Never use tools, execute commands, inspect files, edit files, or browse the network.'
ANALYSIS_TOOL_BOUNDARY = 'Only registered read-only Jarvis tools may be used. No shell, arbitrary files, writes or browsing.'
REVIEW_BOUNDARY = STAGE_ONE_BOUNDARY.replace(
    'Stage 1 is read-only: no tools, file changes, sends, screen actions or test execution are available.',
    'This final review cannot call tools. Earlier workflow phases have already executed the confirmed_tool_calls in the payload.').replace(
    'Never claim these actions were performed.',
    'You may accurately report those confirmed operations. Do not claim unconfirmed actions.')
TERMINAL = frozenset({'complete', 'failed', 'cancelled', 'interrupted'})
RETRYABLE = frozenset({'invalid_output', 'missing_sources', 'math_check', 'disconnected', 'unavailable', 'protocol_failure'})
SAFE_FAILURES = RETRYABLE | {'timeout', 'cancelled', 'budget_exhausted', 'tool_failed', 'inference_failed',
    'model_unavailable', 'effort_unavailable', 'model_mismatch', 'blocked_action', 'storage_unavailable', 'source_changed',
    'model_budget_exhausted', 'context_budget_exhausted', 'invalid_routing_profile'} | usage_policy.ERROR_MESSAGES.keys()
WORKFLOW_LABELS = {'general': 'Focused answer', 'research': 'Evidence research', 'document': 'Document analysis', 'code': 'Code review'}
WORKER_COUNTS = {'general': 1, 'research': 3, 'document': 2, 'code': 2}
EVENT_KINDS = frozenset({'started', 'resumed', 'node_complete', 'agent_started', 'agent_complete',
    'agent_failed', 'tool_started', 'tool_complete', 'tool_failed', 'usage', 'complete', 'failed',
    'cancel_requested', 'cancelled', 'interrupted'})
RUN_ID = re.compile(r'[a-f0-9]{24}\Z')
SECRET_PATTERNS = (
    re.compile(r'(?i)\b(?:sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9_]{8,}|github_pat_[a-z0-9_]{8,})'),
    re.compile(r'(?i)\b(?:api[_ -]?key|authorization|password|secret|access[_ -]?token)\s*[:=]\s*["\']?[^\s,;"\']+'),
    re.compile(r'(?i)\bbearer\s+[a-z0-9._~+/-]+=*'),
    re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----'),
)


class WorkflowError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


class Redactor:
    def __init__(self, values=()):
        self.values = tuple(value for value in values if isinstance(value, str) and len(value) >= 8)

    def text(self, value, limit=MAX_TEXT_CHARS):
        text = str(value)
        for value in self.values:
            text = text.replace(value, '[redacted]')
        for pattern in SECRET_PATTERNS:
            text = pattern.sub('[redacted]', text)
        return text[:limit]


class RunStore:
    """Small private catalog/event journal; graph state uses SqliteSaver separately."""
    def __init__(self, root=ROOT):
        self.root = Path(root)
        if self.root.is_symlink():
            raise OSError('Unsafe workflow directory')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.lock = threading.RLock()
        self.path = self.root / 'catalog.sqlite'
        self.conn, self.catalog_identity = self.connect(self.path, with_identity=True)
        self.root_identity = retention.identities(self.root)
        with self.conn:
            self.conn.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            self.conn.execute('CREATE TABLE IF NOT EXISTS admissions (request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, run_id TEXT NOT NULL)')
            self.conn.execute('CREATE TABLE IF NOT EXISTS erasures (id TEXT PRIMARY KEY, created_at REAL NOT NULL, data TEXT NOT NULL)')

    @staticmethod
    def connect(path, with_identity=False):
        if path.is_symlink():
            raise OSError('Unsafe database path')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.fchmod(fd, 0o600)
        initial = os.fstat(fd)
        os.close(fd)
        conn = sqlite3.connect(path, timeout=SQL_TIMEOUT_S, check_same_thread=False)
        try:
            current = os.stat(path, follow_symlinks=False)
            if (not retention._owned(current) or (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino)):
                raise OSError('Database path changed while opening')
            # DELETE mode avoids world-readable WAL sidecars; parent is private too.
            conn.execute('PRAGMA journal_mode=DELETE')
        except BaseException:
            conn.close()
            raise
        return (conn, (initial.st_dev, initial.st_ino)) if with_identity else conn

    def get(self, identifier):
        if not isinstance(identifier, str) or not RUN_ID.fullmatch(identifier):
            raise WorkflowError('Unknown workflow run.', 404)
        with self.lock:
            if self.erasure(identifier) is not None:
                raise WorkflowError('This task was removed and cannot be restarted.', 410)
            row = self.conn.execute('SELECT data FROM runs WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise WorkflowError('Unknown workflow run.', 404)
            return json.loads(row[0])

    def save(self, data):
        with self.lock, self.conn:
            if self.erasure(data['id']) is not None:
                raise WorkflowError('This task was removed and cannot be restarted.', 410)
            self.conn.execute('INSERT OR REPLACE INTO runs VALUES (?, ?)',
                (data['id'], json.dumps(data, allow_nan=False)))

    def admitted(self, request_key, fingerprint):
        if request_key is None:
            return None
        if not isinstance(request_key, str) or not RUN_ID.fullmatch(request_key):
            raise WorkflowError('Invalid workflow request key.')
        with self.lock:
            row = self.conn.execute('SELECT fingerprint,run_id FROM admissions WHERE request_key=?', (request_key,)).fetchone()
            if row is None:
                return None
            if self.erasure(row[1]) is not None:
                raise WorkflowError('This task was removed and cannot be restarted.', 410)
            if row[0] != fingerprint:
                raise WorkflowError('That request key belongs to a different task.', 409)
            return self.get(row[1])

    def admit(self, data, request_key, fingerprint):
        with self.lock, self.conn:
            if self.erasure(data['id']) is not None:
                raise WorkflowError('This task was removed and cannot be restarted.', 410)
            self.conn.execute('INSERT INTO runs VALUES (?,?)', (data['id'], json.dumps(data, allow_nan=False)))
            if request_key is not None:
                self.conn.execute('INSERT INTO admissions VALUES (?,?,?)', (request_key, fingerprint, data['id']))

    def all(self):
        with self.lock:
            return sorted([json.loads(row[0]) for row in self.conn.execute(
                'SELECT data FROM runs r WHERE NOT EXISTS (SELECT 1 FROM erasures e WHERE e.id=r.id)')],
                key=lambda row: row['created_at'], reverse=True)

    def erasure(self, identifier):
        with self.lock:
            row = self.conn.execute('SELECT data FROM erasures WHERE id=?', (identifier,)).fetchone()
            return json.loads(row[0]) if row else None

    def erasures(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.conn.execute(
                "SELECT data FROM erasures ORDER BY json_extract(data,'$.state')='pending' DESC, created_at DESC LIMIT ?", (retention.MAX_CATALOG_ITEMS,))]

    def begin_erasure(self, identifier, revision, files, catalog_bytes):
        with self.lock, self.conn:
            marker = {'id': identifier, 'state': 'pending', 'revision': revision,
                      'created_at': time.time(), 'manifest': files, 'catalog_bytes': catalog_bytes}
            self.conn.execute('INSERT INTO erasures VALUES (?,?,?)',
                (identifier, marker['created_at'], json.dumps(marker, allow_nan=False)))
            return marker

    def finish_erasure(self, marker):
        with self.lock, self.conn:
            # The receipt contains no question, answer, source or artifact hashes.
            marker = {key: marker[key] for key in ('id', 'revision', 'created_at')}
            marker.update(state='erased', erased_at=time.time())
            self.conn.execute('DELETE FROM runs WHERE id=?', (marker['id'],))
            self.conn.execute('UPDATE erasures SET data=? WHERE id=?',
                              (json.dumps(marker, allow_nan=False), marker['id']))
            return marker

    def capacity(self):
        with self.lock:
            # Pending removals still retain their content and consume capacity.
            count = self.conn.execute('SELECT count(*) FROM runs').fetchone()[0]
            return count < MAX_RUNS and sum(p.stat().st_size for p in self.root.glob('*.sqlite')) < MAX_DB_BYTES

    def close(self):
        self.conn.close()


class GraphJobs:
    """Adapt JARVIS's real model, validators and registered tools to graph nodes."""
    def __init__(self, service, run_id, bridge, cancel):
        self.service, self.run_id, self.bridge, self.cancel = service, run_id, bridge, cancel
        self.settings = service.settings()
        self.budget = routing.ModelBudget(self.settings.model_call_limit, self.settings.observed_token_limit,
            service.store.get(run_id).get('routing_budget', [])) if self.settings.adaptive_routing_enabled else None
        self.model_cancel = routing.CancellationSignal(cancel, self.budget.cancel) if self.budget is not None else cancel
        self.session = ToolSession(service.tools, run_id, root=service.root / "tool-journals",
            permissions=READ_TOOLS, cancel=cancel)

    def guard(self):
        if self.budget is not None and self.budget.cancel.is_set():
            raise BridgeError('model_budget_exhausted')
        if self.cancel.is_set():
            raise BridgeError('cancelled')
        run = self.service.store.get(self.run_id)
        if time.time() >= run['expires_at']:
            raise BridgeError('budget_exhausted')
        return run

    def save_budget(self):
        if self.budget is not None:
            # Serialize the snapshot and write with attempt admission; an older
            # observer must never overwrite a newer worker reservation on disk.
            with self.service.lock:
                self.service.change(self.run_id, routing_budget=self.budget.snapshot()['calls'])

    def retrieve(self, state):
        self.guard()
        self.service.phase(self.run_id, 'retrieve')
        # Real registered tool execution, visible even when a model needs no tools.
        notes = self.tool('notes_search', {'query': state['question']}, 'retriever')['notes']
        return {'notes': notes}

    def assign(self, state):
        self.guard()
        self.service.phase(self.run_id, 'assign')
        recipe = workflow_template(workflow=state['workflow'])
        count = min(WORKER_COUNTS[state['workflow']], self.settings.max_workers, MAX_WORKERS)
        tasks = [role['name'] + ': ' + role['assignment'] for role in recipe['roles'][:count]]
        self.service.change(self.run_id, tasks=tasks)
        return {'tasks': tasks}

    def tool(self, name, arguments, slot, deadline=None):
        run = self.guard()
        deadline = min(deadline or float('inf'), time.monotonic() + max(0, run['expires_at'] - time.time()))
        if name not in READ_TOOLS:
            raise BridgeError('blocked_action')
        with self.service.lock:
            run = self.service.store.get(self.run_id)
            if run['tool_calls'] >= MAX_TOOL_CALLS:
                raise BridgeError('budget_exhausted')
            run['tool_calls'] += 1
            self.service.store.save(run)
        self.service.event(self.run_id, 'tool_started', slot=slot, tool=name)
        delivered = queue.Queue(maxsize=1)
        # Two admitted tasks share the original tool budget. Contention waits
        # within this run's deadline instead of turning normal load into failure.
        while not self.service.tool_slots.acquire(timeout=TOOL_POLL_S):
            self.guard()
            if time.monotonic() >= deadline:
                raise BridgeError('timeout')
        try:
            self.guard()
            if time.monotonic() >= deadline:
                raise BridgeError('timeout')
        except Exception:
            self.service.tool_slots.release()
            raise
        with self.service.lock:
            self.service.pending_tools[self.run_id] = self.service.pending_tools.get(self.run_id, 0) + 1
        def execute():
            try:
                delivered.put((True, self.session.call(name, arguments, deadline)))
            except Exception:
                delivered.put((False, None))
            finally:
                self.service.tool_slots.release()
                with self.service.lock:
                    self.service.pending_tools[self.run_id] -= 1
                    if not self.service.pending_tools[self.run_id]:
                        try:
                            saved = self.service.store.get(self.run_id)
                            if saved['status'] in TERMINAL:
                                owner = self.service.owners.get(self.run_id, {})
                                self.service.change(self.run_id,
                                    cleanup='pending' if owner.get('bridge_cleanup_pending') else 'complete')
                                self.service.release_owner(self.run_id)
                        except (sqlite3.Error, WorkflowError):
                            pass
        threading.Thread(target=execute, daemon=True, name='jarvis-read-tool').start()
        try:
            while True:
                self.guard()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeError('timeout')
                try:
                    success, result = delivered.get(timeout=min(remaining, TOOL_POLL_S))
                    break
                except queue.Empty:
                    continue
            if not success:
                raise BridgeError('tool_failed')
            self.guard()
        except Exception as error:
            self.service.event(self.run_id, 'tool_failed', slot=slot, tool=name)
            raise BridgeError(getattr(error, 'code', 'tool_failed')) from None
        self.service.event(self.run_id, 'tool_complete', slot=slot, tool=name)
        return result

    def invoke(self, slot, assignment, payload, schema, validator, model, tools=False, effort=None):
        while True:
            run = self.guard()
            with self.service.lock:
                run = self.service.store.get(self.run_id)
                row = run['agents'].get(slot, {'slot': slot, 'task': assignment, 'attempts': 0,
                    'started_at': time.time(), 'expires_at': min(run['expires_at'], time.time() +
                    (self.settings.request_timeout_s if slot == 'reviewer' else self.settings.worker_timeout_s)),
                    'tokens': 0, 'usage_reported': False, 'model': model,
                    'effort': (effort or (routing.STRONG_EFFORT if model == self.settings.brain_model else routing.QUICK_EFFORT))
                        if self.budget is not None else self.settings.effort})
                if row['attempts'] > self.settings.worker_retries or time.time() >= row['expires_at']:
                    raise BridgeError('budget_exhausted')
                row.update(attempts=row['attempts'] + 1, status='running', error=None)
                if self.budget is not None:
                    try:
                        reservation = self.budget.reserve(row['model'], row['effort'], slot)
                    except routing.RoutingBudgetError as error:
                        raise BridgeError(error.code) from None
                    run['routing_budget'] = self.budget.snapshot()['calls']
                row.pop('ended_at', None)
                run['agents'][slot] = row
                self.service.store.save(run)  # Reserve attempt BEFORE external inference.
            self.service.event(self.run_id, 'agent_started', slot=slot)
            deadline = time.monotonic() + max(0, min(row['expires_at'], run['expires_at']) - time.time())
            usage = {}
            def observer(values):
                usage.update(values)
                if self.budget is not None:
                    try:
                        self.budget.observe(reservation, values)
                    except routing.RoutingBudgetError as error:
                        raise BridgeError(error.code) from None
                    finally:
                        self.save_budget()
                self.service.agent(self.run_id, slot, current_tokens=usage.get('totalTokens'))
                self.service.event(self.run_id, 'usage', slot=slot)
            options = {}
            sources = list(payload['notes'])
            confirmed_tools = []
            if tools:
                specs = [item for item in self.service.tools.dynamic_specs() if item['name'] in READ_TOOLS]
                def handle(name, arguments, tool_deadline):
                    result = self.tool(name, arguments, slot, tool_deadline)
                    confirmed_tools.append(name)
                    discovered = result.get('notes', []) if isinstance(result, dict) else []
                    if isinstance(result, dict) and 'id' in result and 'text' in result:
                        discovered = discovered + [result]
                    for note in discovered:
                        if type(note.get('id')) is int and isinstance(note.get('text'), str):
                            prior = next((n for n in sources if n['id'] == note['id']), None)
                            if prior is None:
                                sources.append(note)
                            else:
                                if prior.get('revision') != note.get('revision'):
                                    raise BridgeError('source_changed')
                                sources[sources.index(prior)] = dict(prior, **note)
                    return result
                options.update(tools=specs, tool_handler=handle)
            try:
                call_payload = dict(payload, assignment=assignment)
                if self.budget is not None:
                    try:
                        call_payload = routing.compact_payload(call_payload)
                    except routing.RoutingBudgetError as error:
                        raise BridgeError(error.code) from None
                while not self.service.model_slots.acquire(timeout=TOOL_POLL_S):
                    self.guard()
                    if time.monotonic() >= deadline:
                        raise BridgeError('timeout')
                try:
                    self.guard()
                    if time.monotonic() >= deadline:
                        raise BridgeError('timeout')
                    raw = self.bridge.complete(model=row['model'], effort=row['effort'],
                        system=self.service.system.replace(LEGACY_TOOL_BOUNDARY, ANALYSIS_TOOL_BOUNDARY)
                            + (TOOLS_BOUNDARY if tools else REVIEW_BOUNDARY)
                            + ' Perform the assigned task. Return concise supported findings, never private reasoning.',
                        payload=call_payload, schema=schema,
                        deadline=deadline, observer=observer, cancel_event=self.model_cancel, **options)
                finally:
                    self.service.model_slots.release()
                self.guard()
                decoded = decode(raw)
                validator(decoded, sources)
                self.service.agent(self.run_id, slot, status='complete', output=decoded['answer'], ended_at=time.time())
                self.service.event(self.run_id, 'agent_complete', slot=slot)
                return dict(decoded, _sources=sources, _tools=confirmed_tools)
            except Exception as error:
                code = getattr(error, 'code', 'inference_failed')
                if self.budget is not None and self.budget.cancel.is_set():
                    code = 'model_budget_exhausted'
                code = code if code in SAFE_FAILURES else 'inference_failed'
                self.service.agent(self.run_id, slot, status='cancelled' if self.cancel.is_set() else 'failed',
                    error=code, ended_at=time.time())
                self.service.event(self.run_id, 'agent_failed', slot=slot, error=code)
                if code not in RETRYABLE or row['attempts'] > self.settings.worker_retries:
                    raise BridgeError(code) from None
                if self.budget is not None and code in ('invalid_output', 'missing_sources', 'math_check'):
                    with self.service.lock:
                        current = self.service.store.get(self.run_id)
                        if (row['model'] == self.settings.quick_model and
                                current.get('routing_escalations', 0) < self.settings.max_escalations):
                            current['routing_escalations'] = current.get('routing_escalations', 0) + 1
                            current['agents'][slot].update(model=self.settings.brain_model, effort=routing.STRONG_EFFORT)
                            self.service.store.save(current)
            finally:
                self.service.add_usage(self.run_id, slot, usage)

    def worker(self, state):
        self.guard()
        self.service.phase(self.run_id, 'workers')
        slot = 'worker-' + str(state['index'] + 1)
        route = routing.decide(state['question']) if self.budget is not None else None
        strong = route is not None and route.mode == 'strong' and len(self.guard()['tasks']) == 1
        report = self.invoke(slot, state['task'], {'question': state['question'], 'notes': state['notes']},
            REPORT_SCHEMA, checked_report, self.settings.brain_model if strong else self.settings.quick_model,
            tools=True, effort=route.effort if strong else None)
        return {'reports': {slot: report}}

    def review(self, state):
        self.guard()
        self.service.phase(self.run_id, 'review')
        sources = {note['id']: note for note in state['notes']}
        reports = []
        confirmed_tools = []
        for slot, report in sorted(state['reports'].items()):
            sources.update({note['id']: note for note in report['_sources']})
            reports.append({key: value for key, value in report.items() if not key.startswith('_')})
            confirmed_tools.extend({'agent': slot, 'tool': name} for name in report.get('_tools', []))
        single = reports[0] if len(reports) == 1 else None
        if (self.budget is not None and single and not single.get('unresolved_issues')
                and state.get('workflow') == 'general'):
            # One checked finding needs no second model to restate it.
            checked({key: single[key] for key in CHECKED_SCHEMA['required']}, list(sources.values()))
            result = single
        else:
            route = routing.decide(state['question']) if self.budget is not None else None
            deep_review = route is not None and (any(report.get('unresolved_issues') for report in reports)
                or route.effort == routing.DEEP_EFFORT and route.mode == 'strong')
            result = self.invoke('reviewer', 'Check the findings against the sources and arithmetic; answer the original request. '
                'Disclose unresolved issues. Do not repeat completed tools.',
                {'question': state['question'], 'notes': list(sources.values()), 'worker_results': reports,
                    'confirmed_tool_calls': confirmed_tools},
                CHECKED_SCHEMA, checked, self.settings.brain_model,
                effort=routing.DEEP_EFFORT if deep_review else None)
        final = {key: result[key] for key in CHECKED_SCHEMA['required']}
        final['source_refs'] = [{'stable_id': sources[node]['stable_id'], 'revision': sources[node]['revision'],
            'original_id': node} for node in final['nodes'] if sources[node].get('stable_id') and sources[node].get('revision')]
        return {'result': final}


class WorkflowService:
    def __init__(self, settings, tools, bridge_factory, slots, system, root=ROOT, redactor=None,
                 max_concurrent=None, model_slots=None):
        self.settings, self.tools, self.bridge_factory, self.slots, self.system = settings, tools, bridge_factory, slots, system
        self.root, self.redactor = Path(root), redactor or Redactor()
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.stream_slots = threading.BoundedSemaphore(MAX_STREAMS)
        self.tool_slots = threading.BoundedSemaphore(MAX_WORKERS)
        self.model_slots = model_slots if model_slots is not None else threading.BoundedSemaphore(MAX_WORKERS)
        self.max_concurrent = TASK_MAX_CONCURRENT if max_concurrent is None else parse_task_limit(max_concurrent)
        self.limit_notice = TASK_LIMIT_NOTICE if max_concurrent is None else None
        self.pending_tools = {}
        self.finished_executors = set()
        self.store = None
        self._deferred_recovery = False
        self.bridge = None
        self.owners = {}
        self._cleanup_stop = threading.Event()
        self._cleanup_threads = set()
        # Keep the most recently launched thread for existing diagnostic callers.
        # Lifecycle and cancellation always use the per-run owner below.
        self.thread = None
        self.unavailable = None
        self.source_resolver = None

    @property
    def active_ids(self):
        with self.lock:
            return list(self.owners)

    @property
    def active(self):
        """Legacy single-ID view for existing clients; new clients use active_ids."""
        with self.lock:
            return next(iter(self.owners), None)

    def settled(self, run_id):
        with self.lock:
            return run_id not in self.owners

    def initialize(self):
        with self.lock:
            if self.store is not None and not self._deferred_recovery:
                return
            try:
                from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: F401
                if self.store is None:
                    self.store = RunStore(self.root)
                for run in self.store.all():
                    if run['status'] in ('running', 'cancelling'):
                        run.update(status='interrupted', error='disconnected')
                        for row in run['agents'].values():
                            if row['status'] == 'running':
                                row.update(status='interrupted', error='disconnected', ended_at=time.time())
                        self.store.save(run)
                        self.event(run['id'], 'interrupted')
                self.unavailable = None
                self._deferred_recovery = False
            except ImportError:
                self.unavailable = 'Install the workflow dependencies with python3 tools/setup_ultra.py --no-model.'
            except (OSError, sqlite3.Error):
                self.unavailable = 'The private workflow store is unavailable.'

    def enabled(self):
        settings = self.settings()
        return settings.enabled and settings.workflows_enabled and settings.tools_enabled

    def change(self, run_id, **values):
        with self.lock:
            run = self.store.get(run_id)
            run.update(values)
            self.store.save(run)
            self.condition.notify_all()

    def release_owner(self, run_id):
        # Cancellation fences delivery promptly, but the shared admission slot
        # belongs to the run until BOTH graph and underlying callbacks settle.
        with self.lock:
            if (run_id in self.owners and run_id in self.finished_executors
                    and not self.owners[run_id].get('bridge_cleanup_pending')
                    and not self.pending_tools.get(run_id, 0)):
                del self.owners[run_id]
                self.finished_executors.discard(run_id)
                self.pending_tools.pop(run_id, None)
                self.slots.release()
                self.condition.notify_all()

    def _retention_snapshot(self, identifier=None):
        if self.store is None:
            return retention.read_catalog(self.root, identifier)
        retention.validate_paths(self.root, self.store.root_identity, self.store.catalog_identity)
        marks = ([self.store.erasure(identifier)] if identifier else self.store.erasures())
        marks = [row for row in marks if row is not None]
        runs = self.store.all()
        if identifier:
            runs = [run for run in runs if run['id'] == identifier]
        return True, runs, marks

    def _erase_eligibility(self, run_id, run=None):
        if run_id in self.owners or self.pending_tools.get(run_id, 0) or run_id in self.finished_executors:
            return False, 'Wait until this task and all of its cleanup have finished.'
        if run is not None and (run.get('status') not in TERMINAL or run.get('cleanup') != 'complete'):
            return False, 'Only a finished task with confirmed complete cleanup can be removed.'
        return True, None

    def retention_catalog(self):
        try:
            with self.lock:
                available, runs, marks = self._retention_snapshot()
                items = []
                for run in runs:
                    eligible, reason = self._erase_eligibility(run['id'], run)
                    items.append({'id': run['id'], 'label': self.redactor.text(run['question'], retention.LABEL_CHARS),
                        'status': run['status'], 'cleanup': run.get('cleanup', 'none'),
                        'settled': run['id'] not in self.owners, 'eligible': eligible,
                        'reason': reason, 'state': 'retained', 'created_at': run['created_at']})
                for mark in marks:
                    items.append({'id': mark['id'], 'label': 'Removed task', 'status': mark['state'],
                        'cleanup': 'complete' if mark['state'] == 'erased' else 'pending',
                        'settled': mark['id'] not in self.owners, 'eligible': mark['state'] == 'pending',
                        'state': mark['state'], 'created_at': mark['created_at'],
                        'erased_at': mark.get('erased_at')})
                return {'schema_version': retention.SCHEMA_VERSION, 'available': available,
                    'items': sorted(items, key=lambda row: (row['state'] == 'erased', -row['created_at']))[:retention.MAX_CATALOG_ITEMS],
                    'limits': dict(retention.LIMITS), 'exclusions': list(retention.EXCLUSIONS)}
        except (retention.RetentionError, OSError, sqlite3.Error, ValueError, KeyError) as error:
            raise WorkflowError('The private workflow retention catalog is unavailable.',
                                getattr(error, 'status', 503)) from None

    def _erase_plan(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise WorkflowError('Unknown workflow run.', 404)
        _, runs, marks = self._retention_snapshot(run_id)
        base = {'schema_version': retention.SCHEMA_VERSION, 'id': run_id,
            'effects': list(retention.EFFECTS), 'limits': dict(retention.LIMITS),
            'exclusions': list(retention.EXCLUSIONS)}
        if marks:
            mark = marks[0]
            pending = mark['state'] == 'pending'
            eligible, reason = self._erase_eligibility(run_id)
            artifacts = retention.public_artifacts(mark.get('manifest', []))
            if pending:
                artifacts.insert(0, {'kind': 'catalog_record', 'present': True, 'bytes': mark['catalog_bytes']})
            return dict(base, state=mark['state'], eligible=pending and eligible,
                reason=reason if pending else 'This task has already been removed.',
                revision=mark['revision'], artifacts=artifacts, total_bytes=sum(row['bytes'] for row in artifacts))
        if not runs:
            raise WorkflowError('Unknown workflow run.', 404)
        run = runs[0]
        eligible, reason = self._erase_eligibility(run_id, run)
        files = retention.manifest(self.root, run_id,
            self.store.root_identity if self.store is not None else None)
        artifacts = retention.public_artifacts(files, run)
        if sum(row['bytes'] for row in artifacts) > retention.MAX_TOTAL_BYTES:
            raise retention.RetentionError('This task exceeds the bounded removal limit.')
        return dict(base, state='retained', eligible=eligible, reason=reason,
            revision=retention.revision(run, files), artifacts=artifacts,
            total_bytes=sum(row['bytes'] for row in artifacts))

    def erase_plan(self, run_id):
        try:
            with self.lock:
                return self._erase_plan(run_id)
        except (retention.RetentionError, OSError, sqlite3.Error, ValueError, KeyError) as error:
            raise WorkflowError('This task cannot be safely reviewed for removal.',
                                getattr(error, 'status', 409)) from None

    def erase(self, run_id, expected_revision, confirmed=False):
        if confirmed is not True or not isinstance(expected_revision, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_revision):
            raise WorkflowError('Review the task and explicitly confirm its exact removal revision.')
        try:
            with self.lock:
                plan = self._erase_plan(run_id)
                if plan['revision'] != expected_revision:
                    raise WorkflowError('This task changed after review. Review it again.', 409)
                if plan['state'] != 'erased' and not plan['eligible']:
                    raise WorkflowError(plan['reason'], 409)
                if plan['state'] == 'erased':
                    return self._erase_receipt(run_id, expected_revision, True, [])
                if self.store is None:
                    # Only a confirmed, reviewed existing task may initialize a
                    # writable catalog. Read-only status/plan never does this.
                    self.store = RunStore(self.root)
                    self._deferred_recovery = True
                    plan = self._erase_plan(run_id)
                    if plan['revision'] != expected_revision or not plan['eligible']:
                        raise WorkflowError('This task changed after review. Review it again.', 409)
                mark = self.store.erasure(run_id)
                retention.validate_paths(self.root, self.store.root_identity, self.store.catalog_identity)
                if mark is None:
                    run = self.store.get(run_id)
                    files = retention.manifest(self.root, run_id, self.store.root_identity)
                    if retention.revision(run, files) != expected_revision:
                        raise WorkflowError('This task changed after review. Review it again.', 409)
                    mark = self.store.begin_erasure(run_id, expected_revision, files, len(retention.state_bytes(run)))
                try:
                    retention.validate_paths(self.root, self.store.root_identity, self.store.catalog_identity)
                    removed = retention.remove(self.root, run_id, mark['manifest'], self.store.root_identity)
                    retention.validate_paths(self.root, self.store.root_identity, self.store.catalog_identity)
                    self.store.finish_erasure(mark)
                except (retention.RetentionError, OSError, sqlite3.Error):
                    # The durable pending marker blocks replay even if an unlink,
                    # fsync or final transaction failed. Only an explicit retry
                    # advances it, including after restarting this process.
                    return self._erase_receipt(run_id, expected_revision, False, [])
                self.condition.notify_all()
                return self._erase_receipt(run_id, expected_revision, True, ['catalog_record'] + removed)
        except (retention.RetentionError, OSError, sqlite3.Error, ValueError, KeyError) as error:
            raise WorkflowError('The task removal could not be safely started.', getattr(error, 'status', 409)) from None

    @staticmethod
    def _erase_receipt(run_id, revision, erased, removed):
        return {'schema_version': retention.SCHEMA_VERSION, 'id': run_id, 'revision': revision,
            'state': 'erased' if erased else 'pending', 'erased': erased, 'retryable': not erased,
            'removed_artifacts': removed, 'exclusions': list(retention.EXCLUSIONS)}

    def phase(self, run_id, phase):
        self.change(run_id, phase=phase)

    def agent(self, run_id, slot, **values):
        with self.lock:
            run = self.store.get(run_id)
            run['agents'][slot].update(values)
            self.store.save(run)

    def add_usage(self, run_id, slot, usage):
        with self.lock:
            run = self.store.get(run_id)
            row = run['agents'][slot]
            if 'totalTokens' in usage:
                row['tokens'] += usage['totalTokens']
                row['usage_reported'] = True
            else:
                row['unreported_attempts'] = row.get('unreported_attempts', 0) + 1
            row['current_tokens'] = None
            self.store.save(run)

    def event(self, run_id, kind, **values):
        if kind not in EVENT_KINDS or set(values) - {'slot', 'tool', 'node', 'error'}:
            raise ValueError('Non-public activity event')
        with self.lock:
            run = self.store.get(run_id)
            run['sequence'] += 1
            event = dict(id=run['sequence'], kind=kind, elapsed_s=round(time.time()-run['created_at'], SECONDS_DIGITS), **values)
            run['events'] = (run['events'] + [event])[-MAX_EVENTS:]
            self.store.save(run)
            self.condition.notify_all()

    def public(self, run):
        now = time.time()
        rows = []
        for row in run['agents'].values():
            rows.append({key: self.redactor.text(row[key], MAX_TASK_CHARS if key == 'task' else MAX_TEXT_CHARS)
                if key in ('task', 'output') else row[key] for key in
                ('slot', 'task', 'model', 'effort', 'status', 'attempts', 'tokens', 'usage_reported', 'unreported_attempts', 'current_tokens', 'output', 'error') if key in row})
            rows[-1]['elapsed_s'] = round(max(0, row.get('ended_at', now) - row['started_at']), SECONDS_DIGITS)
        result = run.get('result')
        resolved_nodes = result['nodes'] if result else []
        source_refs = result.get('source_refs', []) if result else []
        if source_refs:
            resolved_nodes = []
            for ref in source_refs:
                current = self.source_resolver(ref['stable_id']) if self.source_resolver else None
                if current and current['revision'] == ref['revision']:
                    resolved_nodes.append(current['id'])
        return {'id': run['id'], 'workflow': run['workflow'], 'question': self.redactor.text(run['question'], MAX_QUESTION_CHARS),
            'status': run['status'], 'phase': run['phase'], 'cleanup': run.get('cleanup', 'none'),
            'capabilities': {'cancel': True, 'resume': True, 'pause': False, 'watch': False}, 'error': run.get('error'), 'created_at': run['created_at'],
            'elapsed_s': round(max(0, run.get('ended_at', now)-run['created_at']), SECONDS_DIGITS),
            'remaining_s': round(max(0, run['expires_at']-now), SECONDS_DIGITS), 'agents': rows,
            'tasks': [self.redactor.text(task, MAX_TASK_CHARS) for task in run['tasks']], 'tool_calls': run['tool_calls'],
            'total_tokens': sum(row['tokens'] for row in rows) if any(row['usage_reported'] for row in rows) else None,
            'usage_complete': bool(rows) and all(row['usage_reported'] and not row.get('unreported_attempts') for row in rows),
            'model_usage': routing.ModelBudget(run['settings'].get('model_call_limit', routing.DEFAULT_MODEL_CALLS),
                run['settings'].get('observed_token_limit', routing.DEFAULT_TOKEN_BUDGET),
                run.get('routing_budget', [])).snapshot() if run['settings'].get('adaptive_routing_enabled') else None,
            'sequence': run['sequence'], 'events': run['events'], 'restarts': run['restarts'],
            'can_retry': run['status'] in ('failed', 'cancelled', 'interrupted') and now < run['expires_at']
                and run['restarts'] < MAX_RESTARTS and run['id'] not in self.owners
                and len(self.owners) < self.max_concurrent
                and all(row['status'] == 'complete' or
                    (row['attempts'] <= self.settings().worker_retries and row['expires_at'] > now)
                    for row in run['agents'].values())
                and run.get('error') not in ({'source_changed', 'model_budget_exhausted', 'context_budget_exhausted'} | usage_policy.ERROR_MESSAGES.keys())
                and run.get('cleanup') != 'pending',
            'result': {'answer': self.redactor.text(result['answer']), 'nodes': resolved_nodes,
                'source_refs': source_refs, 'sources_changed': bool(source_refs and len(resolved_nodes) != len(source_refs)),
                'about_notes': result['about_notes']} if result else None}

    def catalog(self):
        if self.enabled():
            self.initialize()
        with self.lock:
            return {'enabled': self.enabled(), 'available': self.store is not None, 'notice': self.unavailable,
                'engine': 'LangGraph', 'active': self.active, 'active_ids': self.active_ids,
                'capacity_notice': self.limit_notice,
                'capabilities': {'cancel': True, 'resume': True, 'pause': False, 'watch': False},
                'limits': {'concurrent_runs': self.max_concurrent, 'max_workers': min(MAX_WORKERS, self.settings().max_workers),
                    'shared_model_calls': MAX_WORKERS, 'shared_tool_calls': MAX_WORKERS,
                    'request_timeout_s': self.settings().request_timeout_s, 'max_runs': MAX_RUNS},
                'workflows': [{'id': key, 'label': value} for key, value in WORKFLOW_LABELS.items()],
                'runs': [self.public(run) for run in self.store.all()] if self.store else []}

    def detail(self, run_id):
        self.initialize()
        if self.store is None:
            raise WorkflowError(self.unavailable, 503)
        return self.public(self.store.get(run_id))

    def start(self, question=None, workflow='research', resume=None, request_key=None):
        if not self.enabled():
            raise WorkflowError('Enable Ultra workflows and tools before launching a workflow.', 409)
        self.initialize()
        if self.store is None:
            raise WorkflowError(self.unavailable, 503)
        with self.lock:
            if self._cleanup_stop.is_set():
                raise WorkflowError('The workflow service is shutting down.', 409)
            if resume is None:
                if (not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS
                        or not isinstance(workflow, str) or workflow not in WORKFLOW_LABELS):
                    raise WorkflowError('Supply a short question and a known workflow.')
                fingerprint = hashlib.sha256(json.dumps([question.strip(), workflow]).encode()).hexdigest()
                existing = self.store.admitted(request_key, fingerprint)
                if existing:
                    return self.public(existing)
            elif request_key is not None:
                raise WorkflowError('Resume uses its existing run identity.')
            if len(self.owners) >= self.max_concurrent or not self.slots.acquire(blocking=False):
                raise WorkflowError('JARVIS task capacity is occupied. Wait or cancel an active task.', 429)
            owned_id = None
            try:
                if resume is not None:
                    run = self.store.get(resume)
                    if not self.public(run)['can_retry']:
                        raise WorkflowError('This run cannot resume within its original budget. Start a new workflow.', 409)
                    if run['settings'] != self.settings().__dict__:
                        raise WorkflowError('Settings changed. Start a new workflow.', 409)
                    run.update(status='running', error=None, restarts=run['restarts']+1)
                    run.pop('ended_at', None)
                else:
                    if not self.store.capacity():
                        raise WorkflowError('Workflow storage is full. Archive the private workflow store while JARVIS is stopped.', 409)
                    now = time.time()
                    run = {'id': secrets.token_hex(ID_BYTES), 'question': question.strip(), 'workflow': workflow,
                        'created_at': now, 'expires_at': now + self.settings().request_timeout_s,
                        'settings': self.settings().__dict__, 'status': 'running', 'phase': 'retrieve', 'tasks': [],
                        'agents': {}, 'events': [], 'sequence': 0, 'restarts': 0, 'tool_calls': 0, 'cleanup': 'none',
                        'versions': {'engine': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                            'tools': hashlib.sha256(json.dumps(self.tools.catalog(),sort_keys=True).encode()).hexdigest(),
                            'behavior': hashlib.sha256(self.system.encode()).hexdigest()}}
                if resume is None:
                    self.store.admit(run, request_key, fingerprint)
                else:
                    self.store.save(run)
                cancel = threading.Event()
                thread = threading.Thread(target=self._execute, args=(run['id'], resume is not None, cancel), daemon=True,
                    name='jarvis-langgraph')
                owned_id = run['id']
                self.owners[owned_id] = {'thread': thread, 'cancel': cancel,
                    'bridge': None, 'bridge_cleanup_pending': False,
                    'bridge_lock': threading.Lock()}
                self.thread = thread
                thread.start()
                return self.public(run)
            except BaseException:
                if owned_id is not None:
                    self.owners.pop(owned_id, None)
                self.slots.release()
                raise

    def _execute(self, run_id, resume, cancel):
        conn = None
        jobs = None
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
            # LangGraph/LangChain honor explicit tracing_context(enabled=False), even
            # if a user's shell has LANGSMITH_TRACING set. Never enable cloud traces.
            from langsmith import tracing_context
            conn = RunStore.connect(self.root / (run_id + '.sqlite'))
            with self.lock:
                # An unconfirmed turn interrupt can close a bridge process.
                # A separate bridge per task prevents it from killing another
                # admitted task; workers inside this task still share one.
                bridge = self.bridge_factory()
                self.owners[run_id]['bridge'] = bridge
                self.bridge = bridge  # Most recent bridge, diagnostics only.
            jobs = GraphJobs(self, run_id, bridge, cancel)
            graph = compile_workflow(jobs, SqliteSaver(conn, serde=JsonPlusSerializer(
                pickle_fallback=False, allowed_json_modules=(), allowed_msgpack_modules=())))
            config = {'configurable': {'thread_id': run_id}, 'max_concurrency': min(MAX_WORKERS, self.settings().max_workers),
                'recursion_limit': GRAPH_RECURSION_LIMIT, 'callbacks': []}
            run = self.store.get(run_id)
            if resume:
                saved = graph.get_state(config)
                # Reject stale evidence. Re-reading is bounded and read-only.
                saved_notes = {note['id']: note for note in saved.values.get('notes', [])}
                for report in saved.values.get('reports', {}).values():
                    saved_notes.update({note['id']: note for note in report.get('_sources', [])})
                for note in saved_notes.values():
                    if note.get('stable_id') and self.source_resolver:
                        resolved = self.source_resolver(note['stable_id'])
                        if not resolved or resolved['id'] != note['id']:
                            raise BridgeError('source_changed')
                    current = jobs.tool('note_read', {'id': note['id']}, 'recovery')
                    if note.get('revision') != current.get('revision'):
                        raise BridgeError('source_changed')
            self.event(run_id, 'resumed' if resume else 'started')
            state = None if resume else {'question': run['question'], 'workflow': run['workflow'], 'reports': {}}
            with tracing_context(enabled=False):
                for update in graph.stream(state, config, stream_mode='updates', durability='sync'):
                    for node in update:
                        if node in ('retrieve', 'assign', 'worker', 'review'):
                            self.event(run_id, 'node_complete', node=node)
                jobs.guard()
                result = graph.get_state(config).values['result']
            self.change(run_id, status='complete', phase='complete', result=result, ended_at=time.time(), error=None)
            self.event(run_id, 'complete')
        except Exception as error:
            code = getattr(error, 'code', 'storage_unavailable' if isinstance(error, (OSError, sqlite3.Error)) else 'inference_failed')
            code = code if code in SAFE_FAILURES else 'inference_failed'
            status = 'cancelled' if cancel.is_set() else 'failed'
            self.change(run_id, status=status, error=code, ended_at=time.time())
            self.event(run_id, status)
        finally:
            if conn is not None:
                conn.close()
            self.close_bridge(run_id)
            with self.lock:
                owner = self.owners.get(run_id, {})
                self.change(run_id, cleanup='pending' if self.pending_tools.get(run_id, 0)
                    or owner.get('bridge_cleanup_pending') else 'complete')
                if jobs is not None:
                    jobs.session.close()
                try:
                    from outcome_collection import capture
                    self.change(run_id, outcome_collection=capture(self.store.get(run_id)))
                except Exception:
                    self.change(run_id, outcome_collection='unavailable')
                # Ownership lasts through every final write, including optional
                # minimized outcome capture. Removal can only begin afterwards.
                self.finished_executors.add(run_id)
                self.release_owner(run_id)
                self._schedule_cleanup_retry(run_id)

    def close_bridge(self, run_id, expected_owner=None):
        """Keep capacity if process cleanup could not be confirmed."""
        with self.lock:
            owner = self.owners.get(run_id)
            if expected_owner is not None and owner is not expected_owner:
                return
        if owner is None:
            return
        with owner['bridge_lock']:
            bridge = owner.get('bridge')
            if bridge is None:
                return
            try:
                bridge.close()
                pending = False
            except Exception:
                pending = True
            with self.lock:
                owner['bridge_cleanup_pending'] = pending
                if not pending:
                    owner['bridge'] = None

    def _schedule_cleanup_retry(self, run_id):
        """One background closer per finished owner; dashboard reads never wait."""
        with self.lock:
            owner = self.owners.get(run_id)
            if (self._cleanup_stop.is_set() or owner is None
                    or run_id not in self.finished_executors
                    or not owner.get('bridge_cleanup_pending')
                    or owner.get('cleanup_thread') is not None):
                return
            thread = threading.Thread(target=self._retry_bridge_cleanup,
                args=(run_id, owner), daemon=True, name='jarvis-workflow-cleanup')
            owner['cleanup_thread'] = thread
            self._cleanup_threads.add(thread)
            thread.start()

    def _retry_bridge_cleanup(self, run_id, owner):
        delay = BRIDGE_CLEANUP_RETRY_INITIAL_S
        try:
            while not self._cleanup_stop.wait(delay):
                with self.lock:
                    if self.owners.get(run_id) is not owner:
                        return
                self.close_bridge(run_id, expected_owner=owner)
                with self.condition:
                    if self.owners.get(run_id) is not owner:
                        return
                    if not owner.get('bridge_cleanup_pending'):
                        try:
                            self.change(run_id, cleanup='pending'
                                if self.pending_tools.get(run_id, 0) else 'complete')
                        except (OSError, sqlite3.Error):
                            # Keep admission ownership until its durable state
                            # can be updated; process capacity is already free.
                            pass
                        else:
                            self.release_owner(run_id)
                            return
                delay = min(BRIDGE_CLEANUP_RETRY_MAX_S,
                    delay * BRIDGE_CLEANUP_RETRY_BACKOFF)
        finally:
            with self.condition:
                thread = threading.current_thread()
                if owner.get('cleanup_thread') is thread:
                    owner['cleanup_thread'] = None
                self._cleanup_threads.discard(thread)
                self.condition.notify_all()

    def cancel(self, run_id):
        with self.lock:
            owner = self.owners.get(run_id)
            if owner is None or run_id in self.finished_executors:
                raise WorkflowError('That workflow is no longer running.', 409)
            owner['cancel'].set()
            self.change(run_id, status='cancelling')
            self.event(run_id, 'cancel_requested')
        return self.detail(run_id)

    def close(self):
        with self.lock:
            self._cleanup_stop.set()
            owners = list(self.owners.items())
            cleanup_threads = list(self._cleanup_threads)
            for _, owner in owners:
                owner['cancel'].set()
        for run_id, _ in owners:
            self.close_bridge(run_id)
        deadline = time.monotonic() + SQL_TIMEOUT_S
        for _, owner in owners:
            owner['thread'].join(timeout=max(0, deadline-time.monotonic()))
        for thread in cleanup_threads:
            thread.join(timeout=max(0, deadline-time.monotonic()))
        with self.condition:
            for run_id, owner in owners:
                if run_id in self.finished_executors:
                    self.change(run_id, cleanup='pending' if self.pending_tools.get(run_id, 0)
                        or owner.get('bridge_cleanup_pending') else 'complete')
                    self.release_owner(run_id)
            while any(self.pending_tools.values()) and time.monotonic() < deadline:
                self.condition.wait(timeout=min(TOOL_POLL_S, deadline-time.monotonic()))
            if (self.store and not self.owners and not self._cleanup_threads
                    and not any(self.pending_tools.values())):
                self.store.close()

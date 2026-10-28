"""One typed tool registry shared by the crew, local HTTP and the MCP adapter."""
import hashlib
import inspect
import json
import secrets
import threading
import time
from pathlib import Path
from ultra_memory import atomic_json
from execution_control import ControlStore, ControlError, digest, MAX_DURATION_S
from execution_gateway import ToolGateway

MAX_TOOL_CALLS = 24
TOOL_SESSION_ID_BYTES = 12
TOOL_SESSION_ID_CHARS = TOOL_SESSION_ID_BYTES * 2
MAX_ARGUMENT_BYTES = 240000
MAX_TOOL_RESULT_CHARS = 40000
MAX_JOURNAL_BYTES = 1500000
JOURNAL_ENVELOPE_BYTES = 4096
ROOT = Path(__file__).resolve().parent
JOURNAL_DIR = ROOT / ".local" / "ultra-tools"
STRING = {"type": "string"}
INTEGER = {"type": "integer"}
TOOL_NAMES = frozenset({"notes_search", "note_read", "notes_capture", "note_write", "preferences_get",
    "preferences_set", "preferences_forget", "screen_watch_status",
    "screen_watch_control", "document_import"})


class ToolError(ValueError):
    pass


def spec(name, description, properties=None, required=(), write=False, feature=None):
    return {"name": name, "description": description,
        "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False},
        "write": write, "feature": feature}


SPECS = (
    spec("notes_search", "Search the local notes. Results contain verbatim passages and citation IDs.", {"query": STRING}, ("query",)),
    spec("note_read", "Read an existing note and its revision before editing it.", {"id": INTEGER}, ("id",)),
    spec("notes_capture", "Save a new Markdown note only when the user asks to remember or save content.", {"text": STRING}, ("text",), True),
    spec("note_write", "Replace a note using the revision from note_read. Use only for a user-requested edit.",
        {"id": INTEGER, "text": STRING, "revision": STRING}, ("id", "text", "revision"), True),
    spec("preferences_get", "Read the user's explicitly saved preferences.", feature="preferences_enabled"),
    spec("preferences_set", "Queue an explicitly requested preference for review in Work. It is saved only after approval. Never save credentials.",
        {"key": STRING, "value": STRING}, ("key", "value"), True, "preferences_enabled"),
    spec("preferences_forget", "Queue forgetting an explicitly requested preference for review in Work; removal requires approval.", {"key": STRING}, ("key",), True, "preferences_enabled"),
    spec("screen_watch_status", "Read whether screen watch is active; this does not capture a screen."),
    spec("screen_watch_control", "Request screen watch start or stop. Starting still needs the browser's display-sharing permission.",
        {"action": {"type": "string", "enum": ["start", "stop"]}}, ("action",), True),
    spec("document_import", "Import a PDF, DOCX or XLSX already placed by the user in .local/ultra-inbox. Filename only.",
        {"filename": STRING}, ("filename",), True, "document_import_enabled"),
)


class Registry:
    argument_limit = MAX_ARGUMENT_BYTES
    result_limit = MAX_TOOL_RESULT_CHARS
    def __init__(self, settings, callbacks):
        self.settings, self.callbacks = settings, callbacks

    def available(self):
        settings = self.settings()
        if not settings.enabled or not settings.tools_enabled:
            return []
        return [item for item in SPECS if item["name"] in self.callbacks and
            (not item["feature"] or getattr(settings, item["feature"])) and
            (not item["name"].startswith("preferences_") or settings.memory_enabled)]

    def catalog(self):
        return [{"name": item["name"], "description": item["description"], "inputSchema": item["inputSchema"],
            "annotations": {"readOnlyHint": not item["write"], "destructiveHint": item["name"] == "note_write",
                "idempotentHint": not item["write"], "openWorldHint": False}} for item in self.available()]

    def dynamic_specs(self):
        return [{"type": "function", **{key: item[key] for key in ("name", "description", "inputSchema")}} for item in self.available()]

    def lookup(self, name, arguments):
        item = next((item for item in self.available() if item["name"] == name), None)
        if item is None or not isinstance(arguments, dict):
            raise ToolError("Tool unavailable or invalid arguments.")
        schema = item["inputSchema"]
        if set(arguments) - set(schema["properties"]) or set(schema["required"]) - set(arguments):
            raise ToolError("Invalid tool arguments.")
        if len(json.dumps(arguments, ensure_ascii=True)) > self.argument_limit:
            raise ToolError("Tool arguments exceed the limit.")
        for key, value in arguments.items():
            field = schema["properties"][key]
            if (field["type"] == "string" and not isinstance(value, str) or
                    field["type"] == "integer" and type(value) is not int or
                    "enum" in field and value not in field["enum"]):
                raise ToolError("Invalid tool argument type.")
        return item

    def call(self, name, arguments, deadline=None):
        self.lookup(name, arguments)
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise ToolError("Tool time budget exhausted.")
            internal = {"_deadline": deadline} if deadline is not None and "_deadline" in inspect.signature(self.callbacks[name]).parameters else {}
            result = self.callbacks[name](**arguments, **internal)
            if len(json.dumps(result, ensure_ascii=True)) > self.result_limit:
                raise ToolError("Tool result exceeds the limit. Request a smaller passage.")
            return result
        except ToolError:
            raise
        except Exception:
            raise ToolError("The local operation could not be completed. Check its inputs and current state.") from None


class ToolSession:
    """Serialize writes and journal results so peers/retries cannot repeat them.

    A crash after a pending record is saved leaves an uncertain operation. Recovery
    refuses to replay it automatically; a fresh user request can resolve it.
    """
    def __init__(self, registry, identifier=None, root=None, permissions=None, cancel=None, request_fingerprint=None):
        self.registry = registry
        self.cancel = cancel
        self.close_requested = False
        self.active_call = False
        self.permissions = frozenset(permissions) if permissions is not None else None
        self.request_fingerprint = request_fingerprint
        self.identifier = identifier or secrets.token_hex(TOOL_SESSION_ID_BYTES)
        if len(self.identifier) != TOOL_SESSION_ID_CHARS or any(c not in "0123456789abcdef" for c in self.identifier):
            raise ToolError("Invalid tool session.")
        self.path = Path(JOURNAL_DIR if root is None else root) / (self.identifier + ".json")
        self.lock = threading.RLock()
        self.entries, self.count = {}, 0
        try:
            if self.path.is_symlink() or self.path.stat().st_size > max(MAX_JOURNAL_BYTES, registry.result_limit + JOURNAL_ENVELOPE_BYTES):
                raise ToolError("Tool journal unavailable.")
            saved = json.loads(self.path.read_text())
            self.entries, self.count = saved["entries"], saved["count"]
            if not isinstance(self.entries, dict) or type(self.count) is not int or not 0 <= self.count <= MAX_TOOL_CALLS:
                raise ToolError("Tool journal unavailable.")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            raise ToolError("Tool journal unavailable.") from None

    def _save(self):
        atomic_json(self.path, {"count": self.count, "entries": self.entries})

    def _gateway(self, deadline):
        if hasattr(self, 'gateway'):
            return
        self.control = ControlStore(self.path.with_suffix('.sqlite'))
        names = {item['name'] for item in self.registry.available()}
        if self.permissions is not None:
            names &= self.permissions
        # Durable session lifespan can never expand on recovery. Existing JSON
        # uncertain writes remain authoritative and are checked before this layer.
        versions={'engine': '1', 'model': 'caller-owned', 'tools': digest(self.registry.catalog()), 'behavior': 'incumbent'}
        if self.request_fingerprint is not None:
            versions['request']=self.request_fingerprint
        self.run, _ = self.control.admit(self.identifier, conversation_id=self.identifier,
            project_id='local', engine='legacy-tools', versions=versions,
            permissions=names, duration_s=MAX_DURATION_S, max_calls=MAX_TOOL_CALLS)
        if self.run['status'] == 'queued':
            self.control.transition(self.run['id'], 'running')
        self.token = self.control.issue(self.run['id'])
        self.gateway = ToolGateway(self.control, self.registry)

    def call(self, name, arguments, deadline, observer=None):
        with self.lock:
            if self.close_requested:
                raise ToolError("Tool session is closed.")
            if time.monotonic() >= deadline or self.cancel is not None and self.cancel.is_set():
                raise ToolError("Tool time budget exhausted or cancelled.")
            item = self.registry.lookup(name, arguments)
            if self.permissions is not None and name not in self.permissions:
                raise ToolError("Tool outside execution scope.")
            self._gateway(deadline)
            self.control.authenticate(self.token, self.run['id'])
            fingerprint = hashlib.sha256(json.dumps([name, arguments], sort_keys=True, ensure_ascii=True).encode()).hexdigest()
            if item["write"] and fingerprint in self.entries:
                saved = self.entries[fingerprint]
                if saved["status"] == "complete":
                    return saved["result"]
                raise ToolError("A previous write has an uncertain result. Inspect local state before a fresh request.")
            if self.count >= MAX_TOOL_CALLS:
                raise ToolError("Tool call budget exhausted.")
            self.count += 1
            if item['write']:
                self.entries[fingerprint] = {'status': 'pending'}
            self._save()
            if observer:
                observer(name)
            self.active_call = True
            try:
                result = self.gateway.invoke(self.token, self.run['id'], name, arguments, deadline, self.cancel)
                if item['write']:
                    self.entries[fingerprint] = {'status': 'complete', 'result': result}
                    self._save()
                return result
            except ControlError:
                raise ToolError("Execution expired, fenced, uncertain or outside its tool budget.") from None
            finally:
                self.active_call = False
                if self.close_requested:
                    self._close_store()
                if observer:
                    observer(None)

    def _close_store(self):
        if hasattr(self, 'control'):
            self.control.close()
            del self.control
            if hasattr(self, 'gateway'):
                del self.gateway

    def close(self):
        # A timeout is not proof that a callback stopped. Defer closing until
        # the executing thread settles; never block the model deadline here.
        self.close_requested = True
        if self.lock.acquire(blocking=False):
            try:self._close_store()
            finally:self.lock.release()

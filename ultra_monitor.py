"""One metadata-only source for the browser drawer and terminal table."""
import contextlib
import secrets
import sys
import threading
import time
from codex_bridge import VERIFIED_MODELS
from ultra_tools import TOOL_NAMES

REFRESH_S = 0.5
ELAPSED_DIGITS = 1
ID_BYTES = 6
MAX_JOBS = 64
MAX_EVENTS = 32
MAX_CREW_SLOTS = 16
MAX_PUBLIC_COUNT = 10 ** 12
MAX_SAVED_ELAPSED_S = 300
CREW_SLOTS = frozenset({"boss"} | {"worker-" + str(n) for n in range(1, MAX_CREW_SLOTS + 1)})
EFFORTS = frozenset({"low", "medium", "high", "max"})
ROLES = frozenset({"dispatch", "brain", "worker", "answer", "vision"})
STATES = frozenset({"queued", "running", "retrying", "complete", "failed", "skipped"})
PHASES = frozenset({"classifying", "planning", "answering", "checking", "waiting", "done",
    "drafting", "sharing", "reviewing", "reporting", "recovering"})
EVENTS = frozenset({"planned", "drafts_ready", "peer_review_started", "peer_review_complete",
    "reported", "resumed", "checkpoint_saved"})
FAILURES = frozenset({"invalid_output", "missing_sources", "math_check", "timeout", "unavailable",
    "protocol_failure", "model_unavailable", "effort_unavailable", "image_unavailable", "inference_failed",
    "blocked_action", "model_mismatch", "disconnected", "request_timeout", "budget_exhausted",
    "model_budget_exhausted", "context_budget_exhausted", "invalid_routing_profile",
    "subscription_required", "quota_exhausted", "usage_unavailable"})


class Monitor:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._lock = threading.RLock()
        self._request_id = None
        self._started = None
        self._ended = None
        self._jobs = {}
        self._events = []
        self._escalations = 0

    def begin(self):
        with self._lock:
            self._request_id = secrets.token_hex(ID_BYTES)
            self._started, self._ended = self.clock(), None
            self._jobs = {}
            self._events = []
            self._escalations = 0
            return self._request_id

    def add(self, role, model, effort="high", slot=None):
        if role not in ROLES or model not in VERIFIED_MODELS or effort not in EFFORTS or (slot is not None and slot not in CREW_SLOTS):
            raise ValueError("Unknown monitor role")
        with self._lock:
            if len(self._jobs) >= MAX_JOBS:
                raise ValueError("Monitor capacity exceeded")
            key = "agent-" + str(len(self._jobs) + 1)
            self._jobs[key] = {"id": key, "role": role, "model": model, "effort": effort, "slot": slot, "status": "queued",
                "phase": "waiting", "current_tool": None, "retries": 0, "failure": None,
                "input_tokens": None, "output_tokens": None, "total_tokens": None,
                "started": self.clock(), "ended": None}
            return key

    def update(self, key, **values):
        allowed = {"model", "effort", "status", "phase", "current_tool", "retries", "failure", "input_tokens", "output_tokens", "total_tokens"}
        if set(values) - allowed:
            raise ValueError("Non-public monitor field")
        if "model" in values and values["model"] not in VERIFIED_MODELS:
            raise ValueError("Unverified monitor model")
        if "effort" in values and values["effort"] not in EFFORTS:
            raise ValueError("Invalid monitor effort")
        if "status" in values and values["status"] not in STATES:
            raise ValueError("Invalid status")
        if "phase" in values and values["phase"] not in PHASES:
            raise ValueError("Invalid phase")
        if values.get("current_tool") is not None and values["current_tool"] not in TOOL_NAMES:
            raise ValueError("Unknown tool")
        for field in ("retries", "input_tokens", "output_tokens", "total_tokens"):
            if field in values and values[field] is not None and (type(values[field]) is not int or not 0 <= values[field] <= MAX_PUBLIC_COUNT):
                raise ValueError("Invalid public counter")
        if values.get("failure") is not None and values["failure"] not in FAILURES:
            values["failure"] = "unavailable"
        with self._lock:
            row = self._jobs[key]
            row.update(values)
            if row["status"] in ("complete", "failed", "skipped"):
                row["ended"] = self.clock()
            elif row["status"] in ("running", "queued", "retrying"):
                row["ended"] = None

    def event(self, kind, slot=None):
        """Publish only a fixed activity name and crew slot, never model content."""
        if not isinstance(kind, str) or kind not in EVENTS or (slot is not None and
                (not isinstance(slot, str) or slot not in CREW_SLOTS)):
            raise ValueError("Unknown monitor event")
        with self._lock:
            elapsed = self.clock() - self._started if self._started is not None else 0
            self._events.append({"kind": kind, "slot": slot,
                "elapsed_s": round(elapsed, ELAPSED_DIGITS)})
            self._events = self._events[-MAX_EVENTS:]

    def restore(self, row):
        """Restore previously observed counters through the same public allowlists."""
        key = self.add(row["role"], row["model"], row["effort"], row.get("slot"))
        values = {name: row[name] for name in ("status", "retries", "failure", "input_tokens", "output_tokens", "total_tokens")}
        if values["status"] in ("running", "retrying", "queued"):
            values.update(status="failed", failure="disconnected")
        self.update(key, phase="recovering", **values)
        elapsed = row.get("elapsed_s", 0)
        if type(elapsed) not in (int, float) or not 0 <= elapsed <= MAX_SAVED_ELAPSED_S:
            raise ValueError("Invalid saved elapsed time")
        with self._lock:
            self._jobs[key]["started"] = self.clock() - elapsed
            self._jobs[key]["ended"] = self.clock()
        return key

    def escalate(self):
        with self._lock:
            self._escalations += 1

    def finish(self):
        with self._lock:
            self._ended = self.clock()

    def snapshot(self):
        with self._lock:
            now = self.clock()
            rows = []
            for value in self._jobs.values():
                row = {k: v for k, v in value.items() if k not in ("started", "ended")}
                row["elapsed_s"] = round((value["ended"] if value["ended"] is not None else now) - value["started"], ELAPSED_DIGITS)
                rows.append(row)
            return {"request_id": self._request_id, "active": self._started is not None and self._ended is None,
                "elapsed_s": round(((self._ended if self._ended is not None else now) - self._started)
                    if self._started is not None else 0, ELAPSED_DIGITS),
                "escalations": self._escalations, "agents": rows,
                "events": [dict(event) for event in self._events]}


def table(snapshot):
    """No prompts, tasks, answers, raw tool arguments or reasoning in this table."""
    lines = ["ULTRA JARVIS | MODEL             EFFORT STATUS    PHASE       TOOL  SECONDS RETRIES TOKENS",
             "-------------+---------------------------------------------------------------------------"]
    for row in snapshot["agents"]:
        tokens = "?" if row["total_tokens"] is None else str(row["total_tokens"])
        label = row["slot"] or row["role"]
        tool = row["current_tool"] or "—"
        lines.append(f'{label:12} | {row["model"]:17} {row["effort"]:6} {row["status"]:9} {row["phase"]:11} {tool:20} '
                     f'{row["elapsed_s"]:6.1f} {row["retries"]:7} {tokens:>6}')
    lines.append(f'Elapsed {snapshot["elapsed_s"]:.1f}s | Escalations {snapshot["escalations"]} | '
                 + ("Working" if snapshot["active"] else "Finished"))
    return "\n".join(lines)


@contextlib.contextmanager
def terminal(monitor, enabled, stream=None):
    if not enabled:
        yield
        return
    stream = stream or sys.stdout
    stop = threading.Event()
    tty = getattr(stream, "isatty", lambda: False)()

    def render():
        lines = 0
        previous = None
        while True:
            snapshot = monitor.snapshot()
            signature = [(r["id"], r["model"], r["effort"], r["status"], r["phase"], r["current_tool"], r["retries"], r["total_tokens"]) for r in snapshot["agents"]]
            signature.append(snapshot["active"])
            if tty or signature != previous:
                content = table(snapshot)
                try:
                    if tty and lines:
                        stream.write(f"\033[{lines}A\033[J")
                    stream.write(content + "\n")
                    stream.flush()
                except (OSError, ValueError):
                    return
                lines = content.count("\n") + 1
                previous = signature
            if stop.wait(REFRESH_S):
                try:
                    if tty and lines:
                        stream.write(f"\033[{lines}A\033[J")
                    stream.write(table(monitor.snapshot()) + "\n")
                    stream.flush()
                except (OSError, ValueError):
                    pass
                return

    thread = threading.Thread(target=render, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=REFRESH_S)

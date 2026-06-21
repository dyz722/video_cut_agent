"""Live run events for the terminal UI.

The event stream is deliberately not chain-of-thought. It records observable
agent behavior: what the harness is doing, what a tool returned in summary, and
which issue or next action is visible from the run.
"""

from dataclasses import dataclass, asdict
import threading
import time


MAX_EVENTS = 240


@dataclass
class RunEvent:
    seq: int
    ts: str
    kind: str
    summary: str
    name: str = ""
    detail: str = ""
    run_id: str = ""
    round: int = 0


class RunEventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._events: list[RunEvent] = []
        self._seq = 0
        self.live_enabled = True
        self.current_run = {
            "running": False,
            "run_id": "",
            "status": "idle",
            "current": "",
            "started_at": "",
            "stop_requested": False,
        }

    def emit(self, kind: str, summary: str, *, name: str = "", detail: str = "",
             run_id: str = "", round: int = 0, print_event: bool = True) -> RunEvent:
        with self._lock:
            self._seq += 1
            event = RunEvent(
                seq=self._seq,
                ts=time.strftime("%H:%M:%S"),
                kind=kind,
                summary=_shorten(summary, 260),
                name=name,
                detail=_shorten(detail, 1600),
                run_id=run_id or self.current_run.get("run_id", ""),
                round=round,
            )
            self._events.append(event)
            del self._events[:-MAX_EVENTS]
            if kind in ("tool", "obs", "status", "plan", "issue", "bg"):
                self.current_run["current"] = event.summary
                self.current_run["status"] = kind
        if print_event and self.live_enabled:
            print(format_event(event))
        return event

    def start_run(self, run_id: str):
        with self._lock:
            self.current_run = {
                "running": True,
                "run_id": run_id,
                "status": "running",
                "current": "starting",
                "started_at": time.strftime("%H:%M:%S"),
                "stop_requested": False,
            }
        self.emit("status", f"run started: {run_id}", run_id=run_id)

    def finish_run(self, status: str = "idle"):
        run_id = self.current_run.get("run_id", "")
        self.emit("status", f"run {status}: {run_id}", run_id=run_id)
        with self._lock:
            self.current_run.update({
                "running": False,
                "status": status,
                "current": "",
                "stop_requested": False,
            })

    def request_stop(self) -> str:
        with self._lock:
            if not self.current_run.get("running"):
                return "No agent run is active."
            self.current_run["stop_requested"] = True
            run_id = self.current_run.get("run_id", "")
        self.emit("issue", "stop requested; will halt after the current model/tool call",
                  run_id=run_id)
        return "Stop requested. The current blocking API/tool call may finish first."

    def status_text(self) -> str:
        with self._lock:
            state = dict(self.current_run)
        if not state.get("running"):
            return "No agent run is active."
        return "\n".join([
            f"run_id: {state.get('run_id')}",
            f"started_at: {state.get('started_at')}",
            f"status: {state.get('status')}",
            f"current: {state.get('current') or '-'}",
            f"stop_requested: {state.get('stop_requested')}",
        ])

    def render(self, limit: int = 20, kind: str | None = None) -> str:
        with self._lock:
            events = list(self._events)
        if kind:
            events = [e for e in events if e.kind == kind]
        events = events[-limit:]
        if not events:
            return "No live events yet."
        return "\n".join(format_event(e, include_seq=True) for e in events)

    def clear(self):
        with self._lock:
            self._events.clear()


def _shorten(text: str, limit: int) -> str:
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit - 1] + "..."


def format_event(event: RunEvent, include_seq: bool = False) -> str:
    prefix = f"{event.seq}. " if include_seq else ""
    name = f" {event.name}" if event.name else ""
    return f"{prefix}[{event.ts}] [{event.kind}]{name} {event.summary}"


def event_to_dict(event: RunEvent) -> dict:
    return asdict(event)


EVENTS = RunEventBus()

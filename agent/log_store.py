"""Project-local JSONL log storage for run events and tool calls."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading

from . import config


EVENT_LOG = "events.jsonl"
TOOL_LOG = "tools.jsonl"
_LOCK = threading.Lock()


def log_dir() -> Path:
    root = config.PROJECT_DIR / ".veoai" / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def log_path(name: str) -> Path:
    return log_dir() / name


def append_jsonl(name: str, item: dict):
    data = dict(item)
    data.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    path = log_path(name)
    line = json.dumps(data, ensure_ascii=False, default=str)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_jsonl(name: str, limit: int = 500) -> list[dict]:
    path = log_path(name)
    if not path.exists():
        return []
    with _LOCK:
        lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def clear_jsonl(name: str):
    path = log_path(name)
    with _LOCK:
        path.write_text("", encoding="utf-8")


def log_summary() -> str:
    events = len(read_jsonl(EVENT_LOG, limit=100000))
    tools = len(read_jsonl(TOOL_LOG, limit=100000))
    return f"events: {events}, tools: {tools}, dir: {log_dir()}"

"""Project-local conversation sessions.

Sessions live under the current project directory so different video projects do
not share context. Each session stores its own messages list and metadata.
"""

from datetime import datetime
import json
import re
import time
import uuid
from pathlib import Path

from . import config


def _session_root() -> Path:
    root = config.PROJECT_DIR / ".veoai" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _json_default(obj):
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _safe_title(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text[:80] or "untitled"


def _path(session_id: str) -> Path:
    path = (_session_root() / f"{session_id}.json").resolve()
    if not path.is_relative_to(_session_root().resolve()):
        raise ValueError("session_id escapes session root")
    return path


def new_session(title: str = "") -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"


def session_title(messages: list) -> str:
    for msg in messages:
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            return _safe_title(msg["content"])
    return "new session"


def save_session(session_id: str, messages: list, title: str = "") -> Path:
    now = datetime.now().isoformat(timespec="seconds")
    fp = _path(session_id)
    existing = {}
    if fp.exists():
        try:
            existing = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    data = {
        "id": session_id,
        "title": _safe_title(title or existing.get("title") or session_title(messages)),
        "project": str(config.PROJECT_DIR),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "message_count": len(messages),
        "messages": messages,
    }
    fp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=_json_default),
                  encoding="utf-8")
    return fp


def load_session(session_id: str) -> dict:
    return json.loads(_path(session_id).read_text(encoding="utf-8"))


def list_sessions() -> list[dict]:
    sessions = []
    for fp in sorted(_session_root().glob("*.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            sessions.append({
                "id": data.get("id") or fp.stem,
                "title": data.get("title") or "untitled",
                "updated_at": data.get("updated_at") or "",
                "message_count": data.get("message_count", len(data.get("messages", []))),
                "path": fp,
            })
        except Exception:
            continue
    return sessions


def render_sessions(limit: int = 20) -> str:
    sessions = list_sessions()[:limit]
    if not sessions:
        return "No sessions in this project yet."
    rows = ["Sessions in current project:"]
    for idx, item in enumerate(sessions, start=1):
        rows.append(f"{idx}. {item['title']}  [{item['id']}]  "
                    f"{item['message_count']} messages  updated {item['updated_at']}")
    rows.append("Use /resume <number> or /resume <session_id> to continue.")
    return "\n".join(rows)


def resolve_session(ref: str) -> str | None:
    ref = (ref or "").strip()
    sessions = list_sessions()
    if not ref:
        return None
    if ref.isdigit():
        idx = int(ref) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx]["id"]
    for item in sessions:
        if item["id"] == ref:
            return item["id"]
    return None

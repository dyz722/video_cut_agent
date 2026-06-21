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
    rows.append("Use /resume <number or session_id>, or run /resume and choose an item.")
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


def _block_type(block) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _block_text(block) -> str:
    if isinstance(block, dict):
        return str(block.get("text", ""))
    return str(getattr(block, "text", ""))


def _tool_name(block) -> str:
    if isinstance(block, dict):
        return str(block.get("name", ""))
    return str(getattr(block, "name", ""))


def _clip(text: str, limit: int = 1400) -> str:
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _message_lines(msg: dict) -> list[str]:
    role = msg.get("role", "")
    content = msg.get("content", "")
    if role == "user" and isinstance(content, str):
        return [f"› user\n{_clip(content)}"]
    if role == "assistant" and isinstance(content, str):
        return [f"assistant\n{_clip(content)}"]
    if role == "assistant" and isinstance(content, list):
        text_parts = []
        tools = []
        for block in content:
            typ = _block_type(block)
            if typ == "text":
                text = _block_text(block).strip()
                if text:
                    text_parts.append(text)
            elif typ == "tool_use":
                name = _tool_name(block)
                if name:
                    tools.append(name)
        lines = []
        if text_parts:
            lines.append(f"assistant\n{_clip(chr(10).join(text_parts))}")
        if tools:
            lines.append(f"assistant tools\n{', '.join(tools)}")
        return lines
    return []


def render_conversation(messages: list, limit: int | None = None) -> str:
    """Render human-readable user/assistant history, hiding tool_result payloads."""
    display_messages = messages[-limit:] if limit else messages
    rows = []
    hidden = len(messages) - len(display_messages)
    if hidden > 0:
        rows.append(f"... {hidden} earlier messages hidden ...")
    for msg in display_messages:
        rows.extend(_message_lines(msg))
    if not rows:
        return "(no user/assistant messages to replay)"
    return "\n\n".join(rows)

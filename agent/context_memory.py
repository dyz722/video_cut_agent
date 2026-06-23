"""Durable project memory for expensive observations.

The live conversation can be compacted or resumed, but project facts such as
visual analysis results should survive outside the message window.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from . import config


WATCH_CACHE = "watch_video.json"
WATCH_SUMMARY = "watch_video.md"


def context_dir() -> Path:
    root = config.PROJECT_DIR / ".veoai" / "context"
    root.mkdir(parents=True, exist_ok=True)
    return root


def watch_cache_path() -> Path:
    return context_dir() / WATCH_CACHE


def watch_summary_path() -> Path:
    return context_dir() / WATCH_SUMMARY


def _relative(path: Path | str) -> str:
    fp = Path(path)
    try:
        return str(fp.resolve().relative_to(config.PROJECT_DIR.resolve()))
    except Exception:
        return str(fp)


def _segment_key(path: Path | str, start: float, end: float) -> str:
    return f"{_relative(path)}::{start:.2f}-{end:.2f}"


def _read_watch_cache() -> dict:
    path = watch_cache_path()
    if not path.exists():
        return {"version": 1, "items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "items": {}}
    if not isinstance(data, dict):
        return {"version": 1, "items": {}}
    data.setdefault("version", 1)
    data.setdefault("items", {})
    return data


def get_watch_result(path: Path | str, start: float, end: float) -> dict | None:
    data = _read_watch_cache()
    item = data.get("items", {}).get(_segment_key(path, start, end))
    return item if isinstance(item, dict) else None


def save_watch_result(path: Path | str, start: float, end: float, question: str,
                      mode: str, result: str):
    data = _read_watch_cache()
    key = _segment_key(path, start, end)
    data["items"][key] = {
        "path": _relative(path),
        "start": round(float(start), 2),
        "end": round(float(end), 2),
        "duration": round(float(end) - float(start), 2),
        "question": str(question or ""),
        "mode": mode,
        "result": str(result),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    watch_cache_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    watch_summary_path().write_text(render_watch_summary(80), encoding="utf-8")


def _clip(text: str, limit: int = 700) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def render_watch_summary(max_items: int = 80) -> str:
    """Return a readable summary file for subagents or manual inspection."""
    data = _read_watch_cache()
    items = list(data.get("items", {}).values())
    items.sort(key=lambda item: item.get("updated_at", ""))
    items = items[-max_items:]
    lines = [
        "# Visual Observation Cache",
        "",
        f"Project: `{config.PROJECT_DIR}`",
        f"Items: {len(items)}",
        "",
    ]
    if items:
        for item in items:
            lines.append(
                f"## {item.get('path')} {item.get('start')}s-{item.get('end')}s"
            )
            lines.append("")
            lines.append(f"- mode: `{item.get('mode', 'auto')}`")
            lines.append(f"- question: {_clip(item.get('question', ''), 240)}")
            lines.append(f"- updated_at: `{item.get('updated_at', '')}`")
            lines.append("")
            lines.append(_clip(item.get("result", ""), 1600))
            lines.append("")
    else:
        lines.append("No cached visual observations yet.")
    return "\n".join(lines).rstrip() + "\n"


def project_context_index() -> str:
    """Return a tiny stable memory index suitable for active context."""
    data = _read_watch_cache()
    count = len(data.get("items", {}))
    lines = [
        "<project-memory-index>",
        f"project: {config.PROJECT_DIR}",
        f"visual_observation_cache: .veoai/context/{WATCH_CACHE}",
        f"visual_observation_summary: .veoai/context/{WATCH_SUMMARY}",
        f"visual_observation_count: {count}",
        "rule: read the visual observation summary before repeating expensive watch_video calls.",
    ]

    analysis_dir = config.PROJECT_DIR / "analysis"
    if analysis_dir.exists():
        artifacts = sorted(
            p.relative_to(config.PROJECT_DIR)
            for p in analysis_dir.glob("*")
            if p.is_file() and p.suffix.lower() in (".json", ".md", ".txt")
        )
        if artifacts:
            lines.append("analysis_artifacts:")
            for artifact in artifacts[-40:]:
                lines.append(f"- {artifact}")
    lines.append("</project-memory-index>")
    return "\n".join(lines)


def project_context_snapshot(max_watch_items: int = 24) -> str:
    """Return a bounded snapshot for explicit memory reads, not every turn."""
    summary = render_watch_summary(max_watch_items)
    return f"{project_context_index()}\n\n{summary}"


def has_project_memory(messages: list) -> bool:
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and "<project-memory-index>" in content:
            return True
    return False


def inject_project_memory_if_sparse(messages: list):
    """Rehydrate short/compacted sessions without bloating normal long context."""
    if len(messages) > 3 or has_project_memory(messages):
        return
    messages.insert(0, {"role": "user", "content": project_context_index()})

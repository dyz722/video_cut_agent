# Harness: context management -- 转写稿和帧描述很吃 token, 要能腾地方. (port of s06)
"""
三层压缩:
    microcompact: 清掉旧 tool_result 大块内容 (保留最近 3 个)
    auto_compact: 超过阈值时全量摘要, 原文落盘 .transcripts/
    manual: agent 主动调 compress 工具
"""

import json
import os
import time

from . import config
from . import context_memory


def estimate_tokens(messages: list) -> int:
    return len(json.dumps(messages, default=str)) // 4


def model_context_window(model: str | None = None) -> int:
    model = (model or config.main_model()).lower()
    env_value = os.getenv("VEOAI_CONTEXT_WINDOW")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    if "claude" in model:
        return 200000
    if "gpt-4o" in model or "gpt-4.1" in model or "gpt-5" in model:
        return 128000
    if "qwen" in model:
        return 128000
    return 100000


def compact_threshold(model: str | None = None) -> int:
    env_value = os.getenv("VEOAI_COMPACT_THRESHOLD")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    return int(model_context_window(model) * 0.72)


def microcompact(messages: list):
    parts = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    parts.append(part)
    if len(parts) <= 3:
        return
    for part in parts[:-3]:
        if isinstance(part.get("content"), str) and len(part["content"]) > 600:
            part["content"] = "[cleared - re-run the tool or read analysis/ files if needed]"


def auto_compact(messages: list) -> list:
    tdir = config.PROJECT_DIR / ".transcripts"
    tdir.mkdir(exist_ok=True)
    path = tdir / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    project_memory = context_memory.project_context_index()
    conv_text = json.dumps(messages, default=str)[-80000:]
    resp = config.client().messages.create(
        model=config.main_model(),
        messages=[{"role": "user", "content":
                   "Summarize this video-editing session for continuity. Keep: project goal, "
                   "loaded skill, material analysis conclusions, timeline decisions made, "
                   "current todo state, pending work. Use project-memory-index as pointers "
                   "to durable files; do not inline all cached visual observations unless "
                   "they are essential.\n\n"
                   f"{project_memory}\n\n<conversation-tail>\n{conv_text}\n</conversation-tail>"}],
        max_tokens=2000,
    )
    summary = resp.content[0].text
    return [{"role": "user", "content":
             f"[Compressed. Full transcript: {path}]\n{project_memory}\n\n{summary}"}]

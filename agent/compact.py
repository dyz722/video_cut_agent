# Harness: context management -- 转写稿和帧描述很吃 token, 要能腾地方. (port of s06)
"""
三层压缩:
    microcompact: 清掉旧 tool_result 大块内容 (保留最近 3 个)
    auto_compact: 超过阈值时全量摘要, 原文落盘 .transcripts/
    manual: agent 主动调 compress 工具
"""

import json
import time

from . import config


def estimate_tokens(messages: list) -> int:
    return len(json.dumps(messages, default=str)) // 4


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
    conv_text = json.dumps(messages, default=str)[-80000:]
    resp = config.client().messages.create(
        model=config.main_model(),
        messages=[{"role": "user", "content":
                   "Summarize this video-editing session for continuity. Keep: project goal, "
                   "loaded skill, material analysis conclusions, timeline decisions made, "
                   "current todo state, pending work.\n" + conv_text}],
        max_tokens=2000,
    )
    summary = resp.content[0].text
    return [{"role": "user", "content": f"[Compressed. Full transcript: {path}]\n{summary}"}]

# Harness: context isolation -- 长视频分段分析, 每段独立 messages[], 只回传摘要. (port of s04)
"""
run_subagent: 为"分析"而生的子 agent。
典型用法: 主 agent 把 2 小时直播按 20 分钟分段, 每段派一个 subagent
读 transcript 片段 + 按需看画面, 回传结构化的候选片段清单。
"""

from . import config

SUB_SYSTEM = """You are a video-analysis subagent in project {workdir}.
Analyze ONLY what the prompt asks. Use read_file to query analysis/transcript.json
segments, watch_video to confirm visuals (sparingly), probe_media for metadata.
Finish with a compact structured summary (markdown list or JSON) -- it is the ONLY
thing returned to the lead agent. Do not include raw transcript dumps."""


def run_subagent(prompt: str, agent_type: str = "Analyze") -> str:
    # 延迟导入避免循环依赖
    from .tools import run_bash, run_read, run_write
    from perception.probe import probe_media
    from perception.watch import watch_video

    sub_tools = [
        {"name": "bash", "description": "Run shell command in project dir.",
         "input_schema": {"type": "object", "properties": {"command": {"type": "string"}},
                          "required": ["command"]}},
        {"name": "read_file", "description": "Read file (supports offset/limit lines).",
         "input_schema": {"type": "object", "properties": {
             "path": {"type": "string"}, "offset": {"type": "integer"},
             "limit": {"type": "integer"}}, "required": ["path"]}},
        {"name": "probe_media", "description": "Get media metadata via ffprobe.",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                          "required": ["path"]}},
        {"name": "watch_video", "description": "Look at a video segment with a VL model.",
         "input_schema": {"type": "object", "properties": {
             "path": {"type": "string"}, "start": {"type": "number"},
             "end": {"type": "number"}, "question": {"type": "string"}},
             "required": ["path", "start", "end", "question"]}},
    ]
    if agent_type != "Analyze":
        sub_tools.append(
            {"name": "write_file", "description": "Write file.",
             "input_schema": {"type": "object", "properties": {
                 "path": {"type": "string"}, "content": {"type": "string"}},
                 "required": ["path", "content"]}})

    sub_handlers = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit"), kw.get("offset")),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "probe_media": lambda **kw: probe_media(kw["path"]),
        "watch_video": lambda **kw: watch_video(kw["path"], kw["start"], kw["end"],
                                                kw["question"]),
    }

    sub_msgs = [{"role": "user", "content": prompt}]
    resp = None
    for _ in range(40):
        resp = config.client().messages.create(
            model=config.main_model(),
            system=SUB_SYSTEM.format(workdir=config.PROJECT_DIR),
            messages=sub_msgs, tools=sub_tools, max_tokens=8000)
        sub_msgs.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                h = sub_handlers.get(b.name, lambda **kw: "Unknown tool")
                try:
                    out = str(h(**b.input))[:50000]
                except Exception as e:
                    out = f"Error: {e}"
                print(f"  [subagent] {b.name}: {out[:100]}")
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
        sub_msgs.append({"role": "user", "content": results})
    if resp:
        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    return "(subagent failed)"

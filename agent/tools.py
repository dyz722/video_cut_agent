# Harness: tool dispatch -- 加一个工具, 只加一个 handler. (port of s02)
"""
中央工具注册表: 基础工具 + 感知层(眼) + 行动层(手)。
agent loop 不感知任何具体工具, 只查 TOOL_HANDLERS。
"""

import subprocess

from . import config
from .todo import TODO
from .skills import SkillLoader
from .background import BG

SKILLS = SkillLoader(config.SKILLS_DIRS)


# === 基础工具 ===
def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=config.PROJECT_DIR,
                           capture_output=True, text=True, timeout=300)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (300s). Use background_run for slow commands."


def run_read(path: str, limit: int = None, offset: int = None) -> str:
    try:
        lines = config.safe_path(path).read_text().splitlines()
        total = len(lines)
        start = (offset or 0)
        end = start + limit if limit else total
        chunk = lines[start:end]
        suffix = []
        if end < total:
            suffix = [f"... ({total - end} more lines, use offset={end})"]
        return "\n".join(chunk + suffix)[:50000] or "(empty file)"
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = config.safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = config.safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# === 感知/行动层 handler (延迟导入, 模块独立可测) ===
def _probe(**kw):
    from perception.probe import probe_media
    return probe_media(kw["path"])


def _transcribe(**kw):
    from perception.transcribe import transcribe
    if kw.get("background"):
        path = kw["path"]
        return BG.run_fn(lambda: transcribe(path), label=f"transcribe {path}")
    return transcribe(kw["path"])


def _scenes(**kw):
    from perception.scenes import detect_scenes
    return detect_scenes(kw["path"], kw.get("threshold", 0.4))


def _watch(**kw):
    from perception.watch import watch_video
    return watch_video(kw["path"], kw["start"], kw["end"], kw["question"])


def _tts(**kw):
    from action.tts import synthesize
    return synthesize(kw["text"], kw["output"], kw.get("voice", "longanyang"),
                      kw.get("instruction"))


def _validate_timeline(**kw):
    from action.timeline import validate_file
    return validate_file(kw.get("path", "timeline.json"))


def _render(**kw):
    from action.render import render_request
    return render_request(kw.get("path", "timeline.json"),
                          background=kw.get("background", True))


def _qc(**kw):
    from action.qc import qc_check
    return qc_check(kw["path"], kw.get("sample_frames", 4))


def _subagent(**kw):
    from .subagent import run_subagent
    return run_subagent(kw["prompt"], kw.get("agent_type", "Analyze"))


def _record_experience(**kw):
    from .experience import record_experience
    result = record_experience(
        kw["scenario"],
        kw["lesson"],
        kw.get("user_feedback", ""),
        kw.get("artifacts", ""),
        kw.get("tags"),
    )
    SKILLS.reload()
    return result


TOOL_HANDLERS = {
    "bash":             lambda **kw: run_bash(kw["command"]),
    "read_file":        lambda **kw: run_read(kw["path"], kw.get("limit"), kw.get("offset")),
    "write_file":       lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":        lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "TodoWrite":        lambda **kw: TODO.update(kw["items"]),
    "load_skill":       lambda **kw: SKILLS.load(kw["name"]),
    "record_experience": _record_experience,
    "compress":         lambda **kw: "Compressing...",
    "task":             _subagent,
    "background_run":   lambda **kw: BG.run(kw["command"], kw.get("timeout", 1800),
                                            kw.get("label", "")),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
    "probe_media":      _probe,
    "transcribe":       _transcribe,
    "detect_scenes":    _scenes,
    "watch_video":      _watch,
    "tts":              _tts,
    "validate_timeline": _validate_timeline,
    "render_timeline":  _render,
    "qc_check":         _qc,
}

TOOLS = [
    {"name": "bash", "description":
        "Run a shell command in the project workspace (ffmpeg/ffprobe available). "
        "300s timeout; use background_run for slow jobs.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}},
                      "required": ["command"]}},
    {"name": "read_file", "description":
        "Read a file in the project workspace. Use offset/limit to page through large "
        "files like analysis/transcript.json -- never read it whole.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "limit": {"type": "integer"},
         "offset": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file in the project workspace.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text once in a file.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "old_text": {"type": "string"},
         "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "TodoWrite", "description":
        "Update the plan checklist. Use for every multi-clip production run.",
     "input_schema": {"type": "object", "properties": {"items": {"type": "array",
         "items": {"type": "object", "properties": {
             "content": {"type": "string"},
             "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
             "activeForm": {"type": "string"}},
             "required": ["content", "status", "activeForm"]}}}, "required": ["items"]}},
    {"name": "load_skill", "description":
        "Load a vertical editing strategy (赛道剪辑策略) or shared knowledge by name. "
        "ALWAYS load the matching skill before planning any edit.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
    {"name": "record_experience", "description":
        "Persist a reusable editing lesson as a learned skill after the user confirms "
        "a result, preference, or correction is useful. Store compact lessons only; "
        "do not store secrets, raw transcripts, customer data, or private file names.",
     "input_schema": {"type": "object", "properties": {
         "scenario": {"type": "string",
                      "description": "Scenario such as ecommerce-clip, manju-compilation, or a custom niche."},
         "lesson": {"type": "string",
                    "description": "Reusable rule/preference learned from the accepted result."},
         "user_feedback": {"type": "string",
                           "description": "Short user signal that made this worth remembering."},
         "artifacts": {"type": "string",
                       "description": "Optional generic references, e.g. timeline path or style names."},
         "tags": {"type": "array", "items": {"type": "string"}}},
         "required": ["scenario", "lesson"]}},
    {"name": "compress", "description": "Manually compress conversation context.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "task", "description":
        "Spawn an isolated analysis subagent (own clean context, returns summary only). "
        "Use for long-material segment analysis, e.g. 'analyze transcript 00:20-00:40 of "
        "live.mp4, list sellable hook moments with timestamps'.",
     "input_schema": {"type": "object", "properties": {
         "prompt": {"type": "string"},
         "agent_type": {"type": "string", "enum": ["Analyze", "general"]}},
         "required": ["prompt"]}},
    {"name": "background_run", "description":
        "Run a slow shell command in a background thread; you get notified on completion.",
     "input_schema": {"type": "object", "properties": {
         "command": {"type": "string"}, "timeout": {"type": "integer"},
         "label": {"type": "string"}}, "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status (all or by id).",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}}},
    # -- 感知层 --
    {"name": "probe_media", "description":
        "ffprobe metadata: duration, resolution, fps, codecs, audio channels.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "transcribe", "description":
        "ASR transcription (DashScope fun-asr) with sentence timestamps -> "
        "analysis/<name>.transcript.json. The primary index for finding cut points. "
        "Set background=true for videos longer than ~10 min.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "background": {"type": "boolean"}},
         "required": ["path"]}},
    {"name": "detect_scenes", "description":
        "Scene-change detection -> analysis/<name>.scenes.json. Natural cut-point "
        "candidates; snap your clip boundaries to these.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "threshold": {"type": "number"}},
         "required": ["path"]}},
    {"name": "watch_video", "description":
        "Look at a video segment with the VL model (qwen3-vl-plus): extracts frames "
        "from [start,end] seconds and answers your question about the visuals. "
        "Costs money -- use only when the transcript is not enough.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "start": {"type": "number"},
         "end": {"type": "number"}, "question": {"type": "string"}},
         "required": ["path", "start", "end", "question"]}},
    # -- 行动层 --
    {"name": "tts", "description":
        "Synthesize voiceover speech (cosyvoice-v3-flash) -> wav file. "
        "voice e.g. longanyang/longanhuan_v3. Optional Chinese instruction like "
        "'你正在进行广告促销，你说话的情感是happy。'",
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string"}, "output": {"type": "string"},
         "voice": {"type": "string"}, "instruction": {"type": "string"}},
         "required": ["text", "output"]}},
    {"name": "validate_timeline", "description":
        "Validate a timeline.json (schema, source files exist, timecodes legal) "
        "WITHOUT rendering. Always run before render_timeline.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "render_timeline", "description":
        "Render timeline.json into the final video via the deterministic ffmpeg "
        "renderer. Slow -> runs in background by default; you get notified. "
        "In interactive mode the human may be asked to approve first.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "background": {"type": "boolean"}}}},
    {"name": "qc_check", "description":
        "Quality-check a rendered video: duration/loudness/black-frame detection + "
        "sample frames saved to analysis/frames/. Follow up with watch_video on the "
        "output for visual self-review.",
     "input_schema": {"type": "object", "properties": {
         "path": {"type": "string"}, "sample_frames": {"type": "integer"}},
         "required": ["path"]}},
]

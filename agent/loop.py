# Harness: the agent loop -- one loop is all you need. (port of s01, 永不改动)
"""
agent_loop: while + stop_reason。
每轮 LLM 调用前: microcompact -> auto_compact -> drain 后台通知。
工具执行后: todo nag 提醒。
"""

from contextlib import contextmanager
import itertools
import sys
import threading
import time
import uuid

from . import config
from .compact import estimate_tokens, microcompact, auto_compact
from .background import BG
from .events import EVENTS
from . import log_store
from .todo import TODO
from .tools import TOOLS, TOOL_HANDLERS, SKILLS

VERBOSE_TOOLS = False
TOOL_LOGS = []
MAX_TOOL_LOGS = 80

SYSTEM_TEMPLATE = """You are a video editing agent (剪辑 agent) working in project workspace: {workdir}

Workspace convention:
  materials/        原始素材 (源视频/图片/BGM)
  analysis/         感知产物: *.transcript.json / *.scenes.json / frames/
  timeline*.json    剪辑决策清单 (你的核心产出, 一条成片一份 timeline)
  output/           渲染成片

Standard workflow (follow unless the user says otherwise):
  1. 建索引: probe_media -> transcribe (>10min 用 background=true) -> detect_scenes
  2. load_skill 加载匹配赛道的剪辑策略 (规划任何剪辑前必须加载); 写 timeline 前先
     load_skill("timeline-format") 掌握格式规范; 如果存在 learned-* 经验 skill,
     同时加载最匹配的一份
  3. TodoWrite 列出出片计划 (每条成片一个 todo)
  4. 写 timeline_<n>.json -> validate_timeline 自检
  5. review_timeline 生成 HTML 审核页, 让用户确认/修改; 如用户保存了
     timeline.reviewed.json, 后续以修订版为准
  6. render_timeline 渲染 (自动走后台, 等通知)
  7. qc_check 自检 -> review_render 生成成片审核页, 让用户确认/标注问题;
     不合格改 timeline 重渲染
  8. 用户确认满意或修改形成稳定偏好后, 先 summarize_review_feedback
     产出候选经验, 经用户确认后再 record_experience

Rules:
- transcript/scenes JSON 用 read_file 的 offset/limit 或 bash grep 查询, 严禁整文件读入。
- watch_video 有成本, 只在转写稿无法判断画面时使用。
- 超长素材(>30min)用 task 派 subagent 分段分析, 只回收结构化摘要。
- 剪辑判断力 (什么是钩子/节奏/片长/字幕样式) 以加载的赛道 skill 为准, 不要凭空发挥。
- 渲染和长转写丢后台后, 可以继续规划下一条片子, 不要干等。
- 交互模式下, 渲染前默认使用 review_timeline 给用户做可视化确认; 用户明确
  要跳过时才直接渲染。
- 成片交付前默认使用 review_render 做可视化验收; 用户明确要跳过时才直接交付。
- 用户确认满意、反复修正出稳定偏好、或一次 QC/返工形成可复用经验后,
  先调用 summarize_review_feedback 生成候选经验; 用户确认候选可复用后,
  再调用 record_experience 或 summarize_review_feedback(record_confirmed=true)。
  只记录可复用剪辑判断, 不记录密钥、客户隐私、原始转写大段文本或私有素材文件名。

Skills available (load_skill):
{skills}"""


def build_system() -> str:
    return SYSTEM_TEMPLATE.format(workdir=config.PROJECT_DIR,
                                  skills=SKILLS.descriptions())


def _status_enabled() -> bool:
    return sys.stdout.isatty()


@contextmanager
def status(title: str):
    """Show a lightweight live status while blocking work is running."""
    EVENTS.emit("status", title)
    if not _status_enabled() or threading.current_thread() is not threading.main_thread():
        yield
        return

    done = threading.Event()

    def run():
        for frame in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            if done.is_set():
                break
            sys.stdout.write(f"\r\033[2K{frame} {title}")
            sys.stdout.flush()
            time.sleep(0.12)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(timeout=0.3)
        sys.stdout.write(f"\r\033[2K✓ {title}\n")
        sys.stdout.flush()


def _shorten(text: str, limit: int = 180) -> str:
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _block_type(block) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _block_text(block) -> str:
    if isinstance(block, dict):
        return str(block.get("text", ""))
    return str(getattr(block, "text", ""))


def assistant_text(content: list) -> str:
    parts = []
    for block in content:
        if _block_type(block) == "text":
            parts.append(_block_text(block))
    return "\n".join(p.strip() for p in parts if p.strip())


def summarize_tool_intent(name: str, input_data: dict) -> str:
    if name == "bash":
        return _shorten(input_data.get("command", ""), 220)
    if name == "read_file":
        path = input_data.get("path", "-")
        limit = input_data.get("limit")
        offset = input_data.get("offset")
        window = []
        if offset is not None:
            window.append(f"offset={offset}")
        if limit is not None:
            window.append(f"limit={limit}")
        suffix = f" ({', '.join(window)})" if window else ""
        return f"read {path}{suffix}"
    if name == "write_file":
        return f"write {input_data.get('path', '-')}"
    if name == "edit_file":
        return f"edit {input_data.get('path', '-')}"
    if name == "load_skill":
        return f"load editing knowledge: {input_data.get('name', '-')}"
    if name == "TodoWrite":
        return f"update plan with {len(input_data.get('items', []))} items"
    if name == "render_timeline":
        approved = "approved" if input_data.get("approved") else "needs approval"
        return f"render {input_data.get('path', '-')} ({approved})"
    if name in ("probe_media", "transcribe", "detect_scenes",
                "validate_timeline", "qc_check", "review_timeline", "review_render"):
        return f"{name} {input_data.get('path', '-')}"
    if name == "watch_video":
        return (f"inspect {input_data.get('path', '-')} "
                f"{input_data.get('start', '?')}-{input_data.get('end', '?')}s")
    if name == "task":
        return _shorten(input_data.get("prompt", ""), 220)
    return _shorten(input_data, 220)


def summarize_tool_result(name: str, input_data: dict, output: str) -> str:
    output = str(output)
    if name == "load_skill":
        return f"loaded skill: {input_data.get('name', '-')}"
    if name == "bash":
        lines = len(output.splitlines()) if output else 0
        return f"bash completed ({lines} lines hidden)"
    if name == "read_file":
        return f"read {input_data.get('path', '-')} ({len(output.splitlines())} lines hidden)"
    if name == "TodoWrite":
        return "plan updated"
    if name == "review_timeline":
        return "timeline review page ready"
    if name == "review_render":
        return "render review page ready"
    if name == "render_timeline":
        return _shorten(output, 140)
    if name == "qc_check":
        issue_line = next((l for l in output.splitlines() if l.startswith("issues:")), "")
        return issue_line or "qc completed"
    if output.startswith("Error:"):
        return _shorten(output, 180)
    return f"{name} completed"


def record_tool_log(name: str, input_data: dict, output: str):
    entry = {
        "name": name,
        "input": input_data,
        "input_summary": _shorten(input_data, 240),
        "output": str(output),
        "summary": summarize_tool_result(name, input_data, str(output)),
        "ts": time.strftime("%H:%M:%S"),
        "run_id": EVENTS.current_run.get("run_id", ""),
    }
    TOOL_LOGS.append(entry)
    del TOOL_LOGS[:-MAX_TOOL_LOGS]
    log_store.append_jsonl(log_store.TOOL_LOG, entry)
    return entry


def render_tool_logs(full: bool = False, limit: int = 12) -> str:
    if not TOOL_LOGS:
        return "No tool logs yet."
    rows = []
    recent = TOOL_LOGS[-limit:]
    for idx, item in enumerate(recent, start=max(len(TOOL_LOGS) - len(recent) + 1, 1)):
        rows.append(f"{idx}. [{item['ts']}] {item['name']}: {item['summary']}")
        if full:
            rows.append(f"   input: {item['input_summary']}")
            rows.append("   output:")
            rows.append("   " + _shorten(item["output"], 1200).replace("\n", "\n   "))
    return "\n".join(rows)


def clear_tool_logs():
    TOOL_LOGS.clear()
    log_store.clear_jsonl(log_store.TOOL_LOG)


def set_verbose_tools(enabled: bool):
    global VERBOSE_TOOLS
    VERBOSE_TOOLS = bool(enabled)


def _cancel_requested(cancel_event) -> bool:
    return bool(cancel_event and cancel_event.is_set())


def _cancel_tool_result(block) -> dict:
    return {"type": "tool_result", "tool_use_id": block.id,
            "content": "Cancelled by user before this tool was executed."}


def agent_loop(messages: list, verbose: bool | None = None, cancel_event=None,
               run_id: str | None = None):
    if verbose is None:
        verbose = VERBOSE_TOOLS
    own_run = not EVENTS.current_run.get("running")
    run_id = run_id or EVENTS.current_run.get("run_id") or str(uuid.uuid4())[:8]
    if own_run:
        EVENTS.start_run(run_id)
    rounds_without_todo = 0
    try:
        while True:
            if _cancel_requested(cancel_event):
                EVENTS.emit("issue", "agent run stopped before the next model call")
                return
            microcompact(messages)
            if estimate_tokens(messages) > config.TOKEN_THRESHOLD:
                EVENTS.emit("status", "auto compact triggered")
                messages[:] = auto_compact(messages)
            notifs = BG.drain()
            if notifs:
                txt = "\n".join(f"[bg:{n['task_id']}] {n.get('label', '')} {n['status']}: "
                                f"{n['result']}" for n in notifs)
                for n in notifs:
                    EVENTS.emit("bg", f"{n.get('label', '')} {n['status']}: "
                                f"{_shorten(n['result'], 180)}")
                messages.append({"role": "user",
                                 "content": f"<background-results>\n{txt}\n</background-results>"})
            with status(f"thinking with {config.main_model()} ({config.main_model_protocol()})"):
                response = config.client().messages.create(
                    model=config.main_model(), system=build_system(),
                    messages=messages, tools=TOOLS, max_tokens=8000)
            messages.append({"role": "assistant", "content": response.content})

            text = assistant_text(response.content)
            if text and response.stop_reason == "tool_use":
                EVENTS.emit("plan", _shorten(text, 260))

            if response.stop_reason != "tool_use":
                # 后台还有活且 agent 停了 -> 批处理模式下等通知再继续
                if config.AUTO_MODE and BG.has_running():
                    wait_title = "waiting for background tasks"
                    EVENTS.emit("status", wait_title)
                    while BG.has_running() and BG.notifications.empty():
                        if _cancel_requested(cancel_event):
                            EVENTS.emit("issue", "agent run stopped while waiting for background tasks")
                            return
                        with status(wait_title):
                            time.sleep(2)
                    continue
                return

            results = []
            used_todo = False
            manual_compress = False
            for block in response.content:
                if block.type == "tool_use":
                    if _cancel_requested(cancel_event):
                        EVENTS.emit("issue", f"cancelled before tool: {block.name}")
                        results.append(_cancel_tool_result(block))
                        continue
                    if block.name == "compress":
                        manual_compress = True
                    handler = TOOL_HANDLERS.get(block.name)
                    EVENTS.emit("tool", summarize_tool_intent(block.name, block.input),
                                name=block.name)
                    try:
                        with status(f"running tool: {block.name}"):
                            output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                    except Exception as e:
                        output = f"Error: {type(e).__name__}: {e}"
                    log_entry = record_tool_log(block.name, block.input, output)
                    event_kind = "issue" if str(output).startswith(("Error:", "INVALID",
                                                                    "HUMAN REJECTED")) else "obs"
                    EVENTS.emit(event_kind, log_entry["summary"], name=block.name)
                    if verbose:
                        print(f"\033[33m> {block.name}\033[0m {str(block.input)[:160]}")
                        print(f"  {str(output)[:240]}")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": str(output)})
                    if block.name == "TodoWrite":
                        used_todo = True
            rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
            if TODO.has_open_items() and rounds_without_todo >= 3:
                EVENTS.emit("status", "open todos detected; reminding agent to update plan")
                results.append({"type": "text",
                                "text": "<reminder>Update your todos.</reminder>"})
            messages.append({"role": "user", "content": results})
            if manual_compress:
                EVENTS.emit("status", "manual compact requested")
                messages[:] = auto_compact(messages)
                return
    finally:
        if own_run:
            EVENTS.finish_run("stopped" if _cancel_requested(cancel_event) else "idle")

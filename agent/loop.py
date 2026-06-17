# Harness: the agent loop -- one loop is all you need. (port of s01, 永不改动)
"""
agent_loop: while + stop_reason。
每轮 LLM 调用前: microcompact -> auto_compact -> drain 后台通知。
工具执行后: todo nag 提醒。
"""

import time

from . import config
from .compact import estimate_tokens, microcompact, auto_compact
from .background import BG
from .todo import TODO
from .tools import TOOLS, TOOL_HANDLERS, SKILLS

SYSTEM_TEMPLATE = """You are a video editing agent (剪辑 agent) working in project workspace: {workdir}

Workspace convention:
  materials/        原始素材 (源视频/图片/BGM)
  analysis/         感知产物: *.transcript.json / *.scenes.json / frames/
  timeline*.json    剪辑决策清单 (你的核心产出, 一条成片一份 timeline)
  output/           渲染成片

Standard workflow (follow unless the user says otherwise):
  1. 建索引: probe_media -> transcribe (>10min 用 background=true) -> detect_scenes
  2. load_skill 加载匹配赛道的剪辑策略 (规划任何剪辑前必须加载); 写 timeline 前先
     load_skill("timeline-format") 掌握格式规范
  3. TodoWrite 列出出片计划 (每条成片一个 todo)
  4. 写 timeline_<n>.json -> validate_timeline 自检
  5. render_timeline 渲染 (自动走后台, 等通知)
  6. qc_check + watch_video 抽查成片; 不合格改 timeline 重渲染

Rules:
- transcript/scenes JSON 用 read_file 的 offset/limit 或 bash grep 查询, 严禁整文件读入。
- watch_video 有成本, 只在转写稿无法判断画面时使用。
- 超长素材(>30min)用 task 派 subagent 分段分析, 只回收结构化摘要。
- 剪辑判断力 (什么是钩子/节奏/片长/字幕样式) 以加载的赛道 skill 为准, 不要凭空发挥。
- 渲染和长转写丢后台后, 可以继续规划下一条片子, 不要干等。

Skills available (load_skill):
{skills}"""


def build_system() -> str:
    return SYSTEM_TEMPLATE.format(workdir=config.PROJECT_DIR,
                                  skills=SKILLS.descriptions())


def agent_loop(messages: list, verbose: bool = True):
    rounds_without_todo = 0
    while True:
        microcompact(messages)
        if estimate_tokens(messages) > config.TOKEN_THRESHOLD:
            print("[auto-compact triggered]")
            messages[:] = auto_compact(messages)
        notifs = BG.drain()
        if notifs:
            txt = "\n".join(f"[bg:{n['task_id']}] {n.get('label', '')} {n['status']}: "
                            f"{n['result']}" for n in notifs)
            messages.append({"role": "user",
                             "content": f"<background-results>\n{txt}\n</background-results>"})
        response = config.client().messages.create(
            model=config.main_model(), system=build_system(),
            messages=messages, tools=TOOLS, max_tokens=8000)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            # 后台还有活且 agent 停了 -> 批处理模式下等通知再继续
            if config.AUTO_MODE and BG.has_running():
                print("[waiting for background tasks...]")
                while BG.has_running() and BG.notifications.empty():
                    time.sleep(2)
                continue
            return

        results = []
        used_todo = False
        manual_compress = False
        for block in response.content:
            if block.type == "tool_use":
                if block.name == "compress":
                    manual_compress = True
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                except Exception as e:
                    output = f"Error: {type(e).__name__}: {e}"
                if verbose:
                    print(f"\033[33m> {block.name}\033[0m {str(block.input)[:160]}")
                    print(f"  {str(output)[:240]}")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": str(output)})
                if block.name == "TodoWrite":
                    used_todo = True
        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if TODO.has_open_items() and rounds_without_todo >= 3:
            results.append({"type": "text",
                            "text": "<reminder>Update your todos.</reminder>"})
        messages.append({"role": "user", "content": results})
        if manual_compress:
            print("[manual compact]")
            messages[:] = auto_compact(messages)
            return

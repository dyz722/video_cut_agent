#!/usr/bin/env python3
"""
video-agent CLI

交互模式:
    veoai                                      # REPL, 使用当前目录作为工作目录
    veoai <project>                            # 使用当前目录下的 project/ 作为工作目录
    veoai <project> --materials ~/视频/         # 把素材链接进项目 materials/
    veoai update                               # 从 GitHub 更新到最新版本

批处理模式:
    python main.py <project> --batch "把这场直播切出10条带货短视频" --auto
    (--auto: 渲染不需要人工确认, 全自动出片 + 质检报告)
"""

import argparse
import atexit
from contextlib import contextmanager
import os
import signal
import subprocess
import sys
import threading
import textwrap
import shutil
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import config  # noqa: E402

DEFAULT_REPO_URL = "https://github.com/dyz722/video_cut_agent.git"
SLASH_COMMANDS = [
    "/model", "/dashscope", "/resume", "/todos", "/bg", "/compact",
    "/logs", "/logview", "/live", "/status", "/stop", "/verbose", "/help", "/quit",
]
_READLINE = None
_HISTORY_REGISTERED = False


def _pkg_version() -> str:
    try:
        return version("video-cut-agent")
    except PackageNotFoundError:
        return "dev"


def _color(text: str, code: str) -> str:
    if os.getenv("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def _clip(text: str, width: int) -> str:
    text = str(text)
    return text if len(text) <= width else text[:max(width - 1, 0)] + "…"


def _box(title: str, lines: list[str], width: int) -> str:
    inner = max(width - 4, 20)
    out = [
        "+" + "-" * (width - 2) + "+",
        "| " + _clip(title, inner).ljust(inner) + " |",
        "+" + "-" * (width - 2) + "+",
    ]
    for line in lines:
        wrapped = textwrap.wrap(str(line), inner) or [""]
        for item in wrapped:
            out.append("| " + _clip(item, inner).ljust(inner) + " |")
    out.append("+" + "-" * (width - 2) + "+")
    return "\n".join(out)


def welcome_screen() -> str:
    cols = shutil.get_terminal_size((100, 24)).columns
    width = min(max(cols - 4, 72), 108)
    project = str(config.PROJECT_DIR)
    proto = config.main_model_protocol()
    model = config.main_model()
    left_lines = [
        "Welcome back!",
        "",
        "        __",
        "   ____/ /__  ___  ___ ____ _",
        "  / __/ / _ \\/ _ \\/ _ `/  ' \\",
        "  \\__/_/\\___/_//_/\\_,_/_/_/_/",
        "",
        f"model: {model}",
        f"protocol: {proto}",
        f"project: {project}",
    ]
    right_lines = [
        "Tips for getting started",
        "1. Put source videos in materials/ or run with --materials.",
        "2. Ask for a clip; veoai will create timeline JSON first.",
        "3. Review timeline/render in the generated HTML pages.",
        "",
        "Shortcuts",
        "/model   switch model provider",
        "/dashscope switch cn/intl DashScope",
        "/resume  resume project session",
        "/todos   show current plan",
        "/bg      check background jobs",
        "/compact compress context",
        "/logs    inspect hidden tool logs",
        "/logview open web log viewer",
        "/live    show live agent events",
        "/status  show current run status",
        "/stop    stop current run",
        "Ctrl-C   request current run stop",
        "Tab      complete slash commands",
        "Up/Down  browse prompt history",
        "/quit    exit",
    ]
    panel_w = (width - 3) // 2
    left = _box(f"veoai v{_pkg_version()}", left_lines, panel_w).splitlines()
    right = _box("Guide", right_lines, width - panel_w - 3).splitlines()
    height = max(len(left), len(right))
    left += [" " * panel_w] * (height - len(left))
    right += [" " * (width - panel_w - 3)] * (height - len(right))
    body = "\n".join(l + "   " + r for l, r in zip(left, right))
    footer = "\n" + _color('Try "把 materials 里的素材剪一条带货短视频"', "2") + "\n" + \
        _color("? for shortcuts", "2")
    return _color(body, "38;5;209") + footer


def command_help() -> str:
    return "\n".join([
        "commands:",
        "  /model     切换主模型协议、Base URL、API key 和模型 ID",
        "  /dashscope 配置/切换 DashScope 国内或海外 endpoint/key",
        "  /resume    查看当前项目下可恢复的会话并选择恢复",
        "  /resume <序号或ID> 恢复某个会话, 上下文不与其他会话交叉",
        "  /todos     查看当前剪辑计划",
        "  /bg        查看后台转写/渲染任务",
        "  /compact   手动压缩上下文",
        "  /logs      查看最近工具调用摘要",
        "  /logs full 展开最近工具输入/输出",
        "  /logs clear 清空工具日志",
        "  /logview   打开本地 Web 日志查看器",
        "  /live      查看最近 agent 运行事件",
        "  /status    查看当前运行状态",
        "  /stop      请求停止当前运行中的 agent",
        "  /verbose on|off 切换详细工具输出",
        "  /quit      退出",
        "  Ctrl-C     运行中请求停止当前 agent, 空闲时退出输入",
        "  Tab        补全斜杠命令, 例如 /m + Tab -> /model",
        "  ↑ / ↓      找回上一条/下一条输入, 可编辑后快速重发",
        "  ? or /help 显示此帮助",
    ])


def prompt_status() -> str:
    return f" {config.main_model()} · {config.PROJECT_DIR} "


def complete_slash_command(text: str, state: int):
    matches = [cmd for cmd in SLASH_COMMANDS if cmd.startswith(text)]
    return matches[state] if state < len(matches) else None


def _slash_matches(text: str) -> list[str]:
    return [cmd for cmd in SLASH_COMMANDS if cmd.startswith(text)]


def create_prompt_session():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style
    except Exception:
        return None

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            for cmd in _slash_matches(text):
                yield Completion(cmd, start_position=-len(text))

    history_file = repl_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)
    style = Style.from_dict({
        "prompt": "ansicyan bold",
        "toolbar": "reverse ansigreen",
    })
    return PromptSession(
        [("class:prompt", "› ")],
        completer=SlashCompleter(),
        history=FileHistory(str(history_file)),
        complete_while_typing=False,
        bottom_toolbar=lambda: HTML(
            f'<style bg="ansiblack" fg="ansigreen"> {prompt_status()} </style>'),
        style=style,
    )


def _readline_module():
    global _READLINE
    if _READLINE is not None:
        return _READLINE
    try:
        import readline
    except Exception:
        return None
    _READLINE = readline
    return _READLINE


def repl_history_path() -> Path:
    return config.USER_DATA_DIR / "history"


def _save_readline_history(history_file: Path | None = None):
    readline = _readline_module()
    if not readline:
        return
    history_file = history_file or repl_history_path()
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        readline.write_history_file(str(history_file))
    except Exception:
        pass


def setup_readline_completion() -> bool:
    global _HISTORY_REGISTERED
    readline = _readline_module()
    if not readline:
        return False
    try:
        readline.set_completer(complete_slash_command)
        readline.set_completer_delims(readline.get_completer_delims().replace("/", ""))
        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("bind ^I rl_complete")  # libedit on macOS
        history_file = repl_history_path()
        history_file.parent.mkdir(parents=True, exist_ok=True)
        if history_file.exists():
            readline.read_history_file(str(history_file))
        readline.set_history_length(int(os.getenv("VEOAI_HISTORY_LIMIT", "1000")))
        if not _HISTORY_REGISTERED:
            atexit.register(_save_readline_history, history_file)
            _HISTORY_REGISTERED = True
        return True
    except Exception:
        return False


def add_repl_history(line: str) -> bool:
    readline = _readline_module()
    line = (line or "").strip()
    if not readline or not line:
        return False
    try:
        length = readline.get_current_history_length()
        last = readline.get_history_item(length) if length else None
        if last != line:
            readline.add_history(line)
        _save_readline_history()
        return True
    except Exception:
        return False


@contextmanager
def esc_interrupt_monitor():
    """Turn Esc into KeyboardInterrupt while the agent is running."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        yield
        return
    try:
        import select
        import termios
        import tty
    except Exception:
        yield
        return

    fd = sys.stdin.fileno()
    old_attrs = None
    stop = threading.Event()

    def watch():
        try:
            while not stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    continue
                ch = os.read(fd, 1)
                if ch == b"\x1b":
                    sys.stdout.write("\n[interrupt] Esc pressed. Stopping current run...\n")
                    sys.stdout.flush()
                    os.kill(os.getpid(), signal.SIGINT)
                    return
        except Exception:
            return

    try:
        old_attrs = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        yield
    finally:
        stop.set()
        if old_attrs is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
            except Exception:
                pass


def format_cli_error(exc: Exception) -> str:
    msg = str(exc)
    hints = []
    if "OpenAI-compatible API error" in msg or "Anthropic" in type(exc).__name__:
        hints.extend([
            "主模型 API 调用失败，veoai 已保持在当前会话中，没有退出。",
            "你可以输入 /model 切换协议、Base URL、API key 或模型 ID 后重试。",
        ])
    if "502" in msg or "Upstream" in msg or "forbidden" in msg.lower():
        hints.append("这通常是三方网关上游不可用、模型无权限或管理员限制，不是剪辑流程本身的问题。")
    if not hints:
        hints.append("发生未处理异常，veoai 已拦截并保持 REPL 继续运行。")
    return "\n".join([
        _color("[error] " + type(exc).__name__, "31"),
        _clip(msg, 1800),
        "",
        *("- " + h for h in hints),
    ])


def link_materials(src: str):
    src_path = Path(src).expanduser().resolve()
    dest = config.PROJECT_DIR / "materials"
    files = [src_path] if src_path.is_file() else sorted(
        p for p in src_path.iterdir()
        if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv",
                                ".mp3", ".wav", ".m4a", ".aac", ".png", ".jpg",
                                ".jpeg", ".srt", ".ass"))
    for f in files:
        target = dest / f.name
        if not target.exists():
            target.symlink_to(f)
            print(f"[素材] linked {f.name}")
    if not files:
        print(f"[素材] {src_path} 下没有可识别的媒体文件")


def repl():
    from agent.loop import agent_loop
    from agent import loop as loop_state
    from agent import session as session_store
    from agent.events import EVENTS
    from agent.log_view import open_log_view
    from agent.todo import TODO
    from agent.background import BG
    from agent.compact import auto_compact

    prompt_session = create_prompt_session()
    completion_enabled = bool(prompt_session) or setup_readline_completion()
    print(welcome_screen())
    if prompt_session:
        print(_color("Prompt UI enabled: /m + Tab, ↑ for history", "2"))
    elif completion_enabled:
        print(_color("Tab completion and history enabled: try /m + Tab, or ↑ for history", "2"))
    session_id = session_store.new_session()
    session_name = "new session"
    history = []
    run_thread = None
    cancel_event = None
    print(_color(f"session: {session_id} · use /resume to switch", "2"))

    def is_running() -> bool:
        return bool(run_thread and run_thread.is_alive())

    def print_latest_assistant():
        if not history:
            return
        content = history[-1].get("content") if isinstance(history[-1], dict) else None
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "text") and block.text:
                    print(block.text)
                elif isinstance(block, dict) and block.get("type") == "text":
                    print(block.get("text", ""))

    def start_agent_run(query: str):
        nonlocal run_thread, cancel_event, session_name
        cancel_event = threading.Event()
        run_id = f"{session_id[-6:]}-{len(history) + 1}"
        history.append({"role": "user", "content": query})
        session_store.save_session(session_id, history, session_name)

        def worker():
            nonlocal session_name
            EVENTS.start_run(run_id)
            try:
                agent_loop(history, cancel_event=cancel_event, run_id=run_id)
            except Exception as e:
                session_store.save_session(session_id, history, session_name)
                EVENTS.emit("issue", f"{type(e).__name__}: {_clip(str(e), 240)}")
                print(format_cli_error(e))
                EVENTS.finish_run("error")
                return
            if session_name == "new session":
                session_name = session_store.session_title(history)
            session_store.save_session(session_id, history, session_name)
            EVENTS.finish_run("stopped" if cancel_event.is_set() else "idle")
            if cancel_event.is_set():
                print("[stopped - 上下文已保留，输入新指令继续]")
            else:
                print_latest_assistant()
                print()

        run_thread = threading.Thread(target=worker, daemon=True)
        run_thread.start()
        print(_color("[agent] run started. Use /status, /live, /logs, /bg, /todos, or /stop.", "2"))

    def resume_session(resolved: str):
        nonlocal session_id, session_name, history
        data = session_store.load_session(resolved)
        session_id = resolved
        session_name = data.get("title") or "resumed session"
        history = data.get("messages", [])
        print(f"[session] resumed {session_name} ({session_id}), "
              f"{len(history)} messages")
        print(_color("---- conversation history ----", "2"))
        print(session_store.render_conversation(history))
        print(_color("---- continue typing to resume ----", "2"))

    def choose_resume_ref() -> str:
        sessions = session_store.list_sessions()
        if not sessions:
            print("No sessions in this project yet.")
            return ""
        print(session_store.render_sessions())
        value = _safe_input("Resume session number or ID (Enter to cancel): ")
        if value.lower() in ("q", "quit", "cancel"):
            return ""
        return value

    while True:
        try:
            if prompt_session:
                query = prompt_session.prompt()
            else:
                prompt = "\033[36mveoai* >> \033[0m" if is_running() else "\033[36mveoai >> \033[0m"
                query = input(prompt)
        except (EOFError, KeyboardInterrupt):
            if is_running() and cancel_event:
                cancel_event.set()
                print(EVENTS.request_stop())
                continue
            break
        q = query.strip()
        if q.lower() in ("q", "quit", "exit", "/quit", ""):
            if q == "":
                continue
            if is_running() and cancel_event:
                cancel_event.set()
                print(EVENTS.request_stop())
                run_thread.join()
            break
        if not prompt_session:
            add_repl_history(query)
        if q in ("/help", "?", "/"):
            print(command_help()); continue
        if q == "/status":
            print(EVENTS.status_text()); continue
        if q.startswith("/live"):
            parts = q.split()
            limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
            print(EVENTS.render(limit=limit)); continue
        if q == "/stop":
            if not is_running() or not cancel_event:
                print("No agent run is active.")
            else:
                cancel_event.set()
                print(EVENTS.request_stop())
            continue
        if q == "/todos":
            print(TODO.render()); continue
        if q == "/bg":
            print(BG.check()); continue
        if q.startswith("/resume"):
            if is_running():
                print("Agent is running. Use /stop before switching sessions.")
                continue
            parts = q.split(maxsplit=1)
            if len(parts) == 1:
                ref = choose_resume_ref()
                if not ref:
                    continue
            else:
                ref = parts[1]
            resolved = session_store.resolve_session(ref)
            if not resolved:
                print("Session not found. Use /resume to list sessions.")
                continue
            resume_session(resolved)
            continue
        if q.startswith("/logs"):
            parts = q.split()
            if len(parts) > 1 and parts[1] == "clear":
                loop_state.clear_tool_logs()
                print("[logs] cleared")
            else:
                print(loop_state.render_tool_logs(full=(len(parts) > 1 and parts[1] == "full")))
            continue
        if q == "/logview":
            print(open_log_view(open_browser=True))
            continue
        if q.startswith("/verbose"):
            parts = q.split()
            if len(parts) == 1:
                print(f"[verbose] {'on' if loop_state.VERBOSE_TOOLS else 'off'}")
            elif parts[1] in ("on", "true", "1"):
                loop_state.set_verbose_tools(True)
                print("[verbose] on")
            elif parts[1] in ("off", "false", "0"):
                loop_state.set_verbose_tools(False)
                print("[verbose] off")
            else:
                print("usage: /verbose on|off")
            continue
        if q == "/model":
            if is_running():
                print("Agent is running. Use /stop before changing the model.")
                continue
            config.configure_main_model(force=True)
            print(f"[model] current: {config.main_model()}")
            continue
        if q == "/dashscope":
            if is_running():
                print("Agent is running. Use /stop before changing DashScope config.")
                continue
            config.configure_dashscope(force=True)
            print(f"[dashscope] region: {config.dashscope_region()} | "
                  f"base_url: {config.dashscope_base_url()}")
            continue
        if q == "/compact":
            if is_running():
                print("Agent is running. Use /stop before compacting context.")
                continue
            if history:
                history[:] = auto_compact(history)
                print("[compacted]")
            continue
        if is_running():
            print("Agent is running. Use /status, /live, /logs, /bg, /todos, or /stop.")
            continue
        start_agent_run(query)


def batch(task: str):
    from agent.loop import agent_loop
    history = [{"role": "user", "content":
                f"{task}\n\n(批处理模式: 按标准工作流自主完成全部出片, 不要向人提问。"
                f"完成后输出: 每条成片的路径 + 质检结论汇总。)"}]
    try:
        agent_loop(history)
    except Exception as e:
        print(format_cli_error(e))
        return 1
    content = history[-1]["content"]
    if isinstance(content, list):
        for block in content:
            if hasattr(block, "text"):
                print(block.text)
    return 0


def update_self(repo: str = DEFAULT_REPO_URL, dry_run: bool = False) -> int:
    """Upgrade the installed package from GitHub using the current Python."""
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        f"git+{repo}",
    ]
    print("[update] 将执行:")
    print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
    if dry_run:
        return 0
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print("[update] veoai 已更新到 GitHub 最新版本。")
    else:
        print("[update] 更新失败。可尝试手动运行上面的命令。")
    return result.returncode


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "update":
        up = argparse.ArgumentParser(description="Update veoai from GitHub.")
        up.add_argument("--repo", default=DEFAULT_REPO_URL,
                        help="Git repository URL to install from.")
        up.add_argument("--dry-run", action="store_true",
                        help="Print the update command without running it.")
        args = up.parse_args(argv[1:])
        return update_self(args.repo, args.dry_run)

    ap = argparse.ArgumentParser(
        description="veoai: 视频剪辑 agent",
        epilog="special commands:\n  veoai update [--dry-run]    从 GitHub 更新到最新版本",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument("project", nargs="?", default=".",
                    help="项目目录, 默认当前目录; 相对路径基于启动 veoai 的目录")
    ap.add_argument("--materials", help="素材文件/目录, 链接进项目 materials/")
    ap.add_argument("--batch", help="批处理任务描述, 非交互执行")
    ap.add_argument("--auto", action="store_true",
                    help="渲染免人工审批 (--batch 时默认开启)")
    args = ap.parse_args(argv)

    config.ensure_config()
    config.set_project(args.project)
    config.AUTO_MODE = args.auto or bool(args.batch)

    if args.materials:
        link_materials(args.materials)
    if args.batch:
        return batch(args.batch)
    else:
        repl()
    return 0


if __name__ == "__main__":
    sys.exit(main())

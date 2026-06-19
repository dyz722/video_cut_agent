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
import os
import subprocess
import sys
import textwrap
import shutil
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import config  # noqa: E402

DEFAULT_REPO_URL = "https://github.com/dyz722/video_cut_agent.git"


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
        "/todos   show current plan",
        "/bg      check background jobs",
        "/compact compress context",
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
        "  /todos     查看当前剪辑计划",
        "  /bg        查看后台转写/渲染任务",
        "  /compact   手动压缩上下文",
        "  /quit      退出",
        "  ? or /help 显示此帮助",
    ])


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
    from agent.todo import TODO
    from agent.background import BG
    from agent.compact import auto_compact

    print(welcome_screen())
    history = []
    while True:
        try:
            query = input("\033[36mveoai >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        q = query.strip()
        if q.lower() in ("q", "quit", "exit", "/quit", ""):
            if q == "":
                continue
            break
        if q in ("/help", "?"):
            print(command_help()); continue
        if q == "/todos":
            print(TODO.render()); continue
        if q == "/bg":
            print(BG.check()); continue
        if q == "/model":
            config.configure_main_model(force=True)
            print(f"[model] current: {config.main_model()}")
            continue
        if q == "/compact":
            if history:
                history[:] = auto_compact(history)
                print("[compacted]")
            continue
        history.append({"role": "user", "content": query})
        try:
            agent_loop(history)
        except KeyboardInterrupt:
            print("\n[interrupted - 输入新指令继续]")
            continue
        except Exception as e:
            print(format_cli_error(e))
            continue
        content = history[-1]["content"]
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "text"):
                    print(block.text)
        print()


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

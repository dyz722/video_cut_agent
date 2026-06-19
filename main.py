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
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import config  # noqa: E402

DEFAULT_REPO_URL = "https://github.com/dyz722/video_cut_agent.git"


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

    print(f"veoai | project: {config.PROJECT_DIR.name} | "
          f"model: {config.main_model()}")
    print("commands: /model /todos /bg /compact /quit\n")
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
    agent_loop(history)
    content = history[-1]["content"]
    if isinstance(content, list):
        for block in content:
            if hasattr(block, "text"):
                print(block.text)


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

    ap = argparse.ArgumentParser(description="veoai: 视频剪辑 agent")
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
        batch(args.batch)
    else:
        repl()
    return 0


if __name__ == "__main__":
    sys.exit(main())

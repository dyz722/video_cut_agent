#!/usr/bin/env python3
"""
video-agent CLI

交互模式:
    python main.py <project>                      # REPL, timeline 渲染前人工审批
    python main.py <project> --materials ~/视频/   # 把素材链接进项目 materials/

批处理模式:
    python main.py <project> --batch "把这场直播切出10条带货短视频" --auto
    (--auto: 渲染不需要人工确认, 全自动出片 + 质检报告)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import config  # noqa: E402


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

    print(f"video-agent | project: {config.PROJECT_DIR.name} | "
          f"model: {config.main_model()}")
    print("commands: /todos /bg /compact /quit\n")
    history = []
    while True:
        try:
            query = input("\033[36mvideo-agent >> \033[0m")
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="video-agent: 剪辑 agent")
    ap.add_argument("project", help="项目名 (workspace/<project>/)")
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


if __name__ == "__main__":
    main()

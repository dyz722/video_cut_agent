# Harness: async actions -- 渲染/转码丢后台, agent 继续规划下一条片子. (port of s08)
"""BackgroundManager: 守护线程跑慢命令(渲染/转码/转写), 完成后通知注入主循环。"""

import subprocess
import threading
import uuid
from queue import Queue

from . import config


class BackgroundManager:
    def __init__(self):
        self.tasks = {}
        self.notifications = Queue()

    def run(self, command: str, timeout: int = 1800, label: str = "") -> str:
        tid = str(uuid.uuid4())[:8]
        self.tasks[tid] = {"status": "running", "command": command,
                           "label": label, "result": None}
        threading.Thread(target=self._exec, args=(tid, command, timeout),
                         daemon=True).start()
        return f"Background task {tid} started: {label or command[:80]}"

    def run_fn(self, fn, label: str = "") -> str:
        """跑 Python 函数 (渲染器/转写等), fn() 返回字符串结果。"""
        tid = str(uuid.uuid4())[:8]
        self.tasks[tid] = {"status": "running", "command": label, "label": label, "result": None}

        def _wrap():
            try:
                out = fn()
                self.tasks[tid].update({"status": "completed", "result": str(out)[:50000]})
            except Exception as e:
                self.tasks[tid].update({"status": "error", "result": f"{type(e).__name__}: {e}"})
            self.notifications.put({"task_id": tid, "status": self.tasks[tid]["status"],
                                    "label": label, "result": self.tasks[tid]["result"][:1000]})

        threading.Thread(target=_wrap, daemon=True).start()
        return f"Background task {tid} started: {label}"

    def _exec(self, tid: str, command: str, timeout: int):
        try:
            r = subprocess.run(command, shell=True, cwd=config.PROJECT_DIR,
                               capture_output=True, text=True, timeout=timeout)
            output = (r.stdout + r.stderr).strip()[:50000]
            status = "completed" if r.returncode == 0 else "error"
            self.tasks[tid].update({"status": status, "result": output or "(no output)"})
        except Exception as e:
            self.tasks[tid].update({"status": "error", "result": str(e)})
        self.notifications.put({"task_id": tid, "status": self.tasks[tid]["status"],
                                "label": self.tasks[tid]["label"],
                                "result": self.tasks[tid]["result"][:1000]})

    def check(self, tid: str = None) -> str:
        if tid:
            t = self.tasks.get(tid)
            if not t:
                return f"Unknown task: {tid}"
            return f"[{t['status']}] {t.get('result') or '(running)'}"
        if not self.tasks:
            return "No background tasks."
        return "\n".join(f"{k}: [{v['status']}] {v['label'] or v['command'][:60]}"
                         for k, v in self.tasks.items())

    def drain(self) -> list:
        notifs = []
        while not self.notifications.empty():
            notifs.append(self.notifications.get_nowait())
        return notifs

    def has_running(self) -> bool:
        return any(t["status"] == "running" for t in self.tasks.values())


BG = BackgroundManager()

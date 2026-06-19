# Perception: sight -- 模型自己判断"需要看画面"时才调用, ASR 为主 VL 为辅.
"""
watch_video: 从 [start,end] 抽帧 -> qwen3-vl-plus 多帧视频理解 -> 文字描述。
帧落盘 analysis/frames/ 方便复查。
"""

import subprocess
import time

from agent import config

MAX_FRAMES = 8


def extract_frames(fp, start: float, end: float, out_dir, prefix: str,
                   max_frames: int = MAX_FRAMES) -> list:
    duration = max(end - start, 0.1)
    n = min(max_frames, max(2, int(duration)))  # 至少2帧, 每秒最多1帧
    fps = n / duration
    pattern = out_dir / f"{prefix}_%02d.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration), "-i", str(fp),
         "-vf", f"fps={fps},scale='min(1280,iw)':-2", "-q:v", "3", str(pattern)],
        capture_output=True, text=True, timeout=600, check=True)
    return sorted(out_dir.glob(f"{prefix}_*.jpg"))


def watch_video(path: str, start: float, end: float, question: str) -> str:
    import dashscope

    fp = config.safe_path(path)
    if not fp.exists():
        return f"Error: file not found: {path}"
    if end <= start:
        return "Error: end must be > start"
    if end - start > 600:
        return ("Error: segment too long (>600s). Watch shorter segments, "
                "or use task subagent to split the analysis.")

    out_dir = config.PROJECT_DIR / "analysis" / "frames"
    prefix = f"{fp.stem}_{int(start)}_{int(end)}_{int(time.time()) % 100000}"
    try:
        frames = extract_frames(fp, start, end, out_dir, prefix)
    except Exception as e:
        return f"Error extracting frames: {e}"
    if not frames:
        return "Error: no frames extracted"

    config.apply_dashscope_config()
    messages = [{"role": "user", "content": [
        {"video": [f"file://{f}" for f in frames]},
        {"text": (f"这是视频 {fp.name} 第 {start:.1f}s-{end:.1f}s 的等间隔抽帧"
                  f"(共{len(frames)}帧, 按时间顺序)。{question}\n"
                  f"回答时尽量给出对应的时间点(秒)。")},
    ]}]
    resp = dashscope.MultiModalConversation.call(
        model=config.vl_model(), messages=messages)
    if resp.status_code != 200:
        return f"Error: VL call failed: {resp.status_code} {resp.message}"
    content = resp.output.choices[0].message.content
    text = "".join(c.get("text", "") for c in content) if isinstance(content, list) else str(content)
    return (f"[watch {fp.name} {start:.1f}-{end:.1f}s, {len(frames)} frames "
            f"(analysis/frames/{prefix}_*.jpg)]\n{text}")

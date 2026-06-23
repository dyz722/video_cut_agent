# Perception: sight -- 模型自己判断"需要看画面"时才调用, ASR 为主 VL 为辅.
"""
watch_video: 从 [start,end] 裁出视频片段优先给 qwen3-vl-plus 理解; 如果当前
endpoint/model 不支持本地视频片段, 自动退回多帧序列。产物落盘方便复查。
"""

from pathlib import Path
import subprocess
import time

from agent import config

MAX_FRAMES = 8
MIN_SEQUENCE_FRAMES = 4
DIRECT_VIDEO_MAX_SECONDS = 120


def extract_frames(fp, start: float, end: float, out_dir, prefix: str,
                   max_frames: int = MAX_FRAMES) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = max(end - start, 0.1)
    n = min(max_frames, max(MIN_SEQUENCE_FRAMES, int(duration)))
    fps = n / duration
    pattern = out_dir / f"{prefix}_%02d.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration), "-i", str(fp),
         "-vf", f"fps={fps},scale='min(1280,iw)':-2", "-q:v", "3", str(pattern)],
        capture_output=True, text=True, timeout=600, check=True)
    return sorted(out_dir.glob(f"{prefix}_*.jpg"))


def extract_video_segment(fp, start: float, end: float, out_dir, prefix: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{prefix}.mp4"
    duration = max(end - start, 0.1)
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration), "-i", str(fp),
         "-vf", "scale='min(1280,iw)':-2,fps=12", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
         "-movflags", "+faststart", str(dst)],
        capture_output=True, text=True, timeout=600, check=True)
    return dst


def _call_vl_with_video(video_path: Path, fp: Path, start: float, end: float,
                        question: str) -> str:
    import dashscope

    config.apply_dashscope_config()
    messages = [{"role": "user", "content": [
        {"video": f"file://{video_path}"},
        {"text": (f"这是视频 {fp.name} 第 {start:.1f}s-{end:.1f}s 的片段。"
                  f"{question}\n回答时尽量给出对应的时间点(秒)。")},
    ]}]
    resp = dashscope.MultiModalConversation.call(
        model=config.vl_model(), messages=messages)
    if resp.status_code != 200:
        raise RuntimeError(f"VL video call failed: {resp.status_code} {resp.message}")
    content = resp.output.choices[0].message.content
    return "".join(c.get("text", "") for c in content) if isinstance(content, list) else str(content)


def _call_vl_with_frames(frames: list[Path], fp: Path, start: float, end: float,
                         question: str) -> str:
    import dashscope

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
        raise RuntimeError(f"VL frame call failed: {resp.status_code} {resp.message}")
    content = resp.output.choices[0].message.content
    return "".join(c.get("text", "") for c in content) if isinstance(content, list) else str(content)


def _watch_frames(fp: Path, start: float, end: float, question: str, prefix: str) -> str:
    out_dir = config.PROJECT_DIR / "analysis" / "frames"
    try:
        frames = extract_frames(fp, start, end, out_dir, prefix)
    except Exception as e:
        return f"Error extracting frames: {e}"
    if not frames:
        return "Error: no frames extracted"
    if len(frames) < MIN_SEQUENCE_FRAMES:
        return (f"Error: only {len(frames)} frames extracted; DashScope VL requires at least "
                f"{MIN_SEQUENCE_FRAMES} sequence images. Retry with a longer segment.")
    try:
        text = _call_vl_with_frames(frames, fp, start, end, question)
    except Exception as e:
        return (f"Error: {type(e).__name__}: {e}. "
                "If this is an image-count/range error, retry with a wider time window; "
                "otherwise inspect /logs full before calling watch_video again.")
    return (f"[watch {fp.name} {start:.1f}-{end:.1f}s, frames mode, {len(frames)} frames "
            f"(analysis/frames/{prefix}_*.jpg)]\n{text}")


def _watch_video_segment(fp: Path, start: float, end: float, question: str,
                         prefix: str) -> tuple[bool, str]:
    out_dir = config.PROJECT_DIR / "analysis" / "vl_segments"
    try:
        segment = extract_video_segment(fp, start, end, out_dir, prefix)
        text = _call_vl_with_video(segment, fp, start, end, question)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, (f"[watch {fp.name} {start:.1f}-{end:.1f}s, video mode "
                  f"(analysis/vl_segments/{prefix}.mp4)]\n{text}")


def watch_video(path: str, start: float, end: float, question: str,
                mode: str = "auto") -> str:
    fp = config.safe_path(path)
    if not fp.exists():
        return f"Error: file not found: {path}"
    if end <= start:
        return "Error: end must be > start"
    if end - start > 600:
        return ("Error: segment too long (>600s). Watch shorter segments, "
                "or use task subagent to split the analysis.")
    if mode not in ("auto", "video", "frames"):
        return "Error: mode must be auto, video, or frames"

    prefix = f"{fp.stem}_{int(start)}_{int(end)}_{int(time.time()) % 100000}"
    duration = end - start
    if mode == "frames":
        return _watch_frames(fp, start, end, question, prefix)

    if mode == "video" or duration <= DIRECT_VIDEO_MAX_SECONDS:
        ok, result = _watch_video_segment(fp, start, end, question, prefix)
        if ok:
            return result
        if mode == "video":
            return (f"Error: direct video VL failed: {result}. "
                    "Retry with mode='frames' or a shorter segment.")
        frame_result = _watch_frames(fp, start, end, question, prefix)
        return (f"[video mode failed, fell back to frames: {result}]\n{frame_result}")

    return _watch_frames(fp, start, end, question, prefix)

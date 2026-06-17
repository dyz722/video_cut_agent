# Perception: media metadata -- agent 的第一眼.
"""probe_media: ffprobe 封装, 返回精简摘要 (完整 JSON 落盘 analysis/)。"""

import json
import subprocess

from agent import config


def ffprobe_json(path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr[:300]}")
    return json.loads(r.stdout)


def media_duration(path) -> float:
    data = ffprobe_json(path)
    return float(data["format"].get("duration", 0))


def probe_media(path: str) -> str:
    fp = config.safe_path(path)
    if not fp.exists():
        return f"Error: file not found: {path}"
    try:
        data = ffprobe_json(fp)
    except Exception as e:
        return f"Error: {e}"

    out_path = config.PROJECT_DIR / "analysis" / f"{fp.stem}.probe.json"
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    fmt = data.get("format", {})
    lines = [f"file: {path}",
             f"duration: {float(fmt.get('duration', 0)):.2f}s",
             f"size: {int(fmt.get('size', 0)) / 1e6:.1f}MB",
             f"bitrate: {int(fmt.get('bit_rate', 0)) // 1000}kbps"]
    for s in data.get("streams", []):
        if s["codec_type"] == "video":
            fps = s.get("avg_frame_rate", "0/1")
            try:
                num, den = fps.split("/")
                fps = f"{int(num) / max(int(den), 1):.2f}"
            except ValueError:
                pass
            lines.append(f"video: {s.get('codec_name')} {s.get('width')}x{s.get('height')} "
                         f"{fps}fps")
        elif s["codec_type"] == "audio":
            lines.append(f"audio: {s.get('codec_name')} {s.get('sample_rate')}Hz "
                         f"{s.get('channels')}ch")
    lines.append(f"(full json: analysis/{out_path.name})")
    return "\n".join(lines)

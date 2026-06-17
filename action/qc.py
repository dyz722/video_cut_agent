# Action: quality control -- 剪辑 agent 的"跑测试": 成片自检.
"""
qc_check: 时长/响度/黑帧检测 + 抽样帧落盘 (供 agent 用 watch_video 视觉复查)。
"""

import re
import subprocess

from agent import config
from perception.probe import ffprobe_json, media_duration
from perception.watch import extract_frames


def qc_check(path: str, sample_frames: int = 4) -> str:
    fp = config.safe_path(path)
    if not fp.exists():
        return f"Error: file not found: {path}"

    report = [f"QC report: {path}"]
    issues = []

    # 1. 基础元信息
    try:
        data = ffprobe_json(fp)
        dur = media_duration(fp)
        v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
        a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
        report.append(f"duration: {dur:.2f}s | video: "
                      f"{v.get('width')}x{v.get('height')}" if v else "NO VIDEO STREAM")
        if not v:
            issues.append("missing video stream")
        if not a:
            issues.append("missing audio stream")
        if dur < 1:
            issues.append(f"suspiciously short: {dur:.2f}s")
    except Exception as e:
        return f"Error probing: {e}"

    # 2. 黑帧 + 响度 (一次解码)
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(fp), "-vf", "blackdetect=d=0.5:pix_th=0.10",
             "-af", "ebur128", "-f", "null", "-"],
            capture_output=True, text=True, timeout=1800)
        blacks = re.findall(
            r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)
        if blacks:
            spans = ", ".join(f"{float(s):.1f}-{float(e):.1f}s" for s, e in blacks[:5])
            issues.append(f"black segments: {spans}")
        m = re.search(r"I:\s+(-?[\d.]+) LUFS", r.stderr.split("Summary:")[-1])
        if m:
            lufs = float(m.group(1))
            report.append(f"loudness: {lufs:.1f} LUFS (短视频平台建议 -16~-12)")
            if lufs < -25:
                issues.append(f"audio too quiet ({lufs:.1f} LUFS)")
            elif lufs > -8:
                issues.append(f"audio too loud ({lufs:.1f} LUFS)")
    except Exception as e:
        issues.append(f"black/loudness scan failed: {e}")

    # 3. 抽样帧
    try:
        out_dir = config.PROJECT_DIR / "analysis" / "frames"
        frames = extract_frames(fp, 0, dur, out_dir, f"qc_{fp.stem}",
                                max_frames=max(sample_frames, 2))
        report.append(f"sample frames: " +
                      ", ".join(f"analysis/frames/{f.name}" for f in frames))
    except Exception as e:
        issues.append(f"frame sampling failed: {e}")

    report.append("issues: " + ("; ".join(issues) if issues else "none detected"))
    report.append("(视觉复查: 用 watch_video 看成片开头3s钩子和字幕是否越界)")
    return "\n".join(report)

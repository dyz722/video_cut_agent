# Perception: scene changes -- 天然剪辑点候选, 剪辑边界 snap 到这里.
"""detect_scenes: ffmpeg scene-change 检测 -> analysis/<name>.scenes.json"""

import json
import re
import subprocess

from agent import config
from .probe import media_duration


def detect_scenes(path: str, threshold: float = 0.4) -> str:
    fp = config.safe_path(path)
    if not fp.exists():
        return f"Error: file not found: {path}"
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(fp), "-vf",
             f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=3600)
        times = [round(float(m), 2)
                 for m in re.findall(r"pts_time:([\d.]+)", r.stderr)]
        duration = media_duration(fp)
    except Exception as e:
        return f"Error: {e}"

    cuts = [0.0] + times + [round(duration, 2)]
    shots = [{"start": cuts[i], "end": cuts[i + 1],
              "duration": round(cuts[i + 1] - cuts[i], 2)}
             for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]

    out = config.PROJECT_DIR / "analysis" / f"{fp.stem}.scenes.json"
    out.write_text(json.dumps({"source": path, "threshold": threshold,
                               "scene_changes": times, "shots": shots},
                              ensure_ascii=False, indent=1))
    preview = ", ".join(f"{t:.1f}" for t in times[:30])
    more = f" ... (+{len(times) - 30} more)" if len(times) > 30 else ""
    return (f"Detected {len(times)} scene changes in {path} ({duration:.1f}s).\n"
            f"Saved: analysis/{out.name}\nCut points (s): {preview}{more}")

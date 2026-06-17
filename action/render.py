# Action: deterministic renderer -- 模型产出 timeline.json, 渲染器一次性执行.
"""
渲染流水线 (避免单条巨型滤镜链):
    Pass 1  每个 clip 独立归一化: 剪切/变速/裁剪构图/统一编码 -> .cache/clip_i.mp4
    Pass 2  拼接: 无转场用 concat demuxer (copy); 有转场逐对 xfade/acrossfade
    Pass 3  合成: 贴片/画中画 overlay + ASS 字幕烧录 + BGM混音(可选闪避) + 配音
"""

import json
import shutil
import subprocess

from agent import config
from agent.background import BG
from perception.probe import ffprobe_json
from .ass_effects import build_ass
from .timeline import load_timeline, validate, clip_out_duration, total_duration


def _run(cmd: list, timeout: int = 3600):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n  cmd: {' '.join(str(c) for c in cmd)[:500]}\n"
                           f"  err: {r.stderr[-1500:]}")


def _has_audio(fp) -> bool:
    return any(s["codec_type"] == "audio" for s in ffprobe_json(fp).get("streams", []))


def _atempo_chain(speed: float) -> str:
    """atempo 单级范围 0.5~2.0, 超出用级联。"""
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


# === Pass 1: clip 归一化 ===
def _normalize_clip(clip: dict, i: int, out: dict, cache) -> str:
    src = config.safe_path(clip["source"])
    w, h, fps = out["width"], out["height"], out.get("fps", 30)
    speed = clip.get("speed", 1.0)
    dst = cache / f"clip_{i:03d}.mp4"
    seg_dur = clip["out"] - clip["in"]

    fit = clip.get("fit", "crop")
    if fit == "pad":
        vfit = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
    else:
        vfit = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    vf = f"[0:v]{vfit},fps={fps},setsar=1"
    if speed != 1.0:
        vf += f",setpts=PTS/{speed}"
    vf += "[v]"

    has_audio = _has_audio(src)
    cmd = ["ffmpeg", "-y", "-ss", clip["in"], "-to", clip["out"], "-i", src]
    if has_audio:
        af = f"[0:a]volume={clip.get('volume', 1.0)}"
        if speed != 1.0:
            af += "," + _atempo_chain(speed)
        af += ",aresample=48000[a]"
    else:
        cmd += ["-f", "lavfi", "-t", seg_dur / speed,
                "-i", "anullsrc=r=48000:cl=stereo"]
        af = "[1:a]anull[a]"
    cmd += ["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", dst]
    _run(cmd)
    return str(dst)


# === Pass 2: 拼接 ===
def _concat(clips: list, files: list, cache) -> str:
    dst = cache / "base.mp4"
    transitions = [c.get("transition") for c in clips[:-1]]
    if not any(transitions):
        lst = cache / "concat.txt"
        lst.write_text("\n".join(f"file '{f}'" for f in files))
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
              "-c", "copy", dst])
        return str(dst)

    # 有转场: 逐对合并
    cur = files[0]
    cur_dur = clip_out_duration(clips[0])
    for i in range(1, len(files)):
        nxt = files[i]
        nxt_dur = clip_out_duration(clips[i])
        tr = transitions[i - 1]
        tmp = cache / f"join_{i:03d}.mp4"
        if tr:
            td = tr.get("duration", 0.5)
            offset = max(cur_dur - td, 0)
            fc = (f"[0:v][1:v]xfade=transition={tr['type']}:duration={td}:"
                  f"offset={offset:.3f}[v];[0:a][1:a]acrossfade=d={td}[a]")
            cur_dur = cur_dur + nxt_dur - td
        else:
            fc = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]"
            cur_dur = cur_dur + nxt_dur
        _run(["ffmpeg", "-y", "-i", cur, "-i", nxt, "-filter_complex", fc,
              "-map", "[v]", "-map", "[a]",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
              "-c:a", "aac", "-ar", "48000", "-ac", "2", tmp])
        cur = str(tmp)
    shutil.copy(cur, dst)
    return str(dst)


# === Pass 3: overlay + 字幕 + 音频合成 ===
def _compose(tl: dict, base: str, cache) -> str:
    out = tl["output"]
    dur = total_duration(tl)
    dst = config.safe_path(out["file"])
    dst.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-i", base]
    fc_parts = []
    idx = 1

    # -- overlays --
    last_v = "[0:v]"
    for j, o in enumerate(tl.get("overlays", [])):
        if o.get("image"):
            cmd += ["-loop", "1", "-i", config.safe_path(o["image"])]
            pre = f"[{idx}:v]format=rgba"
        else:
            cmd += ["-i", config.safe_path(o["video"])]
            pre = f"[{idx}:v]setpts=PTS+{o.get('start', 0)}/TB"
        if o.get("width"):
            pre += f",scale={o['width']}:-2"
        fc_parts.append(f"{pre}[ov{j}]")
        x = "(W-w)/2" if o.get("x", "center") == "center" else str(o["x"])
        y = str(o.get("y", 0))
        fc_parts.append(
            f"{last_v}[ov{j}]overlay={x}:{y}:"
            f"enable='between(t,{o.get('start', 0)},{o.get('end', dur)})'[v{j}]")
        last_v = f"[v{j}]"
        idx += 1

    # -- 字幕 (ASS) --
    subs = tl.get("subtitles", [])
    if subs:
        ass_path = cache / "subtitles.ass"
        ass_path.write_text(build_ass(subs, out["width"], out["height"],
                                      tl.get("subtitle_styles")))
        fc_parts.append(f"{last_v}ass='{ass_path}'[vout]")
    else:
        fc_parts.append(f"{last_v}null[vout]")

    # -- 音频 --
    audio = tl.get("audio", {})
    vos = audio.get("voiceover", [])
    bgm = audio.get("bgm")
    speech_inputs = ["[0:a]"]
    for k, vo in enumerate(vos):
        cmd += ["-i", config.safe_path(vo["file"])]
        delay = int(vo.get("start", 0) * 1000)
        fc_parts.append(f"[{idx}:a]adelay={delay}:all=1,"
                        f"volume={vo.get('volume', 1.0)},aresample=48000[vo{k}]")
        speech_inputs.append(f"[vo{k}]")
        idx += 1
    if len(speech_inputs) > 1:
        fc_parts.append("".join(speech_inputs) +
                        f"amix=inputs={len(speech_inputs)}:duration=first:normalize=0[sp]")
        speech = "[sp]"
    else:
        speech = "[0:a]"

    if bgm:
        cmd += ["-stream_loop", "-1", "-i", config.safe_path(bgm["file"])]
        fc_parts.append(f"[{idx}:a]atrim=0:{dur:.3f},aresample=48000,"
                        f"volume={bgm.get('volume', 0.3)}[bg]")
        idx += 1
        if bgm.get("ducking", True):
            # sidechaincompress 会消耗 speech 流, 先 asplit 复制一份
            fc_parts.append(f"{speech}asplit=2[spA][spB]")
            fc_parts.append("[bg][spB]sidechaincompress=threshold=0.05:ratio=10:"
                            "attack=20:release=400[bgd]")
            fc_parts.append("[spA][bgd]amix=inputs=2:duration=first:normalize=0[aout]")
        else:
            fc_parts.append(f"{speech}[bg]amix=inputs=2:duration=first:normalize=0[aout]")
        aout = "[aout]"
    else:
        if speech == "[0:a]":
            fc_parts.append("[0:a]anull[aout]")
        else:
            fc_parts.append(f"{speech}anull[aout]")
        aout = "[aout]"

    cmd += ["-filter_complex", ";".join(fc_parts),
            "-map", "[vout]", "-map", aout, "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst]
    _run(cmd)
    return str(dst)


def render_file(path: str) -> str:
    tl = load_timeline(path)
    errors = validate(tl)
    if errors:
        return "Render aborted, INVALID timeline:\n" + "\n".join(f"  - {e}" for e in errors)
    stem = config.safe_path(path).stem
    cache = config.PROJECT_DIR / ".cache" / stem
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True)

    files = [_normalize_clip(c, i, tl["output"], cache)
             for i, c in enumerate(tl["clips"])]
    base = _concat(tl["clips"], files, cache)
    final = _compose(tl, base, cache)
    dur = total_duration(tl)
    rel = config.safe_path(tl["output"]["file"]).relative_to(config.PROJECT_DIR)
    return (f"Rendered {path} -> {rel} ({dur:.1f}s). "
            f"Next: qc_check + watch_video to self-review.")


def render_request(path: str = "timeline.json", background: bool = True) -> str:
    """渲染入口: 校验 -> (人审) -> 后台渲染。"""
    from .timeline import validate_file
    v = validate_file(path)
    if v.startswith(("Error", "INVALID")):
        return v
    if not config.AUTO_MODE:
        tl = load_timeline(path)
        print(f"\n\033[35m[人审] 渲染请求: {path}\033[0m\n  {v}")
        for i, c in enumerate(tl["clips"][:12]):
            print(f"  clip{i}: {c['source']} [{c['in']:.1f}-{c['out']:.1f}s]"
                  f"{' +' + c['transition']['type'] if c.get('transition') else ''}")
        ans = input("\033[35m  批准渲染? [y/N]: \033[0m").strip().lower()
        if ans not in ("y", "yes"):
            reason = input("\033[35m  修改意见(回传给 agent): \033[0m").strip()
            return f"HUMAN REJECTED render. Feedback: {reason or '(none)'}"
    if background:
        return BG.run_fn(lambda: render_file(path), label=f"render {path}")
    return render_file(path)

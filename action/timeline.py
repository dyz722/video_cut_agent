# Action: timeline -- 剪辑决策的中间表示. 模型做决策, 渲染器执行.
"""
timeline.json schema (v1):

{
  "version": 1,
  "output": {"file": "output/clip_01.mp4", "width": 1080, "height": 1920, "fps": 30},
  "clips": [                       // 按顺序拼接, in/out 是源素材时间码(秒)
    {"source": "materials/a.mp4", "in": 12.5, "out": 20.0,
     "speed": 1.0,                 // 可选 0.25~4.0
     "volume": 1.0,                // 可选, 源音量
     "fit": "crop",                // 可选 crop(默认,裁满)/pad(留黑边)
     "transition": {"type": "fade", "duration": 0.5}}  // 可选, 与下一条之间
  ],
  "subtitles": [                   // start/end 是成片时间轴(秒)
    {"start": 0.0, "end": 2.5, "text": "三秒钩子文案",
     "style": "hook",              // 样式预设: default/hook/caption/promo (可自定义覆盖)
     "effect": "pop_in"}           // 动效预设: none/fade_in/pop_in/slide_up/karaoke
  ],
  "overlays": [                    // 贴片/横幅/画中画, start/end 是成片时间轴
    {"image": "materials/banner.png", "start": 0, "end": 10,
     "x": "center", "y": 80, "width": 900}
    // 画中画用 video 字段替代 image: {"video": "materials/b.mp4", ...}
  ],
  "audio": {
    "bgm": {"file": "materials/bgm.mp3", "volume": 0.25, "ducking": true},
    "voiceover": [{"file": "analysis/vo_01.wav", "start": 0.0, "volume": 1.0}]
  },
  "subtitle_styles": {             // 可选: 覆盖/新增 ASS 样式预设
    "hook": {"fontsize": 90, "primary_colour": "&H0000FFFF&"}
  }
}
"""

import json

from agent import config

TRANSITIONS = {"fade", "fadeblack", "fadewhite", "wipeleft", "wiperight",
               "slideleft", "slideright", "slideup", "slidedown", "circleopen",
               "dissolve"}
EFFECTS = {"none", "fade_in", "pop_in", "slide_up", "karaoke"}
MEDIA_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".mp3", ".wav",
             ".m4a", ".aac", ".png", ".jpg", ".jpeg")


def load_timeline(path: str) -> dict:
    fp = config.safe_path(path)
    return json.loads(fp.read_text())


def clip_out_duration(clip: dict) -> float:
    return (clip["out"] - clip["in"]) / clip.get("speed", 1.0)


def total_duration(tl: dict) -> float:
    dur = sum(clip_out_duration(c) for c in tl["clips"])
    for c in tl["clips"][:-1]:
        if c.get("transition"):
            dur -= c["transition"].get("duration", 0.5)
    return dur


def validate(tl: dict) -> list:
    """返回错误列表, 空 = 通过。"""
    errors = []
    out = tl.get("output", {})
    if not out.get("file", "").startswith("output/"):
        errors.append("output.file must be under output/")
    if not (out.get("width") and out.get("height")):
        errors.append("output.width/height required (e.g. 1080x1920 竖屏)")

    clips = tl.get("clips", [])
    if not clips:
        errors.append("clips must not be empty")
    for i, c in enumerate(clips):
        src = c.get("source", "")
        try:
            sp = config.safe_path(src)
            if not sp.exists():
                errors.append(f"clips[{i}].source not found: {src}")
            else:
                from perception.probe import media_duration
                dur = media_duration(sp)
                if c.get("out", 0) > dur + 0.5:
                    errors.append(f"clips[{i}].out={c.get('out')} exceeds source "
                                  f"duration {dur:.2f}s")
        except Exception as e:
            errors.append(f"clips[{i}].source invalid: {e}")
        if not isinstance(c.get("in"), (int, float)) or not isinstance(c.get("out"), (int, float)):
            errors.append(f"clips[{i}]: numeric in/out required")
        elif c["out"] <= c["in"]:
            errors.append(f"clips[{i}]: out must be > in")
        speed = c.get("speed", 1.0)
        if not 0.25 <= speed <= 4.0:
            errors.append(f"clips[{i}].speed {speed} out of range 0.25~4.0")
        tr = c.get("transition")
        if tr:
            if tr.get("type") not in TRANSITIONS:
                errors.append(f"clips[{i}].transition.type '{tr.get('type')}' not in "
                              f"{sorted(TRANSITIONS)}")
            if i == len(clips) - 1:
                errors.append(f"clips[{i}]: last clip cannot have a transition")
            elif tr.get("duration", 0.5) >= min(clip_out_duration(c),
                                                clip_out_duration(clips[i + 1])):
                errors.append(f"clips[{i}].transition.duration too long vs clip length")

    dur = total_duration(tl) if clips and not errors else None
    for i, s in enumerate(tl.get("subtitles", [])):
        if not s.get("text"):
            errors.append(f"subtitles[{i}].text required")
        if s.get("end", 0) <= s.get("start", -1):
            errors.append(f"subtitles[{i}]: end must be > start")
        elif dur and s["end"] > dur + 0.5:
            errors.append(f"subtitles[{i}].end={s['end']} exceeds film duration {dur:.2f}s")
        if s.get("effect", "none") not in EFFECTS:
            errors.append(f"subtitles[{i}].effect '{s.get('effect')}' not in {sorted(EFFECTS)}")

    for i, o in enumerate(tl.get("overlays", [])):
        media = o.get("image") or o.get("video")
        if not media:
            errors.append(f"overlays[{i}]: image or video required")
        else:
            try:
                if not config.safe_path(media).exists():
                    errors.append(f"overlays[{i}] media not found: {media}")
            except Exception as e:
                errors.append(f"overlays[{i}] media invalid: {e}")
        if o.get("end", 0) <= o.get("start", -1):
            errors.append(f"overlays[{i}]: end must be > start")

    audio = tl.get("audio", {})
    bgm = audio.get("bgm")
    if bgm:
        try:
            if not config.safe_path(bgm.get("file", "")).exists():
                errors.append(f"audio.bgm.file not found: {bgm.get('file')}")
        except Exception as e:
            errors.append(f"audio.bgm.file invalid: {e}")
    for i, vo in enumerate(audio.get("voiceover", [])):
        try:
            if not config.safe_path(vo.get("file", "")).exists():
                errors.append(f"audio.voiceover[{i}].file not found: {vo.get('file')}")
        except Exception as e:
            errors.append(f"audio.voiceover[{i}].file invalid: {e}")
    return errors


def validate_file(path: str) -> str:
    try:
        tl = load_timeline(path)
    except Exception as e:
        return f"Error: cannot parse {path}: {e}"
    errors = validate(tl)
    if errors:
        return "INVALID timeline:\n" + "\n".join(f"  - {e}" for e in errors)
    dur = total_duration(tl)
    return (f"VALID. {len(tl['clips'])} clips, {len(tl.get('subtitles', []))} subtitles, "
            f"{len(tl.get('overlays', []))} overlays, estimated duration {dur:.2f}s "
            f"-> {tl['output']['file']}")

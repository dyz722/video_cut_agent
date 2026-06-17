# Action: subtitle styling -- 字幕样式与动效全走 ASS (libass), 不用 drawtext.
"""
样式预设 + 动效预设 -> .ass 文件。
skill 通过 timeline 的 style/effect 字段引用预设名, 也可用 subtitle_styles 覆盖参数。
颜色是 ASS 的 &HAABBGGRR& 格式 (注意 BGR 顺序)。
"""

import os
import subprocess
from functools import lru_cache

# 中文字体按优先级自动探测 (可用 SUBTITLE_FONT 环境变量覆盖)
FONT_CANDIDATES = ["PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC",
                   "Source Han Sans SC", "Microsoft YaHei", "Heiti SC",
                   "Arial Unicode MS"]


@lru_cache(maxsize=1)
def default_font() -> str:
    if os.getenv("SUBTITLE_FONT"):
        return os.environ["SUBTITLE_FONT"]
    try:
        r = subprocess.run(["fc-list", ":lang=zh", "family"],
                           capture_output=True, text=True, timeout=10)
        available = r.stdout
        for cand in FONT_CANDIDATES:
            if cand in available:
                return cand
    except Exception:
        pass
    return FONT_CANDIDATES[1]  # 找不到就交给 libass fallback


def _color(v: str) -> str:
    """归一化 ASS 颜色为 &HAABBGGRR 格式。"""
    return "&H" + str(v).strip("&").upper().lstrip("H")


# 基础样式预设 (针对 1080 宽竖屏设计, 其他分辨率按 PlayRes 自动缩放)
# fontname "auto" 在 build_ass 时解析为系统可用中文字体
STYLE_PRESETS = {
    "default": {  # 通用白字黑边, 底部
        "fontname": "auto", "fontsize": 64, "bold": -1,
        "primary_colour": "&H00FFFFFF&", "outline_colour": "&H00000000&",
        "outline": 3, "shadow": 0, "alignment": 2, "margin_v": 180,
    },
    "hook": {     # 开头钩子: 大号黄字, 中上位置
        "fontname": "auto", "fontsize": 88, "bold": -1,
        "primary_colour": "&H0000FFFF&", "outline_colour": "&H00000000&",
        "outline": 4, "shadow": 2, "alignment": 8, "margin_v": 320,
    },
    "caption": {  # 小号说明字幕
        "fontname": "auto", "fontsize": 48, "bold": 0,
        "primary_colour": "&H00FFFFFF&", "outline_colour": "&H00000000&",
        "outline": 2, "shadow": 0, "alignment": 2, "margin_v": 120,
    },
    "promo": {    # 促销: 白字红边, 醒目
        "fontname": "auto", "fontsize": 76, "bold": -1,
        "primary_colour": "&H00FFFFFF&", "outline_colour": "&H002020E0&",
        "outline": 4, "shadow": 2, "alignment": 2, "margin_v": 240,
    },
}

# 动效预设 -> ASS override tags (注入每条 Dialogue 开头)
EFFECT_TAGS = {
    "none": "",
    "fade_in": r"{\fad(200,120)}",
    "pop_in": r"{\fad(80,0)\t(0,160,\fscx118\fscy118)\t(160,300,\fscx100\fscy100)}",
    "slide_up": r"{\fad(150,100)\move($x,$y2,$x,$y1,0,250)}",
    "karaoke": "",  # karaoke 按字时长拆 \k, 在 build_dialogue 里特殊处理
}


def _fmt_time(t: float) -> str:
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_name(name: str) -> str:
    """libass 内置 "Default" 样式 (Arial) 会大小写不敏感地抢占 "default",
    统一给输出到 .ass 的样式名加前缀避开内置名。timeline 对外名字不变。"""
    return "v_" + name


def _style_line(name: str, st: dict) -> str:
    font = st["fontname"] if st.get("fontname", "auto") != "auto" else default_font()
    return (f"Style: {name},{font},{st['fontsize']},"
            f"{_color(st['primary_colour'])},&H000000FF,"
            f"{_color(st['outline_colour'])},&H64000000,"
            f"{st.get('bold', 0)},0,0,0,100,100,0,0,1,"
            f"{st.get('outline', 3)},{st.get('shadow', 0)},"
            f"{st.get('alignment', 2)},60,60,{st.get('margin_v', 180)},1")


def _karaoke_text(text: str, start: float, end: float) -> str:
    """按字符均分时长的简易卡拉OK效果。"""
    chars = [c for c in text]
    if not chars:
        return text
    cs = max(int((end - start) * 100 / len(chars)), 1)  # centiseconds per char
    return "".join(f"{{\\k{cs}}}{c}" for c in chars)


def build_ass(subtitles: list, width: int, height: int,
              style_overrides: dict = None) -> str:
    styles = {k: dict(v) for k, v in STYLE_PRESETS.items()}
    for name, ov in (style_overrides or {}).items():
        base = dict(styles.get(name, styles["default"]))
        base.update(ov)
        styles[name] = base
    # 收集用到的自定义样式名
    for s in subtitles:
        if s.get("style") and s["style"] not in styles:
            styles[s["style"]] = dict(styles["default"])

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
         "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
         "Alignment, MarginL, MarginR, MarginV, Encoding"),
    ]
    header += [_style_line(_ass_name(n), st) for n, st in styles.items()]
    header += ["", "[Events]",
               "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    events = []
    for s in subtitles:
        style = s.get("style", "default")
        effect = s.get("effect", "none")
        text = s["text"].replace("\n", r"\N")
        st = styles.get(style, styles["default"])
        tag = EFFECT_TAGS.get(effect, "")
        if effect == "karaoke":
            text = _karaoke_text(s["text"], s["start"], s["end"])
        elif effect == "slide_up":
            # slide_up 需要坐标: 按 alignment 推算目标位置
            x = width // 2
            y1 = height - st.get("margin_v", 180) if st.get("alignment", 2) == 2 \
                else st.get("margin_v", 320)
            tag = tag.replace("$x", str(x)).replace("$y1", str(y1)) \
                     .replace("$y2", str(y1 + 60))
        events.append(f"Dialogue: 0,{_fmt_time(s['start'])},{_fmt_time(s['end'])},"
                      f"{_ass_name(style)},,0,0,0,,{tag}{text}")
    return "\n".join(header + events) + "\n"

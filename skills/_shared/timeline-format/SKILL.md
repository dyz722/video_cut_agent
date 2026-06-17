---
name: timeline-format
description: timeline.json 剪辑决策清单的完整格式规范与写法范例。写任何 timeline 之前必须加载。
---

# timeline.json 格式规范

一条成片 = 一份 timeline 文件（命名 `timeline_01.json`, `timeline_02.json`...）。
写完必须先 `validate_timeline` 再 `render_timeline`。

## 完整字段

```json
{
  "version": 1,
  "output": {"file": "output/clip_01.mp4", "width": 1080, "height": 1920, "fps": 30},
  "clips": [
    {"source": "materials/a.mp4", "in": 12.5, "out": 20.0,
     "speed": 1.0, "volume": 1.0, "fit": "crop",
     "transition": {"type": "fade", "duration": 0.5}}
  ],
  "subtitles": [
    {"start": 0.0, "end": 2.5, "text": "钩子文案", "style": "hook", "effect": "pop_in"}
  ],
  "overlays": [
    {"image": "materials/banner.png", "start": 0, "end": 10,
     "x": "center", "y": 80, "width": 900}
  ],
  "audio": {
    "bgm": {"file": "materials/bgm.mp3", "volume": 0.25, "ducking": true},
    "voiceover": [{"file": "analysis/vo_01.wav", "start": 0.0, "volume": 1.0}]
  },
  "subtitle_styles": {
    "hook": {"fontsize": 96, "primary_colour": "&H0000FFFF&"}
  }
}
```

## 关键规则

1. **两套时间轴**: `clips[].in/out` 是源素材时间码; `subtitles/overlays` 的
   `start/end` 是成片时间轴。成片时长 = sum((out-in)/speed) - sum(转场时长)。
2. **剪辑点对齐**: in/out 尽量 snap 到 `analysis/*.scenes.json` 的切变点
   (±0.3s 内), 避免切在动作/口型中间; 同时不要切断 transcript 的句子。
3. **fit**: 横屏素材出竖屏用 `crop`(默认, 裁满) ; 需要保留完整画面用 `pad`。
4. **字幕样式预设**: `default`(白字底部) / `hook`(大黄字中上) /
   `caption`(小字) / `promo`(白字红边)。可用 `subtitle_styles` 覆盖
   fontsize / primary_colour (&HAABBGGRR& BGR色序) / alignment(2底/5中/8顶) /
   margin_v 等。
5. **字幕动效**: `none` / `fade_in` / `pop_in`(弹入,钩子常用) /
   `slide_up` / `karaoke`(逐字,口播跟读)。
6. **转场类型**: fade / fadeblack / fadewhite / wipeleft / wiperight /
   slideleft / slideright / slideup / slidedown / circleopen / dissolve。
   转场写在前一个 clip 上, 最后一个 clip 不能有。
7. **BGM**: volume 0.2~0.35 为宜, `ducking: true` 人声时自动压低。
   配音(voiceover)的字幕要与音频时间区间一致。
8. **横屏(16:9)**: width 1920 height 1080; 竖屏(9:16): 1080x1920。

## 常见错误

- subtitles.end 超过成片总时长 -> validate 会报错, 先算好成片时长
- 引用了不存在的素材路径 -> 用 bash ls materials/ 确认文件名
- 转场时长 >= 相邻 clip 时长 -> 缩短转场或取消

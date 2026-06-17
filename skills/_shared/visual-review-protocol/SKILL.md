---
name: visual-review-protocol
description: 剪辑 agent 的可视化审核协议：渲染前用 HTML 审核 timeline，记录用户修改，并把稳定偏好沉淀为经验。
---

# 可视化审核协议

终端对话只适合说明目标和解释结果；剪辑决策本身必须尽量可视化。除非用户明确要求跳过，交互模式下渲染前应使用 `review_timeline` 让用户确认或修改 timeline。

## 何时使用

1. 写完 `timeline_*.json` 并通过 `validate_timeline` 后，调用 `review_timeline`。
2. 用户在 HTML 页中检查 clips、subtitles、overlays 和 audio，并保存审核结果。
3. 如果生成了 `review/<timeline>/timeline.reviewed.json`，后续渲染应优先使用这份修订版。
4. 渲染和 QC 后，如用户仍有修改意见，回到 timeline 修改并再次审核。

## 如何学习

审核页会保存 `review_log.json`，里面包含原始 timeline、修订版、用户备注和被修改的字段。学习时只抽象可复用偏好：

- 用户删掉或缩短的片段，可能代表“节奏太慢”“铺垫不要太长”。
- 用户改字幕样式，可能代表该场景的字幕大小、位置、颜色偏好。
- 用户调整 clip 顺序，可能代表钩子、卖点、情绪峰值的排序偏好。
- 用户标记“需要返工”，优先总结问题类型，不要急着固化成规则。

只有用户明确满意或确认规则时，才调用 `record_experience`。不要记录素材文件名、客户隐私、原始大段转写或一次性的剧情细节。

## 判断尺度

- 把 HTML 审核看作剪辑 agent 的默认人机交互界面，不要只在终端里让用户凭文字想象。
- `review_log.json` 是学习原料，不是最终规则。必须先归纳，再让用户确认。
- 如果用户修改是单次项目特例，只写回本项目 timeline，不沉淀为全局 learned skill。

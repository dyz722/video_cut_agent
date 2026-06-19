# video_cut_agent

`video_cut_agent` 是一个基于 Claude Code harness 思想实现的视频剪辑 agent：模型负责理解目标、规划剪辑和调用工具，代码提供素材感知、timeline 决策清单、确定性 ffmpeg 渲染、质检、skills 和上下文管理。

核心原则：

- Agent 是模型，项目代码是 harness。
- 剪辑判断沉淀在 `skills/`，按需加载，不把所有知识塞进 system prompt。
- 模型只产出 `timeline*.json`，最终视频由确定性渲染器执行。
- 渲染前默认通过 HTML 审核页让用户确认或修改 timeline。
- 长转写、渲染等慢任务走后台，完成后再回到 agent loop。
- 用户认可的剪辑经验可以沉淀为 `learned-*` skill，后续自动复用。

## 安装

### 从 GitHub 安装

推荐使用 `pipx` 安装为终端命令：

```bash
pipx install git+https://github.com/dyz722/video_cut_agent.git
```

也可以使用 `pip`：

```bash
python -m pip install git+https://github.com/dyz722/video_cut_agent.git
```

安装后可直接使用：

```bash
veoai
veoai --help
```

启动后会进入一个终端欢迎面板，展示当前项目目录、主模型协议/模型、常用命令和使用提示。输入区采用类似 Codex 的 `›` 提示和底部状态栏，状态栏会显示当前模型与项目目录。模型请求、工具执行、后台等待时会显示动态状态，例如 `thinking with ...`、`running tool: ...`，避免长任务看起来像卡死。默认只展示工具调用摘要，大段 `bash` 输出、skill 全文、JSON 等会折叠到 `/logs` 中；需要排查时可用 `/logs full` 或 `/verbose on` 展开。`veoai` 会保持在交互会话中；如果主模型网关返回 502、上游禁止访问、模型无权限等错误，会在终端给出诊断提示而不是直接崩溃退出。

后续更新到 GitHub 最新版本：

```bash
veoai update
```

如果只是想查看将执行的更新命令：

```bash
veoai update --dry-run
```

### 本地开发安装

```bash
git clone https://github.com/dyz722/video_cut_agent.git
cd video_cut_agent
python -m pip install -e .
```

本地开发仓库建议用 `git pull` 更新源码；普通用户通过 `veoai update` 更新安装版。

## 依赖

需要本机安装：

- Python 3.10+
- `ffmpeg` 和 `ffprobe`
- 可用的主模型 API：Anthropic-compatible 或 OpenAI-compatible
- DashScope API key，用于 ASR、视觉理解和 TTS

首次运行 `veoai` 时，如果还没有配置主模型，会进入快速配置流程：

```text
[主模型配置] 接口协议 (1=anthropic / 2=openai, 当前 anthropic, 回车保留):
[主模型配置] Base URL (回车使用所选协议官方默认):
[主模型配置] API key (必填):
[主模型配置] 模型 ID (回车保留默认或当前值):
```

主模型支持两类主流接口协议：

- `anthropic`：Anthropic Messages API 或三方 Anthropic-compatible 服务。
- `openai`：OpenAI Chat Completions API 或三方 OpenAI-compatible `/v1/chat/completions` 服务。

选择协议后，输入对应的 Base URL、API key 和模型 ID 即可。DashScope key 可先跳过，等需要转写、视觉理解或 TTS 时再配置。DashScope 支持国内/海外两套 endpoint 和 key，可运行 `/dashscope` 切换。

也可以提前创建 `.env`：

```bash
# anthropic 或 openai
MODEL_API_PROTOCOL=anthropic

# Anthropic-compatible
ANTHROPIC_API_KEY=
# ANTHROPIC_BASE_URL=
# MODEL_ID=claude-sonnet-4-6

# OpenAI-compatible
# MODEL_API_PROTOCOL=openai
# OPENAI_API_KEY=
# OPENAI_BASE_URL=https://api.openai.com/v1
# MODEL_ID=gpt-4o

DASHSCOPE_API_KEY=
# DASHSCOPE_REGION=cn
# DASHSCOPE_API_KEY_CN=
# DASHSCOPE_API_KEY_INTL=
# DASHSCOPE_BASE_URL_CN=https://dashscope.aliyuncs.com/api/v1
# DASHSCOPE_BASE_URL_INTL=https://dashscope-intl.aliyuncs.com/api/v1
# VL_MODEL=qwen3-vl-plus
# ASR_FILE_MODEL=fun-asr
# ASR_REALTIME_MODEL=fun-asr-realtime
# TTS_MODEL=cosyvoice-v3-flash
```

可选环境变量：

- `VIDEO_AGENT_WORKSPACE`：项目工作区根目录，默认是启动 `veoai` 时所在目录
- `VIDEO_AGENT_HOME`：用户数据目录，默认 `~/.video-agent`
- `VIDEO_AGENT_LEARNED_SKILLS_DIR`：自动沉淀 skill 的保存目录
- `VIDEO_AGENT_ENV`：配置文件路径

## 快速开始

交互模式：

```bash
veoai
```

`veoai` 会把你启动命令时所在的目录作为当前项目工作目录。例如：

```bash
cd ~/Videos/my-live-project
veoai
```

此时 `materials/`、`analysis/`、`timeline*.json`、`output/` 都会创建在 `~/Videos/my-live-project/` 下。

也可以指定子项目目录和素材：

```bash
veoai demo --materials ~/Videos/source/
```

进入 REPL 后可以直接描述任务：

```text
把这场直播切出 5 条 30 秒以内的带货短视频，突出痛点、卖点和优惠信息。
```

批处理模式：

```bash
veoai demo \
  --materials ~/Videos/source/ \
  --batch "把这场直播切出 10 条带货短视频，每条给出成片路径和质检结论" \
  --auto
```

交互模式内置命令：

- `/model`：切换主模型协议、Base URL、API key 和模型 ID，并立即用于后续 agent loop。
- `/dashscope`：配置/切换 DashScope 国内或海外 endpoint/key，用于 ASR、视觉理解和 TTS。
- `/todos`：查看当前剪辑计划。
- `/bg`：查看后台转写或渲染任务。
- `/compact`：手动压缩上下文。
- `/logs`：查看最近工具调用摘要。
- `/logs full`：展开最近工具输入/输出。
- `/logs clear`：清空工具日志。
- `/verbose on|off`：切换详细工具输出；默认关闭，保持界面简洁。
- `?` 或 `/help`：查看快捷命令。
- `Esc`：中断当前运行中的 agent，回到输入框；已有上下文保留，可继续输入新指令引导。
- `Tab`：补全斜杠命令，例如输入 `/m` 后按 Tab 补全为 `/model`。
- `↑` / `↓`：找回上一条/下一条输入内容，编辑后可快速重发；历史记录会保存在 `~/.video-agent/history`。
- `/quit`：退出。

如果遇到类似 `OpenAI-compatible API error 502`、`Upstream access forbidden` 的主模型错误，通常是三方网关上游不可用、模型无权限或管理员限制。此时直接输入 `/model` 切换协议、Base URL、API key 或模型 ID 后重试即可。

项目文件会放在：

```text
<当前目录或指定项目目录>/
  materials/      原始素材软链接
  analysis/       转写、场景检测、抽帧等感知产物
  timeline*.json  剪辑决策清单
  output/         渲染成片
```

## 工作流

标准剪辑流程由 agent 自主执行：

1. `probe_media` 获取素材元信息。
2. `transcribe` 生成带时间戳的转写稿，长视频自动走后台。
3. `detect_scenes` 找自然切点。
4. `load_skill` 加载对应赛道策略，例如 `ecommerce-clip` 或 `manju-compilation`。
5. 写入 `timeline_<n>.json` 并运行 `validate_timeline`。
6. `review_timeline` 生成本地 HTML 审核页，让用户确认或修改 clips、subtitles、overlays 和 audio。
7. 如果用户保存了 `review/<timeline>/timeline.reviewed.json`，优先渲染这份修订版。
8. `render_timeline` 调用 ffmpeg 渲染。
9. `qc_check` 自检后，`review_render` 生成成片审核页，让用户播放成片、标注问题、确认交付。
10. 用户满意或多轮修改形成稳定偏好后，`summarize_review_feedback` 先生成经验候选，经用户确认后再沉淀。

## 可视化审核

剪辑 agent 的交互不只发生在终端。渲染前，agent 默认会调用 `review_timeline` 生成一个本地 HTML 审核页：

```text
review/<timeline名>/
  index.html               审核页面
  original.json            原始 timeline 备份
  timeline.reviewed.json   用户保存后的修订版
  review_log.json          用户备注、修改字段和原始/修订 diff 原料
```

用户可以在网页里：

- 删除或调整 clip 的 `in/out`、速度、音量、画面适配和转场。
- 修改字幕时间、文案、样式和动效。
- 编辑 overlays / audio JSON。
- 填写修改原因，并保存为“批准”或“需要返工”。

`review_log.json` 是经验沉淀的原料。agent 会从用户修改中归纳可复用偏好，例如“钩子太慢要缩短铺垫”“促销字幕更大”“漫剧开头必须直接上冲突画面”。只有用户确认这些偏好可复用后，才会调用 `record_experience` 写入全局 learned skill。

渲染完成后，agent 会调用 `review_render` 生成成片审核页：

```text
review/render_<成片名>/
  index.html               成片审核页面
  render_input.json        成片路径和 QC 报告
  render_review_log.json   用户验收状态、问题标签和修改意见
```

用户可以在网页里播放成片，并标记常见问题：

- 开头钩子弱或进入主题太慢。
- 字幕遮挡、太小、错字或节奏不对。
- 画面裁切、商品/角色展示不清晰。
- 声音太小、BGM 压人声或底噪明显。
- 节奏拖沓或剪太碎。

`summarize_review_feedback` 会读取 timeline 和成片审核日志，写出 `analysis/review_experience_candidates.md`。这一步只生成候选经验，不会默认写入全局 skill；只有用户确认候选规则可复用后，agent 才会调用 `record_experience` 或 `summarize_review_feedback(record_confirmed=true)` 沉淀到 `~/.video-agent/skills/_learned/`。

## 剪辑经验沉淀

当用户确认某次剪辑结果满意，或者多轮修改中形成稳定偏好时，agent 可以调用 `record_experience` 把经验写成 learned skill。

例如：

- 漫剧剪辑中，用户偏好“前 2 秒必须给冲突画面，不要先铺垫人物关系”。
- 带货视频中，用户确认“先痛点再价格机制”的结构更容易通过。
- 字幕样式中，用户反复要求“钩子字幕更大、促销信息放底部常驻”。

沉淀后的 skill 会以 `learned-*` 命名，默认保存在：

```text
~/.video-agent/skills/_learned/
```

之后处理相似任务时，agent 会在可用 skills 列表中看到这些经验，并与基础赛道 skill 一起加载。经验沉淀是全局的，不跟随当前项目目录变化；它只保存可复用剪辑判断，不应保存密钥、客户隐私、原始转写大段文本或私有素材文件名。

## 内置 Skills

- `timeline-format`：`timeline*.json` 格式规范。
- `visual-review-protocol`：渲染前 HTML 审核、用户修改记录和经验沉淀协议。
- `ecommerce-clip`：直播带货、种草、转化类短视频策略。
- `manju-compilation`：漫剧合集、剧情节奏和情绪钩子策略。

新增垂直场景时，可以在 `skills/<name>/SKILL.md` 添加新的 skill；用户认可的偏好则优先通过 `record_experience` 进入 learned skill。

## 开发与测试

运行离线 smoke test：

```bash
python tests/test_smoke.py
```

测试会覆盖：

- 模块导入和工具 schema/handler 对齐
- skill 加载和 learned skill 写入
- HTML timeline 审核页生成
- HTML 成片审核页和审核反馈候选生成
- timeline 校验
- ffmpeg 端到端渲染
- 质检报告

同步到 GitHub：

```bash
git status
git add .
git commit -m "your message"
git push
```

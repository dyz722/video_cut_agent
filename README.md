# video_cut_agent

`video_cut_agent` 是一个基于 Claude Code harness 思想实现的视频剪辑 agent：模型负责理解目标、规划剪辑和调用工具，代码提供素材感知、timeline 决策清单、确定性 ffmpeg 渲染、质检、skills 和上下文管理。

核心原则：

- Agent 是模型，项目代码是 harness。
- 剪辑判断沉淀在 `skills/`，按需加载，不把所有知识塞进 system prompt。
- 模型只产出 `timeline*.json`，最终视频由确定性渲染器执行。
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
video-agent --help
video-cut-agent --help
```

### 本地开发安装

```bash
git clone https://github.com/dyz722/video_cut_agent.git
cd video_cut_agent
python -m pip install -e .
```

## 依赖

需要本机安装：

- Python 3.10+
- `ffmpeg` 和 `ffprobe`
- 可用的 Anthropic 兼容模型 API
- DashScope API key，用于 ASR、视觉理解和 TTS

首次运行时会提示输入必需配置，也可以提前创建 `.env`：

```bash
ANTHROPIC_API_KEY=
DASHSCOPE_API_KEY=
# ANTHROPIC_BASE_URL=
# MODEL_ID=claude-sonnet-4-6
# VL_MODEL=qwen3-vl-plus
# ASR_FILE_MODEL=fun-asr
# ASR_REALTIME_MODEL=fun-asr-realtime
# TTS_MODEL=cosyvoice-v3-flash
```

可选环境变量：

- `VIDEO_AGENT_WORKSPACE`：项目工作区根目录，默认是当前目录下的 `workspace/`
- `VIDEO_AGENT_HOME`：用户数据目录，默认 `~/.video-agent`
- `VIDEO_AGENT_LEARNED_SKILLS_DIR`：自动沉淀 skill 的保存目录
- `VIDEO_AGENT_ENV`：配置文件路径

## 快速开始

交互模式：

```bash
video-agent demo --materials ~/Videos/source/
```

进入 REPL 后可以直接描述任务：

```text
把这场直播切出 5 条 30 秒以内的带货短视频，突出痛点、卖点和优惠信息。
```

批处理模式：

```bash
video-agent demo \
  --materials ~/Videos/source/ \
  --batch "把这场直播切出 10 条带货短视频，每条给出成片路径和质检结论" \
  --auto
```

项目文件会放在：

```text
workspace/<project>/
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
6. `render_timeline` 调用 ffmpeg 渲染。
7. `qc_check` 和 `watch_video` 抽查成片，必要时返工。

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

之后处理相似任务时，agent 会在可用 skills 列表中看到这些经验，并与基础赛道 skill 一起加载。经验沉淀只保存可复用剪辑判断，不应保存密钥、客户隐私、原始转写大段文本或私有素材文件名。

## 内置 Skills

- `timeline-format`：`timeline*.json` 格式规范。
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

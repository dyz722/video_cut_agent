# Harness: configuration -- env, API keys (CLI interactive), model choices, project workspace.
"""
config.py

集中管理:
  - .env 加载与 CLI 交互式补全 API key
  - 模型选型 (主模型 + DashScope ASR/VL/TTS, 见 model-choose)
  - 当前项目工作区路径 (workspace/<project>/)
"""

import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
SKILLS_DIR = ROOT / "skills"
WORKSPACE_ROOT = ROOT / "workspace"

# -- 当前项目工作区 (main.py 启动时设置) --
PROJECT_DIR: Path = WORKSPACE_ROOT / "default"

# -- 运行模式 --
AUTO_MODE = False          # True: 渲染不需要人审 (批处理默认)
TOKEN_THRESHOLD = 100000

REQUIRED_KEYS = {
    "ANTHROPIC_API_KEY": "主模型 API key (Anthropic 兼容, 驱动 agent loop)",
    "DASHSCOPE_API_KEY": "阿里 DashScope API key (fun-asr / qwen-vl / cosyvoice)",
}
OPTIONAL_KEYS = {
    "ANTHROPIC_BASE_URL": "主模型 Base URL (Anthropic 兼容代理, 回车跳过)",
    "MODEL_ID": "主模型 ID (回车默认 claude-sonnet-4-6)",
}


def ensure_config():
    """加载 .env; 缺少的 key 在 CLI 交互式询问并写回 .env。"""
    load_dotenv(ENV_PATH, override=True)
    new_lines = []
    for key, desc in REQUIRED_KEYS.items():
        if not os.getenv(key):
            val = input(f"[配置] 请输入 {key} ({desc}): ").strip()
            if not val:
                raise SystemExit(f"缺少必需配置 {key}, 退出。")
            os.environ[key] = val
            new_lines.append(f"{key}={val}")
    for key, desc in OPTIONAL_KEYS.items():
        if not os.getenv(key):
            val = input(f"[配置] {key} ({desc}): ").strip()
            if val:
                os.environ[key] = val
                new_lines.append(f"{key}={val}")
    if new_lines:
        with open(ENV_PATH, "a") as f:
            f.write("\n" + "\n".join(new_lines) + "\n")
        print(f"[配置] 已保存到 {ENV_PATH}")
    # Anthropic 兼容代理时清掉 AUTH_TOKEN 干扰 (同 learn-claude-code 模式)
    if os.getenv("ANTHROPIC_BASE_URL"):
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


# -- 模型选型 (model-choose) --
def main_model() -> str:
    return os.getenv("MODEL_ID", "claude-sonnet-4-6")


def vl_model() -> str:
    return os.getenv("VL_MODEL", "qwen3-vl-plus")


def asr_file_model() -> str:
    return os.getenv("ASR_FILE_MODEL", "fun-asr")            # 录音文件识别 (需公网 URL)


def asr_realtime_model() -> str:
    return os.getenv("ASR_REALTIME_MODEL", "fun-asr-realtime")  # 本地文件流式识别


def tts_model() -> str:
    return os.getenv("TTS_MODEL", "cosyvoice-v3-flash")


_client = None


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL") or None)
    return _client


def set_project(name: str) -> Path:
    """初始化/切换项目工作区, 返回项目目录。"""
    global PROJECT_DIR
    PROJECT_DIR = WORKSPACE_ROOT / name
    for sub in ("materials", "analysis", "analysis/frames", "output", ".cache"):
        (PROJECT_DIR / sub).mkdir(parents=True, exist_ok=True)
    return PROJECT_DIR


def safe_path(p: str) -> Path:
    """路径限制在项目工作区内 (绝对路径若在工作区内也放行)。"""
    path = Path(p)
    if not path.is_absolute():
        path = (PROJECT_DIR / p).resolve()
    else:
        path = path.resolve()
    if not path.is_relative_to(PROJECT_DIR):
        raise ValueError(f"Path escapes project workspace: {p}")
    return path

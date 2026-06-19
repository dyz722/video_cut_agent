# Harness: configuration -- env, API keys (CLI interactive), model choices, project workspace.
"""
config.py

集中管理:
  - .env 加载与 CLI 交互式补全 API key
  - 模型选型 (主模型 + DashScope ASR/VL/TTS, 见 model-choose)
  - 当前项目工作区路径 (默认是启动 veoai 时所在目录)
"""

import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from .model_client import OpenAICompatClient

ROOT = Path(__file__).resolve().parent.parent
USER_DATA_DIR = Path(os.getenv("VIDEO_AGENT_HOME", "~/.video-agent")).expanduser()
ENV_PATH = Path(os.getenv(
    "VIDEO_AGENT_ENV",
    ROOT / ".env" if (ROOT / ".env.example").exists() else USER_DATA_DIR / ".env",
)).expanduser()
SKILLS_DIR = ROOT / "skills"
LEARNED_SKILLS_DIR = Path(os.getenv(
    "VIDEO_AGENT_LEARNED_SKILLS_DIR",
    USER_DATA_DIR / "skills" / "_learned",
)).expanduser()
SKILLS_DIRS = [SKILLS_DIR, LEARNED_SKILLS_DIR]
WORKSPACE_ROOT = Path(os.getenv("VIDEO_AGENT_WORKSPACE", Path.cwd())).expanduser()

# -- 当前项目工作区 (main.py 启动时设置) --
PROJECT_DIR: Path = WORKSPACE_ROOT

# -- 运行模式 --
AUTO_MODE = False          # True: 渲染不需要人审 (批处理默认)
TOKEN_THRESHOLD = 100000
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-4o"

DASHSCOPE_DESC = "阿里 DashScope API key (fun-asr / qwen-vl / cosyvoice)"
DASHSCOPE_CN_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DASHSCOPE_INTL_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


def _write_env_values(values: dict[str, str]):
    """Update known keys in ENV_PATH without duplicating entries."""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    if remaining and out and out[-1].strip():
        out.append("")
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(out).rstrip() + "\n")


def reset_client():
    """Drop cached main model client after model config changes."""
    global _client
    _client = None


def main_model_protocol() -> str:
    proto = os.getenv("MODEL_API_PROTOCOL", "").strip().lower()
    if proto in ("openai", "openai-compatible", "chat-completions"):
        return "openai"
    if proto in ("anthropic", "anthropic-compatible", "messages"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY"):
        return "openai"
    return "anthropic"


def _default_model_for(protocol: str) -> str:
    return DEFAULT_OPENAI_MODEL if protocol == "openai" else DEFAULT_ANTHROPIC_MODEL


def _prompt_choice(prompt: str, current: str, choices: dict[str, str]) -> str:
    labels = " / ".join(f"{k}={v}" for k, v in choices.items())
    value = input(f"{prompt} ({labels}, 当前 {current}, 回车保留): ").strip().lower()
    if not value:
        return current
    return choices.get(value, value)


def dashscope_region() -> str:
    region = os.getenv("DASHSCOPE_REGION", "cn").strip().lower()
    if region in ("intl", "international", "overseas", "global", "sg", "singapore"):
        return "intl"
    return "cn"


def _dashscope_key_name(region: str) -> str:
    return "DASHSCOPE_API_KEY_INTL" if region == "intl" else "DASHSCOPE_API_KEY_CN"


def _dashscope_base_name(region: str) -> str:
    return "DASHSCOPE_BASE_URL_INTL" if region == "intl" else "DASHSCOPE_BASE_URL_CN"


def _dashscope_default_base(region: str) -> str:
    return DASHSCOPE_INTL_BASE_URL if region == "intl" else DASHSCOPE_CN_BASE_URL


def dashscope_api_key(region: str | None = None) -> str:
    region = region or dashscope_region()
    return (os.getenv(_dashscope_key_name(region))
            or os.getenv("DASHSCOPE_API_KEY")
            or "")


def dashscope_base_url(region: str | None = None) -> str:
    region = region or dashscope_region()
    return os.getenv(_dashscope_base_name(region), _dashscope_default_base(region)).rstrip("/")


def dashscope_tts_url(region: str | None = None) -> str:
    return dashscope_base_url(region) + "/services/audio/tts/SpeechSynthesizer"


def apply_dashscope_config(region: str | None = None) -> tuple[str, str]:
    """Apply DashScope api_key/base URL to the dashscope SDK and return them."""
    region = region or dashscope_region()
    key = dashscope_api_key(region)
    base_url = dashscope_base_url(region)
    if not key:
        raise RuntimeError(f"Missing DashScope API key for region '{region}'. "
                           "Run /dashscope to configure it.")
    os.environ["DASHSCOPE_API_KEY"] = key
    try:
        import dashscope
        dashscope.api_key = key
        # Native DashScope SDK uses this base URL for non-compatible APIs.
        dashscope.base_http_api_url = base_url
    except Exception:
        pass
    return key, base_url


def configure_dashscope(force: bool = False):
    """Prompt for domestic/international DashScope endpoints and keys."""
    load_dotenv(ENV_PATH, override=True)
    current_region = dashscope_region()
    if not force and dashscope_api_key(current_region):
        apply_dashscope_config(current_region)
        return

    print("\n[DashScope 配置] 支持国内/海外两套 endpoint 和 key")
    region = _prompt_choice(
        "[DashScope 配置] 当前网络环境",
        current_region,
        {"1": "cn", "2": "intl", "cn": "cn", "intl": "intl", "overseas": "intl"},
    )
    if region not in ("cn", "intl"):
        raise SystemExit(f"不支持的 DashScope 区域: {region}")

    values = {"DASHSCOPE_REGION": region}
    for r, label in (("cn", "国内"), ("intl", "海外")):
        base_name = _dashscope_base_name(r)
        key_name = _dashscope_key_name(r)
        current_base = os.getenv(base_name, _dashscope_default_base(r))
        current_key = os.getenv(key_name, os.getenv("DASHSCOPE_API_KEY", ""))
        base = input(f"[DashScope 配置] {label} Base URL "
                     f"(当前 {current_base}, 回车保留): ").strip() or current_base
        key_hint = f"当前 {_mask(current_key)}, 回车保留" if current_key else "回车跳过"
        key = input(f"[DashScope 配置] {label} API key ({key_hint}): ").strip() or current_key
        values[base_name] = base
        if key:
            values[key_name] = key

    active_key = values.get(_dashscope_key_name(region)) or dashscope_api_key(region)
    if active_key:
        values["DASHSCOPE_API_KEY"] = active_key  # Backward-compatible active key.
        os.environ["DASHSCOPE_API_KEY"] = active_key
    for key, val in values.items():
        os.environ[key] = val
    _write_env_values(values)
    if active_key:
        apply_dashscope_config(region)
        print(f"[DashScope 配置] 已保存到 {ENV_PATH}; 当前区域: {region}; "
              f"Base URL: {dashscope_base_url(region)}")
    else:
        print("[DashScope 配置] 未配置当前区域 key；转写、视觉理解、TTS 暂不可用。")


def configure_main_model(force: bool = False):
    """Prompt for main model protocol/url/key/model and persist it."""
    load_dotenv(ENV_PATH, override=True)
    current_protocol = main_model_protocol()
    protocol = current_protocol
    current_key = os.getenv("OPENAI_API_KEY" if protocol == "openai" else "ANTHROPIC_API_KEY", "")
    if not force and current_key:
        return

    print("\n[主模型配置] 支持 Anthropic-compatible 和 OpenAI-compatible API")
    protocol = _prompt_choice(
        "[主模型配置] 接口协议",
        current_protocol,
        {"1": "anthropic", "2": "openai", "anthropic": "anthropic", "openai": "openai"},
    )
    if protocol not in ("anthropic", "openai"):
        raise SystemExit(f"不支持的主模型协议: {protocol}")

    if protocol == "openai":
        key_name, url_name = "OPENAI_API_KEY", "OPENAI_BASE_URL"
        default_url = "https://api.openai.com/v1"
        url_label = "OpenAI-compatible Base URL"
    else:
        key_name, url_name = "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"
        default_url = ""
        url_label = "Anthropic-compatible Base URL"

    current_key = os.getenv(key_name, "")
    current_url = os.getenv(url_name, default_url)
    current_model = (os.getenv("MODEL_ID", _default_model_for(protocol))
                     if protocol == current_protocol else _default_model_for(protocol))
    if current_url:
        prompt_url = f"[主模型配置] {url_label} (当前 {current_url}, 回车保留): "
    else:
        prompt_url = f"[主模型配置] {url_label} (回车使用官方默认): "
    base_url = input(prompt_url).strip()
    if not base_url:
        base_url = current_url

    key_hint = f"当前 {_mask(current_key)}, 回车保留" if current_key else "必填"
    api_key = input(f"[主模型配置] API key ({key_hint}): ").strip()
    if not api_key:
        api_key = current_key
    if not api_key:
        raise SystemExit("缺少主模型 API key, 退出。")

    model_id = input(f"[主模型配置] 模型 ID (当前 {current_model}, 回车保留): ").strip()
    if not model_id:
        model_id = current_model or _default_model_for(protocol)

    os.environ["MODEL_API_PROTOCOL"] = protocol
    os.environ[key_name] = api_key
    os.environ["MODEL_ID"] = model_id
    if base_url:
        os.environ[url_name] = base_url
    else:
        os.environ.pop(url_name, None)
    if protocol == "anthropic" and base_url:
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    _write_env_values({
        "MODEL_API_PROTOCOL": protocol,
        url_name: base_url,
        key_name: api_key,
        "MODEL_ID": model_id,
    })
    reset_client()
    print(f"[主模型配置] 已保存到 {ENV_PATH}; 当前协议: {protocol}; 当前模型: {model_id}")


def ensure_config():
    """加载 .env; 缺少的 key 在 CLI 交互式询问并写回 .env。"""
    load_dotenv(ENV_PATH, override=True)
    configure_main_model(force=False)
    configure_dashscope(force=False)
    # Anthropic 兼容代理时清掉 AUTH_TOKEN 干扰 (同 learn-claude-code 模式)
    if os.getenv("ANTHROPIC_BASE_URL"):
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


# -- 模型选型 (model-choose) --
def main_model() -> str:
    return os.getenv("MODEL_ID", _default_model_for(main_model_protocol()))


def vl_model() -> str:
    return os.getenv("VL_MODEL", "qwen3-vl-plus")


def asr_file_model() -> str:
    return os.getenv("ASR_FILE_MODEL", "fun-asr")            # 录音文件识别 (需公网 URL)


def asr_realtime_model() -> str:
    return os.getenv("ASR_REALTIME_MODEL", "fun-asr-realtime")  # 本地文件流式识别


def tts_model() -> str:
    return os.getenv("TTS_MODEL", "cosyvoice-v3-flash")


_client = None


def client():
    global _client
    if _client is None:
        protocol = main_model_protocol()
        if protocol == "openai":
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY is required for OpenAI-compatible protocol.")
            _client = OpenAICompatClient(os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
                                         key)
        else:
            _client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL") or None)
    return _client


def set_project(name: str = ".") -> Path:
    """初始化/切换项目工作区, 返回项目目录。"""
    global PROJECT_DIR
    if name in ("", ".", "./"):
        PROJECT_DIR = WORKSPACE_ROOT.resolve()
    else:
        project_path = Path(name).expanduser()
        PROJECT_DIR = (project_path if project_path.is_absolute()
                       else WORKSPACE_ROOT / project_path).resolve()
    for sub in ("materials", "analysis", "analysis/frames", "output", ".cache"):
        (PROJECT_DIR / sub).mkdir(parents=True, exist_ok=True)
    return PROJECT_DIR


def safe_path(p: str) -> Path:
    """路径限制在项目工作区内 (绝对路径若在工作区内也放行)。"""
    path = Path(p)
    if not path.is_absolute():
        path = PROJECT_DIR / p
    else:
        path = path
    # abspath 只归一化 ..，不跟随 symlink；materials/ 里的外部素材软链需要被允许。
    path = Path(os.path.abspath(path))
    project_dir = Path(os.path.abspath(PROJECT_DIR))
    if not path.is_relative_to(project_dir):
        raise ValueError(f"Path escapes project workspace: {p}")
    return path

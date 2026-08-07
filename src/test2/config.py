"""
LLM Provider 配置
从 .env 读取 LLM_PROVIDER 和 LLM_MODEL → 查 providers.toml → 返回 LLM 实例

用法：只需在 .env 中设置：
    LLM_PROVIDER=openai       # 用哪家
    LLM_MODEL=gpt-4o          # 用哪个模型
    LLM_API_KEY=sk-xxx        # API Key
"""

import os
import tomllib
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录 = config.py 所在 src/test2/ 向上两级
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 统一从项目根目录加载 .env，避免依赖运行时 cwd（无论从哪个目录启动都能读到配置）
load_dotenv(PROJECT_ROOT / ".env")
#将.env中的环境变量加载到os.environ中(字典格式)

# 加载预设表
PRESETS_PATH = Path(__file__).parent / "providers.toml"
with open(PRESETS_PATH, "rb") as f:
    PRESETS = tomllib.load(f)

    #tomllib.load()将 TOML 文件解析成一个嵌套字典
    '''
    PRESETS = {
    "zhipu": {
        "protocol": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "models": ["glm-4-flash", "glm-4-plus", "glm-4-air"]
    },
    "openai": { ... },
    "deepseek": { ... },
    '''


def get_llm(provider: str | None = None, temperature: float | None = None):
    """
    获取 LLM 实例

    Args:
        provider: 供应商名称，不传则读 .env 中的 LLM_PROVIDER
        temperature: 温度参数，不传则读 .env 中的 LLM_TEMPERATURE

    Returns:
        LangChain ChatModel 实例
    """
    # 从环境变量读取
    provider = (provider or os.getenv("LLM_PROVIDER", "")).strip().lower()
    model = os.getenv("LLM_MODEL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if temperature is None:
        temperature = float(os.getenv("LLM_TEMPERATURE", "0"))

    if not provider:
        raise ValueError(
            "未设置 LLM_PROVIDER，请在 .env 中设置，例如:\n"
            "  LLM_PROVIDER=openai\n"
            f"  支持: {', '.join(PRESETS.keys())}"
        )

    # 查表
    preset = PRESETS.get(provider)
    #preset 是一个字典，包含了该供应商协议、 URL 和模型列表
    if not preset:
        raise ValueError(
            f"未知的 LLM_PROVIDER: '{provider}'\n"
            f"支持: {', '.join(PRESETS.keys())}\n"
            f"请在 .env 中设置 LLM_PROVIDER=上述之一"
        )

    protocol = preset.get("protocol", "openai")

    # Anthropic 协议：用 ChatAnthropic
    if protocol == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs: dict = {
            "model": model or preset.get("models", [""])[0],
            "temperature": temperature,
        }
        # 有 LLM_API_KEY 才显式传，否则交给 ChatAnthropic 回退 ANTHROPIC_API_KEY，避免 pydantic 校验失败
        if api_key:
            kwargs["api_key"] = api_key
        return ChatAnthropic(**kwargs)
    #有自定义密钥就手动传入，没有就不填密钥参数，交给框架自动读取环境变量

    
    # OpenAI 协议：用 ChatOpenAI（覆盖绝大多数供应商）
    if protocol == "openai":
        from langchain_openai import ChatOpenAI
        base_url = preset.get("base_url") or os.getenv("LLM_BASE_URL", "")
        return ChatOpenAI(
            model=model or preset.get("models", [""])[0],
            api_key=api_key or "placeholder",
            base_url=base_url,
            temperature=temperature,
        )

    raise ValueError(f"不支持的协议: {protocol}")

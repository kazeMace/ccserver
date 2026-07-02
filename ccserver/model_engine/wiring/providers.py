"""
providers — Provider 元数据单一事实源（Single Source of Truth, SSOT）。

设计意图：
  在重构前，一个 provider 的元数据（默认 api_type / env 变量名 / 默认 base_url）
  被复制在三个地方：
    - endpoint.py 的 _PROVIDER_DEFAULT_API_TYPE / _PROVIDER_ENV_VARS（硬编码字典）
    - providers/registry.py 的 _init_defaults（OpenAIProvider 构造参数）
    - info/catalog.py 的 ModelInfo.provider
  三处各自维护、容易漂移（曾出现 endpoint 认得 deepseek、但 registry 没注册的不一致）。

  本模块把 provider 的元数据集中定义一次（PROVIDER_SPECS），
  endpoint.resolve() 从这里读取以推断 api_type / base_url / api_key，
  新增 provider 只需改这一个列表。

依赖约束（重要）：
  本模块是「叶子模块」，只依赖标准库 + dataclass，
  不 import endpoint / providers / adapters，避免循环依赖。
  api_type 常量（API_TYPE_ANTHROPIC / API_TYPE_OPENAI）定义在本模块，
  endpoint.py 反过来从这里 import 并 re-export（保持旧 import 路径不破）。

provider_catalog — single source of truth for provider metadata.
Defines api_type constants + ProviderSpec list once; endpoint.resolve() reads
from here, so adding a provider touches only this file.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── api_type 合法值（从 endpoint.py 迁移到此处，作为 SSOT 的一部分）──────────────────
# api_type 决定「用哪个 Adapter 实现」，是技术协议维度。
API_TYPE_ANTHROPIC = "anthropic-messages"
API_TYPE_OPENAI = "openai-completions"

# 新增 api_type 常量（扩展协议支持）
# New api_type constants (extended protocol support)
API_TYPE_RESPONSES = "openai-responses"   # OpenAI Responses API
API_TYPE_OLLAMA    = "ollama"             # Ollama 本地推理
API_TYPE_LITELLM   = "litellm"            # LiteLLM 代理层


@dataclass(frozen=True)
class ProviderSpec:
    """
    单个 provider 的元数据描述（不可变）。

    frozen=True 确保注册后不会被意外修改，可安全跨线程共享。

    Attributes:
        id:               provider 唯一标识，如 "anthropic"、"openai"（小写）
        name:             人类可读名称，如 "Anthropic"、"OpenAI"
        api_type:         默认协议类型（决定用哪个 Adapter），
                          取值 API_TYPE_ANTHROPIC / API_TYPE_OPENAI
        default_base_url: 默认 API 端点；空字符串表示「无内置默认，需从 env 或调用方取」
        env_api_key:      API 密钥的环境变量名；空字符串表示「该 provider 不需要 key」
        env_base_url:     base_url 的环境变量名；空字符串表示「无对应 env」
    """

    id: str
    name: str
    api_type: str
    default_base_url: str
    env_api_key: str
    env_base_url: str


# ── 内置 provider 清单（SSOT）─────────────────────────────────────────────────────
# 只列「系统实际支持并会注册」的 provider。
# 注意：此处不含 deepseek / google —— 它们没有专属 Provider 实现，
#       如需使用请走 generic（OpenAI 兼容）provider 自行配置 base_url / api_key。
PROVIDER_SPECS: list[ProviderSpec] = [
    # Anthropic 原生协议
    ProviderSpec(
        id="anthropic",
        name="Anthropic",
        api_type=API_TYPE_ANTHROPIC,
        default_base_url="",  # anthropic SDK 自带默认端点
        env_api_key="ANTHROPIC_API_KEY",
        env_base_url="ANTHROPIC_BASE_URL",
    ),
    # OpenAI 官方
    ProviderSpec(
        id="openai",
        name="OpenAI",
        api_type=API_TYPE_OPENAI,
        default_base_url="https://api.openai.com/v1",
        env_api_key="OPENAI_API_KEY",
        env_base_url="OPENAI_BASE_URL",
    ),
    # OpenRouter（OpenAI 兼容聚合）
    ProviderSpec(
        id="openrouter",
        name="OpenRouter",
        api_type=API_TYPE_OPENAI,
        default_base_url="https://openrouter.ai/api/v1",
        env_api_key="OPENROUTER_API_KEY",
        env_base_url="OPENROUTER_BASE_URL",
    ),
    # Ollama 本地（无需 API Key，使用专属 ollama api_type）
    ProviderSpec(
        id="ollama",
        name="Ollama",
        api_type=API_TYPE_OLLAMA,
        default_base_url="http://localhost:11434",
        env_api_key="",  # 本地服务不需要 key
        env_base_url="OLLAMA_BASE_URL",
    ),
    # LM Studio 本地（无需 API Key）
    ProviderSpec(
        id="lmstudio",
        name="LM Studio",
        api_type=API_TYPE_OPENAI,
        default_base_url="http://localhost:1234/v1",
        env_api_key="",  # 本地服务不需要 key
        env_base_url="LMSTUDIO_BASE_URL",
    ),
    # One API（自建聚合，base_url 通常从 env 提供）
    ProviderSpec(
        id="oneapi",
        name="One API",
        api_type=API_TYPE_OPENAI,
        default_base_url="",  # 无内置默认，从 ONEAPI_BASE_URL 读取
        env_api_key="ONEAPI_API_KEY",
        env_base_url="ONEAPI_BASE_URL",
    ),
    # 通用 OpenAI 兼容端点（base_url / key 由调用方或 env 提供）
    ProviderSpec(
        id="generic",
        name="Generic OpenAI Compatible",
        api_type=API_TYPE_OPENAI,
        default_base_url="",  # 无内置默认，由调用方或 GENERIC_BASE_URL 提供
        env_api_key="GENERIC_API_KEY",
        env_base_url="GENERIC_BASE_URL",
    ),
    # DeepSeek
    ProviderSpec(
        id="deepseek", name="DeepSeek",
        api_type=API_TYPE_OPENAI,
        default_base_url="https://api.deepseek.com",
        env_api_key="DEEPSEEK_API_KEY",
        env_base_url="DEEPSEEK_BASE_URL",
    ),
    # Kimi (Moonshot AI)
    ProviderSpec(
        id="kimi", name="Kimi (Moonshot)",
        api_type=API_TYPE_OPENAI,
        default_base_url="https://api.moonshot.cn/v1",
        env_api_key="MOONSHOT_API_KEY",
        env_base_url="MOONSHOT_BASE_URL",
    ),
    # Mimo
    ProviderSpec(
        id="mimo", name="Mimo",
        api_type=API_TYPE_OPENAI,
        default_base_url="https://api.mimo.ai/v1",
        env_api_key="MIMO_API_KEY",
        env_base_url="MIMO_BASE_URL",
    ),
    # Qwen (Alibaba Cloud DashScope)
    ProviderSpec(
        id="qwen", name="Qwen (Alibaba)",
        api_type=API_TYPE_OPENAI,
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        env_api_key="DASHSCOPE_API_KEY",
        env_base_url="DASHSCOPE_BASE_URL",
    ),
    # Gemini (Google)
    ProviderSpec(
        id="gemini", name="Gemini (Google)",
        api_type=API_TYPE_OPENAI,
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        env_api_key="GOOGLE_API_KEY",
        env_base_url="GEMINI_BASE_URL",
    ),
    # LiteLLM 代理层
    ProviderSpec(
        id="litellm", name="LiteLLM",
        api_type=API_TYPE_LITELLM,
        default_base_url="",
        env_api_key="LITELLM_API_KEY",
        env_base_url="LITELLM_BASE_URL",
    ),
]


# ── 按 id 索引（供 O(1) 查询）─────────────────────────────────────────────────────
# 断言：id 不得重复，否则字典构造会静默覆盖、导致难以排查的注册丢失。
def _build_spec_index(specs: list[ProviderSpec]) -> dict[str, ProviderSpec]:
    """
    把 PROVIDER_SPECS 列表构造成 {id: ProviderSpec} 字典，并校验 id 唯一。

    Args:
        specs: ProviderSpec 列表。
    Returns:
        {provider_id: ProviderSpec} 字典。
    Raises:
        AssertionError: 存在重复的 provider id。
    """
    index: dict[str, ProviderSpec] = {}
    for spec in specs:
        assert spec.id, "ProviderSpec.id 不能为空"
        assert spec.id not in index, f"重复的 provider id: {spec.id!r}"
        index[spec.id] = spec
    return index


PROVIDER_SPEC_BY_ID: dict[str, ProviderSpec] = _build_spec_index(PROVIDER_SPECS)

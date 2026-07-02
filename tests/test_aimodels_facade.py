"""tests/test_aimodels_facade.py — model_engine 门面与结构一致性。

验证包重构后的不变量：
  - 门面公共 API 齐全（新类型体系）
  - Provider 继承层次正确
  - 协议适配器在 adapters/ 子包（新名）
  - L1 客户端在 client.py
  - 旧 ccserver.model 包已不存在
"""

import importlib


def test_facade_exports_public_api():
    """门面 re-export 新 API。"""
    import ccserver.model_engine as m
    new_names = [
        "UnifiedResponse", "UnifiedTextBlock", "UnifiedThinkingBlock",
        "UnifiedToolUseBlock", "UnifiedUsage", "UnifiedStreamDelta",
        "TransientLLMError", "is_transient", "wrap_transient",
        "LLMProvider", "ProtocolAdapter", "ProtocolCodec",
        "LLMCaller", "AdapterFactory", "ModelEndpoint",
    ]
    for name in new_names:
        assert hasattr(m, name), f"门面缺少 {name}"


def test_adapters_live_under_adapters_subpackage():
    """协议适配器在 model_engine/adapters/ 下（按协议分组）。"""
    for mod in [
        "ccserver.model_engine.adapters.anthropic_sdk",
        "ccserver.model_engine.adapters.chat_completions",
    ]:
        importlib.import_module(mod)  # 不抛即通过


def test_client_module_hosts_llmcaller():
    """L1 客户端在 model_engine/client.py。"""
    from ccserver.model_engine.client import LLMCaller
    assert LLMCaller is not None


def test_legacy_model_package_gone():
    """旧 ccserver.model 包不应再存在。"""
    try:
        importlib.import_module("ccserver.model")
        assert False, "旧 ccserver.model 包不应再存在"
    except ModuleNotFoundError:
        pass


def test_provider_taxonomy_via_facade():
    """LLMProvider 是根基类，BaseLLMProvider 继承它。"""
    from ccserver.model_engine import LLMProvider, BaseLLMProvider
    assert issubclass(BaseLLMProvider, LLMProvider)

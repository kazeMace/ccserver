# tests/test_vision_service.py
"""tests/test_vision_service.py — vision 服务的 VLM 选择 + describe/locate。"""

import pytest
from unittest.mock import MagicMock

from ccserver.builtins.tools import vision


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前清空 VLM adapter 进程级缓存，避免串扰。"""
    vision._cached_vlm = None
    yield
    vision._cached_vlm = None


def test_resolve_uses_main_adapter_when_supports_image():
    """主 adapter 支持图像 → 直接用主 adapter+model。"""
    main = MagicMock()
    main.supports_image = True
    adapter, model = vision._resolve_vlm_adapter("claude-sonnet-4-6", main)
    assert adapter is main and model == "claude-sonnet-4-6"


def test_resolve_default_openai_when_key(monkeypatch):
    """主不支持图像 + 无 VLM 配置 + 有 OPENAI_API_KEY → gpt-4o。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # VLM 配置为空
    cfg = MagicMock(); cfg.api_key = None; cfg.base_url = None; cfg.provider = None
    monkeypatch.setattr("ccserver.configuration.get_process_config", lambda: MagicMock(vlm=cfg))
    sentinel = MagicMock()
    monkeypatch.setattr("ccserver.model_engine.providers.openai_chat.OpenAIChatProvider.from_config",
                        classmethod(lambda cls, **kw: sentinel))
    main = MagicMock(); main.supports_image = False
    adapter, model = vision._resolve_vlm_adapter("deepseek-chat", main)
    assert adapter is sentinel and model == "gpt-4o"


def test_resolve_uses_vlm_config_when_set(monkeypatch):
    """tier-2：显式 VLM 配置（api_key 等）→ 经 ModelEndpoint+AdapterFactory 构建，model=vlm.model_id。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = MagicMock()
    cfg.api_key = "sk-vlm"; cfg.base_url = None; cfg.provider = None
    cfg.model_id = "gpt-4o"; cfg.api_type = None
    monkeypatch.setattr("ccserver.configuration.get_process_config", lambda: MagicMock(vlm=cfg))
    sentinel = MagicMock()
    # 拦截 AdapterFactory.build，避免真建 client；断言走的是配置分支
    monkeypatch.setattr("ccserver.model_engine.wiring.factory.AdapterFactory.build",
                        staticmethod(lambda ep: sentinel))
    main = MagicMock(); main.supports_image = False
    adapter, model = vision._resolve_vlm_adapter("deepseek-chat", main)
    assert adapter is sentinel and model == "gpt-4o"


def test_resolve_default_anthropic_when_only_anthropic_key(monkeypatch):
    """无 openai key、有 anthropic key → claude-sonnet-4-6。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    cfg = MagicMock(); cfg.api_key = None; cfg.base_url = None; cfg.provider = None
    monkeypatch.setattr("ccserver.configuration.get_process_config", lambda: MagicMock(vlm=cfg))
    sentinel = MagicMock()
    monkeypatch.setattr("ccserver.model_engine.providers.anthropic.get_default_provider", lambda: sentinel)
    main = MagicMock(); main.supports_image = False
    adapter, model = vision._resolve_vlm_adapter("deepseek-chat", main)
    assert adapter is sentinel and model == "claude-sonnet-4-6"


def test_resolve_raises_when_no_credentials(monkeypatch):
    """无主图像、无 VLM 配置、无 key → RuntimeError。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = MagicMock(); cfg.api_key = None; cfg.base_url = None; cfg.provider = None
    monkeypatch.setattr("ccserver.configuration.get_process_config", lambda: MagicMock(vlm=cfg))
    main = MagicMock(); main.supports_image = False
    with pytest.raises(RuntimeError, match="VLM unavailable"):
        vision._resolve_vlm_adapter("deepseek-chat", main)


@pytest.mark.asyncio
async def test_describe_image_uses_resolved_adapter(monkeypatch):
    """describe_image 经 _resolve_vlm_adapter 选主 adapter 并产出文本。"""
    main = MagicMock(); main.supports_image = True

    async def fake_invoke(self, messages, **kw):
        resp = MagicMock()
        b = MagicMock(); b.type = "text"; b.text = "看到一个按钮"
        resp.content = [b]
        return resp
    monkeypatch.setattr(vision.LLMCaller, "invoke", fake_invoke)

    out = await vision.describe_image("ZmFrZQ==", prompt="描述", main_model="claude-sonnet-4-6", main_adapter=main)
    assert out == "看到一个按钮"


@pytest.mark.asyncio
async def test_locate_element_parses_json(monkeypatch):
    """locate_element 解析模型返回的 JSON。"""
    main = MagicMock(); main.supports_image = True

    async def fake_invoke(self, messages, **kw):
        resp = MagicMock()
        b = MagicMock(); b.type = "text"; b.text = '{"found": true, "x": 10, "y": 20}'
        resp.content = [b]
        return resp
    monkeypatch.setattr(vision.LLMCaller, "invoke", fake_invoke)

    out = await vision.locate_element("ZmFrZQ==", "按钮", 800, 600, main_model="m", main_adapter=main)
    assert out == {"found": True, "x": 10, "y": 20}


@pytest.mark.asyncio
async def test_locate_element_regex_fallback(monkeypatch):
    """模型输出含杂质时正则兜底提取 JSON。"""
    main = MagicMock(); main.supports_image = True

    async def fake_invoke(self, messages, **kw):
        resp = MagicMock()
        b = MagicMock(); b.type = "text"; b.text = '好的：{"found": false} 以上'
        resp.content = [b]
        return resp
    monkeypatch.setattr(vision.LLMCaller, "invoke", fake_invoke)

    out = await vision.locate_element("ZmFrZQ==", "x", 10, 10, main_model="m", main_adapter=main)
    assert out == {"found": False}

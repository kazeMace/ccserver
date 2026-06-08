"""
test_provider_plugin — 测试 ProviderPlugin 和 ProviderRegistry。

覆盖：
- ProviderRegistry 注册、查询、创建 adapter
- 内置插件加载（anthropic、openai、qwen、zhipuai 等）
- get_adapter() 向后兼容
- 不存在的 provider 报错
"""
import pytest
from ccserver.model.plugins.registry import get_provider_registry
from ccserver.model.factory import get_adapter


class TestProviderRegistry:
    """测试 ProviderRegistry 核心功能。"""

    def test_builtin_providers_registered(self):
        """验证所有内置 provider 注册成功。"""
        reg = get_provider_registry()
        providers = reg.list_providers()
        # 至少包含这些核心 provider
        assert "anthropic" in providers
        assert "openai" in providers
        assert "openrouter" in providers
        assert "qwen" in providers
        assert "zhipuai" in providers

    def test_get_existing_provider(self):
        """查询已注册的 provider。"""
        reg = get_provider_registry()
        plugin = reg.get("anthropic")
        assert plugin is not None
        assert plugin.id == "anthropic"
        assert plugin.name == "Anthropic"

    def test_get_nonexistent_provider(self):
        """查询不存在的 provider 返回 None。"""
        reg = get_provider_registry()
        assert reg.get("nonexistent") is None

    def test_get_case_insensitive(self):
        """provider id 查询大小写不敏感。"""
        reg = get_provider_registry()
        assert reg.get("Anthropic") is not None
        assert reg.get("ANTHROPIC") is not None

    def test_list_providers_sorted(self):
        """list_providers 返回排序列表。"""
        reg = get_provider_registry()
        providers = reg.list_providers()
        assert providers == sorted(providers)

    def test_create_adapter_anthropic(self):
        """创建 AnthropicAdapter。"""
        reg = get_provider_registry()
        adapter = reg.create_adapter("anthropic")
        from ccserver.model.anthropic_adapter import AnthropicAdapter
        assert isinstance(adapter, AnthropicAdapter)

    def test_create_adapter_unknown_raises(self):
        """未知 provider 抛出 ValueError。"""
        reg = get_provider_registry()
        with pytest.raises(ValueError, match="Unknown provider"):
            reg.create_adapter("unknown_provider_xyz")

    def test_plugin_info(self):
        """get_plugin_info 返回摘要。"""
        reg = get_provider_registry()
        info = reg.get_plugin_info()
        assert isinstance(info, list)
        assert len(info) >= 5
        for item in info:
            assert "id" in item
            assert "name" in item
            assert "transport" in item


class TestProviderPluginDetails:
    """测试各 provider 插件的 transport_type 和默认配置。"""

    def test_anthropic_transport_type(self):
        reg = get_provider_registry()
        plugin = reg.get("anthropic")
        assert plugin.transport_type == "anthropic"

    def test_openai_transport_type(self):
        reg = get_provider_registry()
        plugin = reg.get("openai")
        assert plugin.transport_type == "openai-compat"

    def test_openrouter_transport_type(self):
        reg = get_provider_registry()
        plugin = reg.get("openrouter")
        assert plugin.transport_type == "openai-compat"

    def test_qwen_transport_type(self):
        reg = get_provider_registry()
        plugin = reg.get("qwen")
        assert plugin.transport_type == "openai-compat"

    def test_zhipuai_transport_type(self):
        reg = get_provider_registry()
        plugin = reg.get("zhipuai")
        assert plugin.transport_type == "zhipuai"

    def test_volcano_transport_type(self):
        reg = get_provider_registry()
        plugin = reg.get("volcano")
        assert plugin.transport_type == "openai-compat"


class TestGetAdapterBackwardCompat:
    """测试 get_adapter() 向后兼容。"""

    def test_get_adapter_anthropic(self):
        """get_adapter("anthropic") 返回 AnthropicAdapter。"""
        from ccserver.model.anthropic_adapter import AnthropicAdapter
        adapter = get_adapter("anthropic")
        assert isinstance(adapter, AnthropicAdapter)

    def test_get_adapter_default(self):
        """get_adapter() 默认 provider。"""
        adapter = get_adapter()
        assert adapter is not None

    def test_get_adapter_unknown_raises(self):
        """未知 provider 抛出 ValueError。"""
        with pytest.raises(ValueError, match="Unknown provider"):
            get_adapter("unknown_provider_xyz")

    def test_get_adapter_case_insensitive(self):
        """大小写不敏感。"""
        adapter = get_adapter("ANTHROPIC")
        assert adapter is not None

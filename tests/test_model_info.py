"""
test_model_info — 测试 ModelInfo 和 ModelInfoRegistry。

覆盖：
- ModelInfo 冻结数据类的基本功能
- ModelInfo.supports() / supports_image / supports_video 便捷方法
- ModelInfoRegistry 注册、查询、按条件过滤
- 进程级单例 get_registry()
"""
import pytest
from ccserver.model_engine.metadata.model_info import ModelInfo
from ccserver.model_engine.metadata.model_info_registry import ModelInfoRegistry
from ccserver.model_engine.metadata.model_info_registry import get_registry


class TestModelInfo:
    """测试 ModelInfo 数据类。"""

    def test_default_input_type_is_text(self):
        """默认 input_types 为 {"text"}。"""
        info = ModelInfo(model_id="test-model")
        assert info.input_types == frozenset({"text"})

    def test_supports_image(self):
        """supports_image 属性检查。"""
        text_only = ModelInfo(model_id="text-model", input_types=frozenset({"text"}))
        assert not text_only.supports_image

        multimodal = ModelInfo(model_id="mm-model", input_types=frozenset({"text", "image"}))
        assert multimodal.supports_image

    def test_supports_video(self):
        """supports_video 属性检查。"""
        info = ModelInfo(model_id="video-model", input_types=frozenset({"text", "image", "video"}))
        assert info.supports_video

    def test_supports_file(self):
        """supports_file 属性检查（多模态模型支持 file）。"""
        info = ModelInfo(model_id="file-model", input_types=frozenset({"text", "file"}))
        assert info.supports_file

    def test_supports_generic(self):
        """通用 supports() 方法。"""
        info = ModelInfo(model_id="mm", input_types=frozenset({"text", "image"}))
        assert info.supports("text")
        assert info.supports("image")
        assert not info.supports("video")

    def test_frozen_dataclass(self):
        """ModelInfo 是冻结数据类，不可修改。"""
        info = ModelInfo(model_id="frozen")
        with pytest.raises(Exception):
            info.model_id = "changed"

    def test_repr(self):
        """ModelInfo 的 repr 应包含关键信息。"""
        info = ModelInfo(model_id="test", provider="anthropic")
        r = repr(info)
        assert "test" in r


class TestModelInfoRegistry:
    """测试 ModelInfoRegistry 注册表。"""

    def test_register_and_get(self):
        """注册后可通过 get() 查询。"""
        reg = ModelInfoRegistry()
        info = ModelInfo(model_id="m1", provider="test")
        reg.register(info)
        assert reg.get("m1") is info

    def test_get_nonexistent_returns_none(self):
        """查询不存在的模型返回 None。"""
        reg = ModelInfoRegistry()
        assert reg.get("nonexistent") is None

    def test_list_by_provider(self):
        """按 provider 过滤。"""
        reg = ModelInfoRegistry()
        reg.register(ModelInfo(model_id="a1", provider="p1"))
        reg.register(ModelInfo(model_id="a2", provider="p1"))
        reg.register(ModelInfo(model_id="b1", provider="p2"))
        assert len(reg.list_by_provider("p1")) == 2
        assert len(reg.list_by_provider("p2")) == 1
        assert len(reg.list_by_provider("p3")) == 0

    def test_list_by_input_type(self):
        """按 input_type 过滤。"""
        reg = ModelInfoRegistry()
        reg.register(ModelInfo(model_id="text1", input_types=frozenset({"text"})))
        reg.register(ModelInfo(model_id="img1", input_types=frozenset({"text", "image"})))
        reg.register(ModelInfo(model_id="img2", input_types=frozenset({"text", "image", "video"})))

        assert len(reg.list_by_input_type("text")) == 3
        assert len(reg.list_by_input_type("image")) == 2
        assert len(reg.list_by_input_type("video")) == 1

    def test_list_by_input_type_with_provider(self):
        """按 input_type + provider 组合过滤。"""
        reg = ModelInfoRegistry()
        reg.register(ModelInfo(model_id="claude", provider="anthropic",
                               input_types=frozenset({"text", "image"}), priority=100))
        reg.register(ModelInfo(model_id="haiku", provider="anthropic",
                               input_types=frozenset({"text", "image"}), priority=70))
        reg.register(ModelInfo(model_id="deepseek", provider="deepseek",
                               input_types=frozenset({"text"}), priority=30))

        results = reg.list_by_input_type_with_provider("image", "anthropic")
        assert len(results) == 2
        # 按 priority 降序
        assert results[0].model_id == "claude"

        results2 = reg.list_by_input_type_with_provider("image", "deepseek")
        assert len(results2) == 0

    def test_supports_check(self):
        """supports() 方法。"""
        reg = ModelInfoRegistry()
        reg.register(ModelInfo(model_id="mm", input_types=frozenset({"text", "image"})))
        assert reg.supports("mm", "image")
        assert not reg.supports("mm", "video")
        assert not reg.supports("unknown", "text")

    def test_register_bulk(self):
        """批量注册。"""
        reg = ModelInfoRegistry()
        reg.register_bulk([
            ModelInfo(model_id="a"),
            ModelInfo(model_id="b"),
            ModelInfo(model_id="c"),
        ])
        assert len(reg.list_all()) == 3

    def test_register_overwrite(self):
        """重复注册同一 model_id 会覆盖。"""
        reg = ModelInfoRegistry()
        reg.register(ModelInfo(model_id="m", name="v1"))
        reg.register(ModelInfo(model_id="m", name="v2"))
        assert reg.get("m").name == "v2"

    def test_list_all(self):
        """列出所有模型。"""
        reg = ModelInfoRegistry()
        reg.register(ModelInfo(model_id="a"))
        reg.register(ModelInfo(model_id="b"))
        assert len(reg.list_all()) == 2


class TestModelInfoRegistrySingleton:
    """测试进程级单例。"""

    def test_get_registry_returns_same_instance(self):
        """多次调用返回同一实例。"""
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_builtin_catalog_loaded(self):
        """内置模型目录自动加载。"""
        reg = get_registry()
        # 至少有 Anthropic 系列模型
        claude = reg.get("claude-sonnet-4-6")
        assert claude is not None
        assert claude.supports_image
        assert claude.provider == "anthropic"

    def test_deepseek_removed_from_catalog(self):
        """deepseek 已从内置目录移除（无专属 Provider）。"""
        reg = get_registry()
        assert reg.get("deepseek-chat") is None
        assert reg.get("gemini-2.5-pro-exp-03-25") is None

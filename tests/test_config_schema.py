"""
test_config_schema — 验证配置 schema 的默认值与 from_dict/as_dict 往返。

对应 plan Task A1。
"""

from ccserver.configuration.schema import CcServerConfig, ModelConfig


def test_defaults_construct():
    """空构造时各段默认值与旧 config.py 一致。"""
    cfg = CcServerConfig()
    assert cfg.model.model_id == "claude-sonnet-4-6"
    assert cfg.model.provider == "anthropic"
    assert cfg.vlm.model_id == "claude-sonnet-4-6"
    assert cfg.agent.main_round_limit == 100
    assert cfg.agent.sub_round_limit == 30
    assert cfg.agent.max_depth == 5
    assert cfg.agent.prompt_lib == "cc_reverse:v2.1.81"
    assert cfg.agent.language == "简体中文"
    assert cfg.compaction.threshold == 120000
    assert cfg.compaction.keep_recent == 20
    assert cfg.infra.storage_backend == "file"
    assert cfg.infra.mongo_db == "ccserver"
    assert cfg.tools.user_agent_team is False


def test_from_dict_partial_overrides_only_given():
    """部分覆盖：只改给定字段，其余保持默认。"""
    cfg = CcServerConfig.from_dict({"model": {"model_id": "gpt-4o"}})
    assert cfg.model.model_id == "gpt-4o"
    assert cfg.model.provider == "anthropic"          # 未给，保持默认
    assert cfg.agent.main_round_limit == 100          # 未给段，保持默认


def test_as_dict_roundtrip():
    """as_dict → from_dict 往返不丢字段。"""
    cfg = CcServerConfig.from_dict({"agent": {"language": "English"}})
    again = CcServerConfig.from_dict(cfg.as_dict())
    assert again.agent.language == "English"
    assert again.model.model_id == cfg.model.model_id
    assert again.infra.storage_backend == cfg.infra.storage_backend


def test_as_dict_paths_are_str():
    """infra 路径字段在 as_dict 中应为 str（JSON 序列化友好）。"""
    cfg = CcServerConfig()
    d = cfg.as_dict()
    assert isinstance(d["infra"]["sessions_base"], str)
    assert isinstance(d["infra"]["global_config_dir"], str)


def test_model_config_type():
    """ModelConfig 可独立构造。"""
    m = ModelConfig(model_id="x", api_type="anthropic-messages")
    assert m.model_id == "x"
    assert m.api_type == "anthropic-messages"

"""
test_config_env_map — 验证环境变量映射与类型转换。

对应 plan Task A3。
"""

from ccserver.configuration.env_map import ENV_MAP, apply_env


def test_env_overrides_model():
    """环境变量覆盖已有值。"""
    data = apply_env({"model": {"model_id": "x"}}, ENV_MAP, environ={"CCSERVER_MODEL": "gpt-4o"})
    assert data["model"]["model_id"] == "gpt-4o"


def test_env_int_and_bool_conversion():
    """int / bool 类型转换。"""
    data = apply_env(
        {},
        ENV_MAP,
        environ={"CCSERVER_MAIN_ROUNDS": "55", "CCSERVER_USER_AGENT_TEAM": "true"},
    )
    assert data["agent"]["main_round_limit"] == 55
    assert data["tools"]["user_agent_team"] is True


def test_env_bool_false():
    data = apply_env({}, ENV_MAP, environ={"CCSERVER_USER_AGENT_TEAM": "false"})
    assert data["tools"]["user_agent_team"] is False


def test_env_absent_no_key():
    """环境变量不存在时不写入对应键。"""
    data = apply_env({}, ENV_MAP, environ={})
    assert data == {}


def test_env_empty_string_skipped():
    """空字符串值跳过，不覆盖。"""
    data = apply_env({"model": {"model_id": "keep"}}, ENV_MAP, environ={"CCSERVER_MODEL": ""})
    assert data["model"]["model_id"] == "keep"


def test_env_nested_path_written():
    data = apply_env({}, ENV_MAP, environ={"CCSERVER_LOG_LEVEL": "INFO"})
    assert data["infra"]["log_level"] == "INFO"


def test_anthropic_api_key_alias():
    data = apply_env({}, ENV_MAP, environ={"ANTHROPIC_API_KEY": "sk-xxx"})
    assert data["model"]["api_key"] == "sk-xxx"

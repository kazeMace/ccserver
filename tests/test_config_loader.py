"""
test_config_loader — 验证三作用域层叠优先级与合并语义。

对应 plan Task A4。
"""

import json

from ccserver.configuration.loader import deep_merge, ProcessConfig, resolve_session


def test_deep_merge_scalar_override():
    base = {"model": {"model_id": "a", "api_key": "k"}}
    over = {"model": {"model_id": "b"}}
    assert deep_merge(base, over) == {"model": {"model_id": "b", "api_key": "k"}}


def test_deep_merge_does_not_mutate_inputs():
    base = {"model": {"model_id": "a"}}
    over = {"model": {"model_id": "b"}}
    deep_merge(base, over)
    assert base["model"]["model_id"] == "a"  # 未被改


def test_permissions_deny_union_allow_override():
    base = {"permissions": {"deny": ["X"], "allow": ["A"], "ask": ["Q1"]}}
    over = {"permissions": {"deny": ["Y"], "allow": ["B"], "ask": ["Q2"]}}
    out = deep_merge(base, over)
    assert set(out["permissions"]["deny"]) == {"X", "Y"}      # 并集
    assert out["permissions"]["allow"] == ["B"]                # 覆盖
    assert set(out["permissions"]["ask"]) == {"Q1", "Q2"}     # 并集


def test_permissions_allow_absent_keeps_base():
    base = {"permissions": {"allow": ["A"]}}
    over = {"permissions": {"deny": ["Y"]}}
    out = deep_merge(base, over)
    assert out["permissions"]["allow"] == ["A"]  # over 未给 allow，沿用 base


def test_process_load_env_over_file(tmp_path):
    gf = tmp_path / "settings.json"
    gf.write_text(json.dumps({"model": {"model_id": "from_file"}}))
    pc = ProcessConfig.load(global_file=gf, environ={"CCSERVER_MODEL": "from_env"})
    assert pc.model.model_id == "from_env"   # env 覆盖文件


def test_process_load_file_over_default(tmp_path):
    gf = tmp_path / "settings.json"
    gf.write_text(json.dumps({"agent": {"main_round_limit": 7}}))
    pc = ProcessConfig.load(global_file=gf, environ={})
    assert pc.agent.main_round_limit == 7
    assert pc.model.model_id == "claude-sonnet-4-6"  # 未覆盖，默认


def test_resolve_session_project_over_process_and_overrides(tmp_path):
    pf = tmp_path / "settings.local.json"
    pf.write_text(json.dumps({"agent": {"language": "English"}}))
    pc = ProcessConfig.load(global_file=tmp_path / "nope.json", environ={})
    sc = resolve_session(pc, project_file=pf, overrides={"model": {"model_id": "ovr"}})
    assert sc.agent.language == "English"     # 项目文件
    assert sc.model.model_id == "ovr"          # 编程式覆盖最高


def test_resolve_session_no_files(tmp_path):
    pc = ProcessConfig.load(global_file=tmp_path / "nope.json", environ={})
    sc = resolve_session(pc, project_file=tmp_path / "none.json")
    assert sc.model.model_id == "claude-sonnet-4-6"

"""Tests for Drama Engine admin developer console."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from drama_engine.admin_service.server.app import create_app
from drama_engine.application.catalog import GameCatalog
from drama_engine.application.script_repository import ScriptRepository


VALID_SCRIPT = """
meta:
  title: "测试剧本"
  description: "用于 admin service 测试"
  min_players: 1
  max_players: 1
roles:
- name: villager
  display_name: "村民"
  faction: good
  brief: "测试"
  scopes: [public]
  abilities: [vote]
players:
  count: 1
  initial_attrs:
    alive: true
  casting:
    type: shuffle
    distribution:
      villager: 1
scopes:
- name: public
  display_name: "全场"
  members: all
  delivery: immediate
initial_state:
  GAME:
    round: 0
flow:
  loop: false
  scenes:
  - name: start
    display_name: "开始"
    scene_type: narration
    scope: public
    dialogue_policy:
      mode: none
    response:
      mode: none
      cue: "开始"
    resolution:
      effects:
      - type: increment_state
        entity: GAME
        attr: round
        value: 1
referee:
  victory:
    rules: []
""".strip()


def make_client(tmp_path: Path) -> TestClient:
    """Create isolated admin app for tests."""
    repository = ScriptRepository(data_root=tmp_path / "admin_scripts")
    return TestClient(create_app(repository=repository))


def test_admin_lists_builtin_scripts_and_serves_frontend(tmp_path: Path) -> None:
    """管理端应能列出内置剧本并提供前端页面。"""
    client = make_client(tmp_path)

    health = client.get("/admin/api/health")
    assert health.status_code == 200
    assert health.json()["service"] == "drama_engine_admin"

    scripts = client.get("/admin/api/scripts")
    assert scripts.status_code == 200
    payload = scripts.json()
    assert payload["scripts"]
    assert any(item["source"] == "builtin" for item in payload["scripts"])

    page = client.get("/admin")
    assert page.status_code == 200
    assert "Drama Engine 管理控制台" in page.text
    assert "自然语言创建" in page.text


def test_builtin_script_catalog_reads_nested_script_library(tmp_path: Path) -> None:
    """目录和管理仓库应递归读取新版 scripts 分类目录。"""
    catalog = GameCatalog()
    games = catalog.list_games()
    repository = ScriptRepository(data_root=tmp_path / "admin_scripts")
    records = repository.list_scripts(include_builtin=True)

    # 迁移后脚本库只含 interactive_session 分类目录（旧脚本已归档 legacy_scripts）。
    assert any(game.game_id == "werewolf" for game in games)
    assert any("scripts/interactive_session/deduction/werewolf.yaml" in game.script_path for game in games)
    assert any(record.script_id == "builtin_gomoku" for record in records)
    assert all(not record.path.endswith(".preset.yaml") for record in records if record.source == "builtin")


def test_upload_validate_inspect_flow_and_playtest(tmp_path: Path) -> None:
    """上传剧本后应支持 validate、inspect、flow 和试玩测试。"""
    client = make_client(tmp_path)

    upload = client.post(
        "/admin/api/scripts/upload",
        data={"name": "测试剧本", "description": "admin upload"},
        files={"file": ("test.yaml", VALID_SCRIPT.encode("utf-8"), "application/x-yaml")},
    )
    assert upload.status_code == 200
    script = upload.json()["script"]
    script_id = script["script_id"]
    assert script["status"] in {"draft", "valid"}

    validate = client.post(f"/admin/api/scripts/{script_id}/validate")
    assert validate.status_code == 200
    validation = validate.json()["validation"]
    assert validation["summary"]["fatal"] == 0

    inspect = client.get(f"/admin/api/scripts/{script_id}/inspect")
    assert inspect.status_code == 200
    inspection = inspect.json()["inspection"]
    assert inspection["overview"]["title"] == "测试剧本"
    assert inspection["scenes"][0]["name"] == "start"

    flow = client.get(f"/admin/api/scripts/{script_id}/flow")
    assert flow.status_code == 200
    flow_data = flow.json()["flow"]
    assert flow_data["sequence"]["nodes"][0]["id"] == "start"
    assert "flowchart TD" in flow_data["sequence"]["mermaid"]
    assert flow_data["tree"]["id"] == "script"

    playtest = client.post(
        f"/admin/api/scripts/{script_id}/playtests",
        json={"mode": "dry_run", "human_player_count": 0, "step_mode": True},
    )
    assert playtest.status_code == 200
    playtest_payload = playtest.json()["playtest"]
    playtest_id = playtest_payload["playtest_id"]
    assert playtest_payload["runtime_session_id"]

    runtime = client.get(f"/admin/api/playtests/{playtest_id}/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["runtime"]["metadata"]["admin_playtest"] is True

    step = client.post(f"/admin/api/playtests/{playtest_id}/step", json={"count": 2})
    assert step.status_code == 200
    assert step.json()["playtest"]["current_step"] == 2


def test_generation_and_plugin_entrypoints(tmp_path: Path) -> None:
    """自然语言草稿入口和插件入口应可用且不直接发布剧本。"""
    client = make_client(tmp_path)

    generated = client.post("/admin/api/scripts/generate", json={"prompt": "创建一个 8 人狼人杀变体"})
    assert generated.status_code == 200
    payload = generated.json()
    assert payload["script"]["status"] in {"draft", "valid"}
    assert payload["script"]["source"] == "uploaded"
    assert payload["notes"]

    plugins = client.get("/admin/api/plugins")
    assert plugins.status_code == 200
    plugin_id = plugins.json()["plugins"][0]["plugin_id"]
    run = client.post(f"/admin/api/plugins/{plugin_id}/run", json={"input": {"prompt": "hello"}})
    assert run.status_code == 200
    assert run.json()["plugin_id"] == plugin_id

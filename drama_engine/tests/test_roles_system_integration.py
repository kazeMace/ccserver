"""测试 roles 系统的完整集成。

验证点：
1. DSL roles 字段正确编译
2. roles 信息存入 GAME.roles
3. GameInstance 自动分配推荐角色
4. ViewSnapshot 包含 roles 信息
"""
import pytest

from drama_engine.core.game_instance.factory import GameInstanceRegistry

_SCRIPT = "drama_engine/tests/fixtures/test_roles_system.yaml"


async def _make_instance(human: bool = False) -> object:
    """创建并 assign 一个 test_roles 实例。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="test_roles",
        script_path=_SCRIPT,
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"} if human else None,
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()
    return instance


@pytest.mark.asyncio
async def test_roles_system_end_to_end():
    """端到端测试：从 DSL 编译到 State 中的 roles 信息。"""
    instance = await _make_instance()

    # 验证：State 里有 GAME.roles
    state = instance.runtime.runner.game_state
    roles = state.get_attr("GAME", "roles")

    assert roles is not None, "GAME.roles 应该存在"
    assert "nora" in roles, "应该有 nora 角色"
    assert "marco" in roles, "应该有 marco 角色"

    # 验证：nora 角色的详细信息
    nora = roles["nora"]
    assert nora["display_name"] == "Nora Hampton"
    assert "精英律师" in nora["description"]
    assert nora["portrait_url"] == "https://assets.castloop.ai/characters/nora.jpg"
    assert nora["emoji"] == "⚖️"
    assert nora["voice_id"] == "en-US-JennyNeural"

    # 验证：Player_1 被分配了 nora 角色（推荐角色）
    player_role = state.get_attr("Player_1", "role")
    assert player_role == "nora", "Player_1 应该被分配 nora 角色"


@pytest.mark.asyncio
async def test_roles_manual_assignment():
    """测试手动角色分配（通过 assign 参数传入）。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="test_roles_manual",
        script_path=_SCRIPT,
        seat_ids=["Player_1"],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign(role_assignments={"Player_1": "marco"})

    # 验证：Player_1 被分配了 marco 而不是推荐的 nora
    state = instance.runtime.runner.game_state
    player_role = state.get_attr("Player_1", "role")
    assert player_role == "marco", "手动分配应该覆盖推荐"


@pytest.mark.asyncio
async def test_view_snapshot_contains_roles():
    """测试 project_context 返回的 state 中包含 roles 信息。"""
    instance = await _make_instance()

    # 获取 host snapshot
    snapshot = instance.project_context("host", "view")

    # roles 在 state.GAME.roles 中
    game_state = snapshot.get("state", {}).get("GAME", {})
    roles = game_state.get("roles")

    assert roles is not None, "state.GAME.roles 应该存在"
    assert "nora" in roles, "roles 应该包含 nora"
    assert "marco" in roles, "roles 应该包含 marco"

    # 验证：roles 格式正确
    nora = roles["nora"]
    assert nora["display_name"] == "Nora Hampton"
    assert nora["emoji"] == "⚖️"
    assert nora["voice_id"] == "en-US-JennyNeural"

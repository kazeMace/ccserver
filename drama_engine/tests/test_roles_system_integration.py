"""测试 roles 系统的完整集成。

验证点：
1. DSL roles 字段正确编译
2. roles 信息存入 GAME.roles
3. GameInstance 自动分配推荐角色
4. Actor 读取 role 信息并注入 persona
5. ViewSnapshot 包含 roles 信息
"""
import pytest


@pytest.mark.asyncio
async def test_roles_system_end_to_end():
    """端到端测试：从 DSL 编译到 actor 获取人设。"""
    from drama_engine.core.game_instance.registry import GameInstanceRegistry

    # 1. 创建 GameInstance（使用 test_roles_system.yaml）
    registry = GameInstanceRegistry()
    instance = registry.create(
        session_id="test_roles_e2e",
        script_path="drama_engine/tests/fixtures/test_roles_system.yaml",
    )

    # 2. assign（应该自动分配 nora 角色给 Player_1）
    await instance.assign()

    # 验证：State 里有 GAME.roles
    runner = instance.runtime.runner
    state = runner.game_state
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

    # 验证：Player_1 被分配了 nora 角色
    player_role = state.get_attr("Player_1", "role")
    assert player_role == "nora", "Player_1 应该被分配 nora 角色"

    print("✓ 测试通过：roles 系统端到端工作正常")


@pytest.mark.asyncio
async def test_roles_manual_assignment():
    """测试手动角色分配。"""
    from drama_engine.core.game_instance.registry import GameInstanceRegistry

    registry = GameInstanceRegistry()
    instance = registry.create(
        session_id="test_roles_manual",
        script_path="drama_engine/tests/fixtures/test_roles_system.yaml",
    )

    # 手动分配角色（覆盖推荐）
    await instance.assign(role_assignments={"Player_1": "marco"})

    # 验证：Player_1 被分配了 marco 而不是推荐的 nora
    runner = instance.runtime.runner
    state = runner.game_state
    player_role = state.get_attr("Player_1", "role")

    assert player_role == "marco", "手动分配应该覆盖推荐"

    print("✓ 测试通过：手动角色分配正常工作")


@pytest.mark.asyncio
async def test_actor_profile_injection():
    """测试 Actor 是否正确获取 role 人设。

    注意：这个测试需要实际运行 actor.act()，
    但由于环境依赖问题（html2text），暂时跳过。
    """
    pytest.skip("需要完整的 ccserver 环境，当前环境缺少依赖")


@pytest.mark.asyncio
async def test_view_snapshot_contains_roles():
    """测试 ViewSnapshot 是否包含 roles 信息。"""
    from drama_engine.core.game_instance.registry import GameInstanceRegistry

    registry = GameInstanceRegistry()
    instance = registry.create(
        session_id="test_roles_view",
        script_path="drama_engine/tests/fixtures/test_roles_system.yaml",
    )

    await instance.assign()

    # 获取 host snapshot
    snapshot = instance.project_context("host", "view")

    # 验证：snapshot 包含 roles
    assert "roles" in snapshot, "ViewSnapshot 应该包含 roles 字段"
    roles = snapshot["roles"]

    assert "nora" in roles, "roles 应该包含 nora"
    assert "marco" in roles, "roles 应该包含 marco"

    # 验证：roles 格式正确
    nora = roles["nora"]
    assert nora["name"] == "Nora Hampton"
    assert nora["emoji"] == "⚖️"
    assert nora["voice_id"] == "en-US-JennyNeural"

    print("✓ 测试通过：ViewSnapshot 正确包含 roles 信息")

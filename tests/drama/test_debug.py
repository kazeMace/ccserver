"""
tests/drama/test_debug.py — debug 模块的测试套件

Test suite for the drama_engine.core.diagnostics.debug module.

测试覆盖 / Test coverage:
  - MockActor.act (自由文本 / free text)
  - MockActor.act (结构化投票 / structured vote)
  - MockActor.act (布尔字段 / bool field)
  - MockActor.perceive (不应抛出 / should not raise)
  - DryRunConfig 默认值
  - StateInspector 差异计算
  - StateInspector 无变化时返回空 dict
  - SnapshotManager 保存与加载
"""

import sys
import os

# 把项目根目录加入 sys.path，保证从任意位置都能 import drama_engine
# Add project root to sys.path so drama_engine can be imported from anywhere
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from drama_engine.core.engine import Vocabulary, State
from drama_engine.core.diagnostics.debug import MockActor, DryRunConfig, StateInspector, SnapshotManager

# 用于测试的最小词汇表（所有集合为空，State 初始化即可）
# Minimal vocabulary for testing (all sets empty, State can be initialized)
_VOCAB = Vocabulary(roles=frozenset(), factions=frozenset(), scopes=frozenset(), abilities=frozenset())

import pytest


# =============================================================================
# MockActor 测试
# =============================================================================


@pytest.mark.asyncio
async def test_mock_actor_freespeak_returns_text():
    """
    自由文本模式（collect=None）下，act 应返回包含 text 和 actor 的 dict，data 为 None。

    In free text mode (collect=None), act should return dict with text and actor, data=None.
    """
    actor = MockActor(name="P1")
    result = await actor.act(cue="请发言", collect=None)
    assert result["actor"] == "P1"
    assert isinstance(result["text"], str)
    assert result["data"] is None


@pytest.mark.asyncio
async def test_mock_actor_vote_returns_valid_data():
    """
    投票模型下，act 应返回 data 字典，vote 字段来自 candidates 列表。

    In vote model mode, act should return data dict, vote field from candidates list.
    """
    from pydantic import BaseModel, Field
    class VoteModel(BaseModel):
        vote: str = Field(description="投票目标")
        reason: str = Field(description="理由")

    actor = MockActor(name="P1", candidates=["Player_2", "Player_3"])
    result = await actor.act(cue="请投票", collect=VoteModel)
    assert result["data"] is not None
    assert "vote" in result["data"]
    assert result["data"]["vote"] in ["Player_2", "Player_3"]


@pytest.mark.asyncio
async def test_mock_actor_set_candidates_updates_vote_targets():
    from pydantic import BaseModel

    class VoteModel(BaseModel):
        vote: str
        reason: str

    actor = MockActor(name="P1")
    actor.set_candidates(["Player_4"])
    result = await actor.act(cue="请投票", collect=VoteModel)
    assert result["data"]["vote"] == "Player_4"


@pytest.mark.asyncio
async def test_mock_actor_yesno_returns_bool():
    """
    含 action 字段的模型下，action 应为 bool 类型。

    When model has an 'action' field, it should be of bool type.
    """
    from pydantic import BaseModel, Field
    class YesNoModel(BaseModel):
        action: bool = Field(description="是否执行")
        reason: str

    actor = MockActor(name="P1")
    result = await actor.act(cue="是否救人？", collect=YesNoModel)
    assert isinstance(result["data"]["action"], bool)


@pytest.mark.asyncio
async def test_mock_actor_perceive_does_not_raise():
    """
    perceive 应该静默忽略事件，不抛出任何异常。

    perceive should silently ignore events without raising any exception.
    """
    actor = MockActor(name="P1")
    await actor.perceive({"role": "user", "content": [{"type": "text", "text": "test"}]})


# =============================================================================
# DryRunConfig 测试
# =============================================================================


def test_dry_run_config_defaults():
    """
    DryRunConfig 的默认值应为 auto_advance=True, max_rounds=3。

    DryRunConfig defaults should be auto_advance=True, max_rounds=3.
    """
    cfg = DryRunConfig()
    assert cfg.auto_advance is True
    assert cfg.max_rounds == 3


# =============================================================================
# StateInspector 测试
# =============================================================================


def test_inspector_records_diff():
    """
    StateInspector 应该正确记录场景前后的属性变化。

    StateInspector should correctly record attribute changes before/after a scene.
    """
    state = State(_VOCAB)
    state.register_entity("GAME", {"round": 1, "saved": False})
    inspector = StateInspector(enabled=True)
    inspector.snapshot_before(state, scene_name="test-scene")

    from drama_engine.core.engine import StateWriter, SetAttr
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "round", 2))
    writer.apply(SetAttr("GAME", "saved", True))

    diff = inspector.compute_diff(state)
    assert "GAME.round" in diff
    assert diff["GAME.round"] == (1, 2)
    assert "GAME.saved" in diff
    assert diff["GAME.saved"] == (False, True)


def test_inspector_no_diff_when_unchanged():
    """
    状态未变化时，compute_diff 应该返回空 dict。

    When state is unchanged, compute_diff should return an empty dict.
    """
    state = State(_VOCAB)
    state.register_entity("GAME", {"round": 1})
    inspector = StateInspector(enabled=True)
    inspector.snapshot_before(state, scene_name="test-scene")
    diff = inspector.compute_diff(state)
    assert diff == {}


# =============================================================================
# SnapshotManager 测试
# =============================================================================


def test_snapshot_manager_save_and_load(tmp_path):
    """
    SnapshotManager 应能正确保存状态到 JSON 文件，并从文件加载还原。

    SnapshotManager should correctly save state to JSON file and load it back.
    """
    state = State(_VOCAB)
    state.register_entity("GAME", {"round": 2, "wolf_target": "Player_1"})
    state.register_entity("Player_1", {"alive": False, "role": "villager"})

    manager = SnapshotManager(snapshot_dir=str(tmp_path))
    path = manager.save(state, scene_name="dawn-resolve", round_num=2)

    assert os.path.exists(path)

    loaded = manager.load(path)
    assert loaded["round"] == 2
    assert loaded["scene"] == "dawn-resolve"
    assert loaded["state"]["GAME"]["round"] == 2
    assert loaded["state"]["Player_1"]["alive"] is False

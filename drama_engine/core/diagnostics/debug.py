"""
drama_engine/debug.py — 调试工具模块

Debug tools module for drama_engine.

包含四个主要组件：
Contains four main components:
  1. MockActor       — 干跑模式下的假演员，不调用 LLM
                       Fake actor for dry-run mode, no LLM calls
  2. DryRunConfig    — 干跑模式的配置参数
                       Configuration for dry-run mode
  3. StateInspector  — 场景前后的状态差异检查器
                       State diff inspector before/after a scene
  4. SnapshotManager — 状态快照的保存与加载
                       Save and load state snapshots

使用示例 / Usage example:
  actor = MockActor(name="Player_1", candidates=["Player_2", "Player_3"])
  result = await actor.act(cue="请投票", collect=VoteModel)
"""

import os
import json
import random
import copy
import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any

# 从引擎导入 State 类型（仅用于类型注解和读取 _attrs）
# Import State type from engine for type hints and _attrs access
from drama_engine.core.engine import State


# =============================================================================
# 模块级工具函数
# Module-level utility functions
# =============================================================================


def _state_to_dict(state: State) -> dict:
    """
    把 State 的内部属性字典序列化成普通 Python dict。

    Serialize State._attrs into a plain Python dict.

    参数 / Parameters:
      state — drama_engine.engine.State 实例

    返回 / Returns:
      {entity_name: {attr_key: value}, ...} 格式的嵌套 dict
      Nested dict in format {entity_name: {attr_key: value}, ...}

    注意 / Note:
      使用 copy.deepcopy 避免序列化结果与 state 内部共享引用。
      Uses copy.deepcopy to avoid shared references between result and state internals.
    """
    # state._attrs 的结构是 {entity_name: {attr_key: value}}
    # state._attrs structure is {entity_name: {attr_key: value}}
    result = {}
    for entity_name, attrs in state._attrs.items():
        result[entity_name] = copy.deepcopy(attrs)
    return result


# =============================================================================
# 1. MockActor — 干跑模式假演员
# MockActor — fake actor for dry-run mode
# =============================================================================


class MockActor:
    """
    干跑模式下的假演员，不调用 LLM。

    Fake actor for dry-run mode. Does not call LLM at all.

    用途：
      - 用于 --dry-run 参数，快速验证游戏流程不卡死
      - 根据字段名自动推断 mock 返回值

    Purpose:
      - Used for --dry-run flag to quickly validate game flow without LLM
      - Automatically infers mock return values based on field names

    属性 / Attributes:
      name        — 演员名字，如 "Player_1"
      _candidates — 投票/选择类字段的候选目标列表
    """

    def __init__(self, name: str, candidates: list = None):
        """
        初始化 MockActor。

        Initialize MockActor.

        参数 / Parameters:
          name       — 演员名字，如 "Player_1"
          candidates — 投票/选择时的候选目标列表，如 ["Player_2", "Player_3"]
                       为 None 时使用空列表
        """
        assert isinstance(name, str) and name, "name 必须是非空字符串 / name must be a non-empty string"

        self.name = name
        self.actor_id = str(uuid.uuid4())
        self.player_id = name
        self.display_name = name
        self.nickname = ""
        self.controller_type = "mock"
        self.is_human = False
        self.role_name = None
        self.role_display_name = None
        self.actor_profile = None
        # 候选目标列表，用于 vote/target/choose 类字段的随机选择
        # Candidate list for random selection in vote/target/choose fields
        self._candidates = candidates if candidates is not None else []
        self._candidate_constraints = {}

    def set_player_profile(
        self,
        player_id: str,
        display_name: str = "",
        nickname: str = "",
    ) -> None:
        """设置运行时展示资料，方便 dry-run/debug 查看。"""
        self.player_id = player_id
        self.display_name = display_name or player_id
        self.nickname = nickname or ""

    def set_role_snapshot(self, role: Any) -> None:
        """保存本局角色快照，方便 dry-run/debug 查看。"""
        self.role_name = role.name
        self.role_display_name = getattr(role, "display_name", "") or role.name

    def set_actor_profile(self, profile: Any) -> None:
        """保存稳定身份档案，方便 dry-run/debug 查看。"""
        self.actor_profile = profile
        self.role_name = profile.role_name
        self.role_display_name = profile.role_display_name or profile.role_name

    def set_candidates(self, candidates: list) -> None:
        """设置本幕候选目标，避免 dry-run 产出未注册的 mock_target。

        候选项可能是字符串（如玩家名）或 {id, text} 结构（如 choose 静态候选）。
        这里统一归一化为可提交的标识（优先取 id），保证 dry-run 选出的值是合法候选。
        """
        normalized: list = []
        for item in candidates or []:
            if isinstance(item, dict):
                normalized.append(item.get("id") if item.get("id") is not None else item.get("text"))
            else:
                normalized.append(item)
        self._candidates = [item for item in normalized if item is not None]

    def set_candidate_constraints(self, constraints: dict) -> None:
        """设置本幕候选数量约束，供 ChooseMany dry-run 使用。"""
        self._candidate_constraints = dict(constraints or {})

    async def perceive(self, event: dict) -> None:
        """
        接收并静默忽略场上事件。

        Receive a scene event and silently ignore it.

        参数 / Parameters:
          event — 事件字典，如 {"role": "user", "content": [...]}

        注意 / Note:
          MockActor 不需要积累观察缓冲，直接丢弃即可。
          MockActor has no observation buffer, just discard the event.
        """
        # 静默忽略，不做任何处理
        # Silently discard, do nothing
        pass

    async def act(self, cue: str, collect=None) -> dict:
        """
        生成 mock 发言或结构化动作，不调用 LLM。

        Generate a mock response without calling LLM.

        参数 / Parameters:
          cue     — 旁白提示词
          collect — Pydantic Model class 或 None

        返回 / Returns:
          Response 字典：{"actor": str, "text": str, "data": dict 或 None}
          Response dict: {"actor": str, "text": str, "data": dict or None}
        """
        # 自由文本模式：直接返回占位文本
        # Free text mode: return placeholder text
        if collect is None:
            return {
                "actor": self.name,
                "text": f"(dry-run {self.name})",
                "data": None,
            }

        # 结构化模式：根据字段推断 mock 值
        # Structured mode: infer mock values from field names
        mock_data = self._mock_collect(collect)
        return {
            "actor": self.name,
            "text": f"(dry-run {self.name})",
            "data": mock_data,
        }

    def _mock_collect(self, collect) -> dict:
        """
        根据 Pydantic Model 的字段名和类型注解推断 mock 值。

        Infer mock values from Pydantic Model field names and type annotations.

        推断规则（按优先级）/ Inference rules (by priority):
          1. 字段名含 vote/target/choose → 随机选 _candidates 之一（无候选则用 "mock_target"）
          2. 字段名含 action             → True (bool)
          3. 字段名含 reason             → "(dry-run mock)"
          4. annotation 含 "bool"        → True
          5. annotation 含 "str"         → "(mock)"
          6. 其他                        → None

        参数 / Parameters:
          collect — Pydantic Model class（非实例）

        返回 / Returns:
          字段名 -> mock 值 的 dict
          Dict of field_name -> mock_value
        """
        result = {}

        # 从 Pydantic model_fields 读取字段定义
        # Read field definitions from Pydantic model_fields
        fields_info = collect.model_fields

        for field_name, field_info in fields_info.items():
            mock_value = self._infer_mock_value(field_name, field_info)
            result[field_name] = mock_value

        return result

    def _infer_mock_value(self, field_name: str, field_info) -> Any:
        """
        对单个字段推断 mock 值。

        Infer mock value for a single field.

        参数 / Parameters:
          field_name — 字段名，如 "vote", "reason"
          field_info — Pydantic FieldInfo 对象

        返回 / Returns:
          推断出的 mock 值
          Inferred mock value
        """
        name_lower = field_name.lower()

        annotation_str = self._get_annotation_str(field_info)

        # 规则 1: ChooseMany.targets 这类 list 字段 → 返回候选列表子集。
        # Rule 1: list fields such as ChooseMany.targets → return candidate subset.
        if "list" in annotation_str and ("target" in name_lower or "targets" in name_lower):
            return self._pick_candidate_list()

        # 规则 2: 字段名含 vote/target/choose → 随机选候选之一
        # Rule 2: field name contains vote/target/choose → pick random candidate
        if "vote" in name_lower or "target" in name_lower or "choose" in name_lower:
            return self._pick_random_candidate()

        # 规则 3: 字段名含 action → True
        # Rule 3: field name contains action → True
        if "action" in name_lower:
            return True

        # 规则 4: 字段名含 reason → "(dry-run mock)"
        # Rule 4: field name contains reason → "(dry-run mock)"
        if "reason" in name_lower:
            return "(dry-run mock)"

        # 规则 5 & 6: 从类型注解推断
        # Rule 5 & 6: infer from type annotation
        if "bool" in annotation_str:
            return True

        if "str" in annotation_str:
            return "(mock)"

        # 规则 7: 兜底返回 None
        # Rule 7: fallback to None
        return None

    def _pick_random_candidate(self) -> str:
        """
        从候选列表中随机选一个目标。没有候选时返回默认值。

        Randomly pick one target from the candidate list.
        Returns default value when no candidates available.

        返回 / Returns:
          候选列表中的一个字符串，或 "mock_target"
          A string from the candidate list, or "mock_target"
        """
        if not self._candidates:
            return "mock_target"
        return random.choice(self._candidates)

    def _pick_candidate_list(self) -> list[str]:
        """返回多选字段的 mock 候选列表。

        当前 MockActor 不读取 scene 的动态数量约束，因此返回所有候选。
        候选校验会在数量不匹配时提示重试；多数脚本的 dry-run 多选应优先
        使用运行器注入的候选集，使返回值至少是 list 且元素合法。
        """
        if not self._candidates:
            return []
        expected_count = self._candidate_constraints.get("count")
        if isinstance(expected_count, int) and expected_count > 0:
            return list(self._candidates[:expected_count])
        return list(self._candidates)

    def _get_annotation_str(self, field_info) -> str:
        """
        从 Pydantic FieldInfo 获取类型注解的字符串表示，用于字符串匹配。

        Get the string representation of type annotation from Pydantic FieldInfo,
        used for string matching in rule 5/6.

        参数 / Parameters:
          field_info — Pydantic FieldInfo 对象

        返回 / Returns:
          类型注解的字符串，如 "str"、"bool"、"int" 等
          String representation of the type annotation
        """
        annotation = field_info.annotation
        if annotation is None:
            return ""
        return str(annotation).lower()


# =============================================================================
# 2. DryRunConfig — 干跑模式配置
# DryRunConfig — dry-run configuration
# =============================================================================


@dataclass
class DryRunConfig:
    """
    干跑模式的配置参数。

    Configuration parameters for dry-run mode.

    属性 / Attributes:
      auto_advance — 是否自动推进（不等待用户确认）
                     Whether to auto-advance (no user confirmation required)
      max_rounds   — 最多跑几轮后停止
                     Max rounds to run before stopping
    """
    auto_advance: bool = True     # 自动推进，不暂停等待 / Auto-advance without pausing
    max_rounds: int = 3           # 最多运行轮数 / Maximum rounds to run


# =============================================================================
# 3. StateInspector — 场景前后状态对比
# StateInspector — state diff before/after a scene
# =============================================================================


class StateInspector:
    """
    场景前后的状态差异检查器。

    Inspector that compares state before and after a scene.

    用途：
      - 打印场景开始时的分隔线和状态
      - 场景结束后对比状态，输出变化项

    Purpose:
      - Print scene separator and state at the start of a scene
      - Compare state after scene ends, output changed items

    属性 / Attributes:
      _enabled — 是否启用检查，False 时所有方法静默跳过
      _before  — 场景前的状态快照 {entity: {attr: value}}
    """

    def __init__(self, enabled: bool = True):
        """
        初始化检查器。

        Initialize the inspector.

        参数 / Parameters:
          enabled — 是否启用。False 时所有方法静默跳过。
                    Whether enabled. All methods silently skip when False.
        """
        self._enabled = enabled
        # 保存场景开始前的状态快照
        # Stores state snapshot before the scene starts
        self._before: dict = {}

    def snapshot_before(self, state: State, scene_name: str) -> None:
        """
        在场景开始前记录状态快照，并打印场景分隔线。

        Record a state snapshot before the scene starts, and print a separator.

        参数 / Parameters:
          state      — 当前 State 对象
          scene_name — 场景名，用于打印分隔线
        """
        if not self._enabled:
            return

        # 打印场景分隔线，便于在日志里定位每个场景
        # Print scene separator to help locate each scene in logs
        print(f"[INSPECT] ──── 场景: {scene_name} ────")

        # 保存深拷贝，避免后续状态变化影响快照
        # Save deep copy to avoid later state changes affecting the snapshot
        self._before = _state_to_dict(state)

    def compute_diff(self, state: State) -> dict:
        """
        计算场景结束后状态相对于快照的变化。

        Compute the diff between the current state and the before-snapshot.

        参数 / Parameters:
          state — 场景结束后的 State 对象

        返回 / Returns:
          变化字典 {"entity.attr": (before_val, after_val), ...}
          只包含有变化的条目。
          Change dict {"entity.attr": (before_val, after_val), ...}
          Only contains items that changed.
        """
        if not self._enabled:
            return {}

        # 防护：如果未调用 snapshot_before，diff 结果可能不准确
        # Guard: if snapshot_before was not called, diff results may be inaccurate
        if not self._before:
            print("[INSPECT] 警告：未调用 snapshot_before，diff 结果可能不准确")

        after_dict = _state_to_dict(state)
        diff = {}

        # 遍历当前状态的所有实体和属性
        # Iterate all entities and attributes in the current state
        for entity_name, attrs in after_dict.items():
            before_entity = self._before.get(entity_name, {})
            for attr_key, after_val in attrs.items():
                before_val = before_entity.get(attr_key)
                # 只记录有变化的属性
                # Only record attributes that changed
                if before_val != after_val:
                    key = f"{entity_name}.{attr_key}"
                    diff[key] = (before_val, after_val)

        return diff

    def print_diff(self, diff: dict, scene_name: str = "") -> None:
        """
        打印状态变化。

        Print state changes.

        参数 / Parameters:
          diff       — compute_diff() 的返回值
          scene_name — 场景名，仅用于打印标题
        """
        if not self._enabled:
            return

        if not diff:
            print(f"[INSPECT] 场景 {scene_name!r} 无状态变化")
            return

        print(f"[INSPECT] 场景 {scene_name!r} 状态变化：")
        for key, (before_val, after_val) in diff.items():
            print(f"[INSPECT]   {key}: {before_val!r} → {after_val!r}")

    def print_performers(self, scene_name: str, performers: set) -> None:
        """
        打印本场景的上场者名单。

        Print the list of performers for this scene.

        参数 / Parameters:
          scene_name — 场景名
          performers — 上场者名字的集合，如 {"Player_1", "Player_2"}
        """
        if not self._enabled:
            return

        print(f"[INSPECT] 场景 {scene_name!r} 上场者：{sorted(performers)}")


# =============================================================================
# 4. SnapshotManager — 状态快照保存与加载
# SnapshotManager — save and load state snapshots
# =============================================================================


class SnapshotManager:
    """
    状态快照的保存与加载管理器。

    Manager for saving and loading state snapshots.

    用途：
      - 保存某一轮的状态到 JSON 文件，方便事后分析
      - 加载快照文件
      - 从快照重建 State 对象

    Purpose:
      - Save the state at a specific round to a JSON file for later analysis
      - Load a snapshot file
      - Reconstruct a State object from a snapshot

    属性 / Attributes:
      _snapshot_dir — 快照文件的存储目录
    """

    def __init__(self, snapshot_dir: str = "drama_engine/snapshots"):
        """
        初始化快照管理器，自动创建目录。

        Initialize snapshot manager, auto-create directory if not exists.

        参数 / Parameters:
          snapshot_dir — 快照文件存储目录路径
        """
        assert isinstance(snapshot_dir, str) and snapshot_dir, (
            "snapshot_dir 必须是非空字符串 / snapshot_dir must be non-empty string"
        )

        self._snapshot_dir = snapshot_dir

        # 自动创建目录，如果不存在则创建（包含父目录）
        # Auto-create directory including parent directories
        os.makedirs(snapshot_dir, exist_ok=True)

    def save(self, state: State, scene_name: str, round_num: int, script_path: str = "") -> str:
        """
        把当前状态序列化保存为 JSON 快照文件。

        Serialize and save the current state to a JSON snapshot file.

        参数 / Parameters:
          state       — 当前 State 对象
          scene_name  — 当前场景名，用于文件名
          round_num   — 当前轮次，用于文件名
          script_path — 剧本文件路径（可选，写入快照元数据）

        返回 / Returns:
          保存的快照文件的绝对路径
          Absolute path of the saved snapshot file
        """
        assert isinstance(round_num, int) and round_num >= 0, (
            "round_num 必须是非负整数 / round_num must be a non-negative integer"
        )
        assert isinstance(scene_name, str), (
            "scene_name 必须是字符串 / scene_name must be a string"
        )

        # 生成时间戳，格式：20240101_120000
        # Generate timestamp, format: 20240101_120000
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # 过滤 scene_name 中的非法文件名字符（如 "/" 和空格），防止路径穿越或文件名错误
        # Sanitize scene_name to remove illegal filename characters (e.g. "/" and spaces)
        safe_scene_name = scene_name.replace("/", "_").replace(" ", "_")

        # 文件名格式：snapshot_round{N}_{scene_name}_{timestamp}.json
        # Filename format: snapshot_round{N}_{scene_name}_{timestamp}.json
        filename = f"snapshot_round{round_num}_{safe_scene_name}_{timestamp}.json"
        filepath = os.path.join(self._snapshot_dir, filename)

        # 构建快照数据
        # Build snapshot data
        snapshot_data = {
            "round": round_num,
            "scene": scene_name,
            "script_path": script_path,
            "timestamp": timestamp,
            "state": _state_to_dict(state),
        }

        # 写入 JSON 文件，ensure_ascii=False 支持中文
        # Write to JSON file, ensure_ascii=False supports Chinese characters
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snapshot_data, f, ensure_ascii=False, indent=2)

        print(f"[SnapshotManager] 已保存快照：{filepath}")
        return filepath

    def load(self, snapshot_path: str) -> dict:
        """
        从文件加载快照数据。

        Load snapshot data from a file.

        参数 / Parameters:
          snapshot_path — 快照文件路径（绝对或相对路径）

        返回 / Returns:
          快照 dict，包含 round/scene/state 等字段
          Snapshot dict containing round/scene/state etc. fields
        """
        assert os.path.exists(snapshot_path), (
            f"快照文件不存在：{snapshot_path} / Snapshot file not found: {snapshot_path}"
        )

        with open(snapshot_path, "r", encoding="utf-8") as f:
            snapshot_data = json.load(f)

        print(f"[SnapshotManager] 已加载快照：{snapshot_path}")
        return snapshot_data

    def restore_state(self, snapshot: dict, vocab) -> State:
        """
        从快照数据重建 State 对象。

        Reconstruct a State object from snapshot data.

        参数 / Parameters:
          snapshot — load() 返回的快照 dict
          vocab    — Vocabulary 对象，用于初始化新的 State

        返回 / Returns:
          重建的 State 对象
          Reconstructed State object
        """
        assert "state" in snapshot, (
            "快照格式错误，缺少 'state' 字段 / Invalid snapshot format, missing 'state' field"
        )

        new_state = State(vocab)

        # 逐一注册实体并恢复属性
        # Register entities one by one and restore attributes
        state_data = snapshot["state"]
        for entity_name, attrs in state_data.items():
            try:
                new_state.register_entity(entity_name, attrs)
            except Exception as e:
                # 打印具体错误信息，包含实体名，方便定位问题
                # Print detailed error with entity name for easier debugging
                print(f"[SnapshotManager] 恢复实体失败 entity={entity_name!r}: {e}")

        print(f"[SnapshotManager] 已从快照还原 State，实体数={len(state_data)}")
        return new_state

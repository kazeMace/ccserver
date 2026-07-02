"""Casting and flow strategies used by Script.flow and Script.casting."""

import random
from typing import Any

from .state import SetAttr


class ShuffleDeal:
    """
    随机发牌 — 把角色随机分配给演员。

    参数：
      role_counts — dict，如 {"werewolf": 3, "villager": 3, "seer": 1}
                    表示这出戏需要 3 个狼人、3 个村民、1 个预言家
    """

    def __init__(self, role_counts: dict):
        """
        初始化随机发牌策略。

        参数：
          role_counts — 每种角色需要几个，如 {"werewolf": 3, "villager": 3}
        """
        # 保存角色数量配置
        self.role_counts = role_counts

    def deal(self, actor_names: list, roles: list) -> list:
        """
        把角色随机分配给演员。

        参数：
          actor_names — 演员名字列表，如 ["Player1", "Player2", ...]
          roles       — Role 对象列表（Script.roles）

        返回：
          list of (actor_name, Role) 元组，如 [("Player1", wolf_role), ...]
        """
        # 根据 role_counts 展开角色列表
        # 例如 {"werewolf": 2} 展开成 [wolf_role, wolf_role]
        role_map = {r.name: r for r in roles}
        expanded_roles = []
        for role_name, count in self.role_counts.items():
            assert role_name in role_map, f"角色 '{role_name}' 未在 Script.roles 中定义"
            for _ in range(count):
                expanded_roles.append(role_map[role_name])

        # 检查角色数量和演员数量是否匹配
        assert len(expanded_roles) == len(actor_names), (
            f"角色总数 {len(expanded_roles)} 与演员数 {len(actor_names)} 不符"
        )

        # 打乱角色顺序，然后一一配对
        shuffled_roles = expanded_roles[:]
        random.shuffle(shuffled_roles)

        assignment = list(zip(actor_names, shuffled_roles))
        print(f"[Cast] 选角完成：{[(name, r.name) for name, r in assignment]}")
        return assignment


class FixedDeal:
    """
    固定发牌 — 按指定的分配关系把角色分配给演员。
    用于测试或需要固定角色的场景。

    参数：
      assignment — dict，如 {"Player1": "werewolf", "Player2": "seer"}
    """

    def __init__(self, assignment: dict):
        """
        初始化固定发牌策略。

        参数：
          assignment — 演员名 -> 角色名 的映射
        """
        self.assignment = assignment

    def deal(self, actor_names: list, roles: list) -> list:
        """
        按预设分配关系分配角色。

        参数：
          actor_names — 演员名字列表
          roles       — Role 对象列表

        返回：
          list of (actor_name, Role) 元组
        """
        role_map = {r.name: r for r in roles}
        result = []
        for name in actor_names:
            role_name = self.assignment.get(name)
            assert role_name is not None, f"演员 '{name}' 没有在 FixedDeal 中分配角色"
            assert role_name in role_map, f"角色 '{role_name}' 未在 Script.roles 中定义"
            result.append((name, role_map[role_name]))
        print(f"[Cast] 固定选角：{[(name, r.name) for name, r in result]}")
        return result


class Sequence:
    """
    线性流程 — 幕场按列表顺序循环推进。

    v0.1 的流程实现，够用于狼人杀等大部分游戏。
    Flow 接口设计成可替换（未来可扩展为状态机或动态派生）。

    参数：
      scenes — Scene 列表，按顺序执行
      loop   — 是否循环（True 表示跑完所有幕后从头再来，直到 referee 判出胜负）
    """

    def __init__(self, scenes: list, loop: bool = True):
        """
        初始化线性流程。

        参数：
          scenes — Scene 对象列表
          loop   — 是否循环，默认 True
        """
        self.scenes = scenes
        self.loop = loop

    def next_scenes(self, state: Any) -> list:
        """
        返回下一批要执行的幕列表。

        参数：
          state — 当前世界状态（State 对象）

        返回：
          Scene 列表（本实现直接返回全部 scenes，Director 会循环调用）
        """
        # 线性实现：直接返回全部幕，Director 负责循环
        return self.scenes

    def on_batch_start(self, state: Any, writer: Any) -> None:
        """批次开始钩子。线性流程无阶段进入动作。"""
        return None

    def after_scene(self, scene: Any, state: Any, writer: Any) -> bool:
        """单幕结束钩子。返回 True 表示继续执行本批次剩余 scenes。"""
        return True

    def after_batch(self, state: Any, writer: Any) -> None:
        """批次结束钩子。线性流程无阶段转移动作。"""
        return None


class StateMachineFlow:
    """
    状态机流程 — 按“阶段节点”推进幕场。

    Sequence 适合 night -> day -> night 这类固定循环；StateMachineFlow 适合
    警长竞选、PK、首夜特殊阶段等需要根据状态分支的游戏流程。

    参数：
      initial — 初始状态节点名
      states  — dict，形如：
                {
                    "night": {
                        "scenes": [Scene, ...],
                        "entry": callable | None,
                        "exit": callable | None,
                        "transitions": [{"to": "day", "when": callable | None}],
                        "terminal": False,
                    }
                }
    """

    def __init__(self, initial: str, states: dict):
        """初始化状态机流程。"""
        assert initial, "StateMachineFlow 需要 initial 状态"
        assert isinstance(states, dict) and states, "StateMachineFlow 需要 states"
        assert initial in states, f"initial 状态 '{initial}' 不在 states 中"
        self.initial = initial
        self.states = states
        self.current = None
        self.loop = True
        self._entered_current = False
        self._interrupted = False
        self.scenes = [
            scene
            for node in states.values()
            for scene in node.get("scenes", [])
        ]

    def next_scenes(self, state: Any) -> list:
        """
        返回当前状态节点的幕列表。

        第一次调用进入 initial；之后由 after_batch 或 after_scene 推进状态。
        Director 不需要知道流程是线性还是状态机。
        """
        if self.current is None:
            self.current = self.initial

        node = self.states[self.current]
        if node.get("terminal"):
            self.loop = False
            return []

        return list(node.get("scenes", []))

    def on_batch_start(self, state: Any, writer: Any) -> None:
        """
        执行当前状态节点的 entry 动作。

        entry 只在进入该状态后的第一个批次开始时执行一次。
        """
        if self.current is None:
            self.current = self.initial
        if self.loop is False:
            return
        if self._entered_current:
            return
        node = self.states[self.current]
        entry = node.get("entry")
        if entry is not None:
            entry(state, writer)
        self._entered_current = True

    def after_scene(self, scene: Any, state: Any, writer: Any) -> bool:
        """
        单幕结束后检查是否请求强制转移。

        effects 可通过 `flow_set_next` 写入 `GAME.__flow_next_state`。一旦存在，
        状态机执行当前状态 exit 动作，切换到目标状态，并要求 Director 跳过
        当前批次剩余 scenes。
        """
        next_state = state.get_attr("GAME", "__flow_next_state")
        if not next_state:
            return True
        self._consume_forced_transition(next_state, state, writer)
        writer.apply(SetAttr("GAME", "__flow_next_state", None))
        self._interrupted = True
        return False

    def after_batch(self, state: Any, writer: Any) -> None:
        """
        当前状态节点的 scenes 执行完后，按 transitions 推进到下一状态。
        """
        if self.loop is False:
            return
        if self._interrupted:
            self._interrupted = False
            return
        next_state = self._next_state_name(state)
        if next_state != self.current:
            self._leave_current(state, writer)
            self.current = next_state
            self._entered_current = False
            if self.states[self.current].get("terminal"):
                self.loop = False

    def _next_state_name(self, state: Any) -> str:
        """
        根据当前节点 transitions 选择下一状态。

        transitions 按顺序匹配；无 when 的 transition 是 fallback。
        如果没有 transition，默认停留在当前节点，形成自循环。
        """
        node = self.states[self.current]
        transitions = node.get("transitions", [])
        for transition in transitions:
            condition = transition.get("when")
            if condition is None or condition(state):
                target = transition.get("to")
                assert target in self.states, f"transition.to '{target}' 不在 states 中"
                return target
        return self.current

    def _leave_current(self, state: Any, writer: Any) -> None:
        """执行当前状态节点的 exit 动作。"""
        node = self.states[self.current]
        exit_action = node.get("exit")
        if exit_action is not None:
            exit_action(state, writer)

    def _consume_forced_transition(self, next_state: str, state: Any, writer: Any) -> None:
        """执行 flow_set_next 请求的强制状态切换。"""
        assert next_state in self.states, f"flow_set_next 目标状态 '{next_state}' 不在 states 中"
        self._leave_current(state, writer)
        self.current = next_state
        self._entered_current = False
        if self.states[self.current].get("terminal"):
            self.loop = False

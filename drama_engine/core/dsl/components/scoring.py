# drama_engine/components/scoring.py
"""
计分系统（ScoreTracker）。

负责管理游戏内各团队/玩家的分数。
支持初始化团队、增加分数、查询分数等操作。

使用约定：
  - 每个团队作为一个 entity 注册到 state 中
  - 团队的 score 属性存储当前分数
  - 获取不存在的团队分数时返回 0（默认值）
"""

from __future__ import annotations
from drama_engine.core.engine import State, StateWriter, SetAttr


class ScoreTracker:
    """
    计分跟踪器 — 管理游戏内各团队/玩家的分数。

    支持的操作：
      - init(state, writer)     — 初始化所有团队的初始分数为 0
      - add_score(entity, value, state, writer) — 给指定实体增加分数
      - get_scores(state)       — 获取所有团队当前的分数字典
    """

    def __init__(self, scoring_spec: dict):
        """
        初始化计分跟踪器。

        参数：
          scoring_spec — 计分规则字典，通常包含以下结构：
                        {
                            "teams": [
                                {"name": "wolf_team", "display_name": "狼人阵营", "members": {...}},
                                {"name": "good_team", "display_name": "好人阵营", "members": {...}},
                            ]
                        }
        """
        # 保存计分规则，供后续查询和初始化使用
        self._spec = scoring_spec

    def init(self, state: State, writer: StateWriter) -> None:
        """
        初始化所有团队的初始分数为 0。

        本方法会为 scoring_spec 中定义的每个团队创建一个 entity，
        并将其 score 属性设为 0。

        参数：
          state  — 当前游戏状态
          writer — 状态写入口（用于记录变更到日志）
        """
        # 遍历规则中定义的所有团队
        for team in self._spec.get("teams", []):
            team_name = team["name"]

            # 如果团队 entity 还没注册，先注册它
            if team_name not in state.all_entities():
                state.register_entity(team_name, {"score": 0})
            else:
                # 如果已存在，直接设置初始分数为 0
                # 使用 writer.apply 记录这个变更
                mutation = SetAttr(team_name, "score", 0)
                writer.apply(mutation)

    def add_score(self, entity: str, value: int, state: State, writer: StateWriter) -> None:
        """
        给指定实体增加分数。

        参数：
          entity — 实体名，如 "wolf_team"
          value  — 增加的分数（可以是负数表示扣分）
          state  — 当前游戏状态
          writer — 状态写入口
        """
        # 获取当前分数（如果没有 score 属性，默认为 0）
        current_score = state.get_attr(entity, "score") or 0

        # 计算新分数
        new_score = current_score + value

        # 通过 writer.apply 记录变更，确保进入日志系统
        mutation = SetAttr(entity, "score", new_score)
        writer.apply(mutation)

    def get_scores(self, state: State) -> dict:
        """
        获取所有团队当前的分数。

        返回一个字典，其中 key 是团队名，value 是当前分数。
        如果某个团队的 score 属性不存在，返回 0。

        参数：
          state — 当前游戏状态

        返回：
          dict — 团队名 -> 分数的映射，如 {"wolf_team": 20, "good_team": 15}
        """
        result = {}

        # 遍历规则中定义的所有团队
        for team in self._spec.get("teams", []):
            team_name = team["name"]

            # 从 state 中读取该团队的分数，不存在时返回 0
            score = state.get_attr(team_name, "score") or 0
            result[team_name] = score

        return result

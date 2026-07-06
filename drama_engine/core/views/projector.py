"""统一视图投影入口（架构文档 §17）。

ViewProjector 为 host / player / public / audience 提供统一视图数据源。它复用现有
view_projection 快照实现，把「对外视图」从 runner.summary() 收口到独立视图层，
使 GameInstance 只依赖 ViewProjector 抽象。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.session.view_projection import (
    build_host_snapshot,
    build_player_snapshot,
    build_public_snapshot,
)

logger = logging.getLogger(__name__)


class ViewProjector:
    """把 GameRuntime 投影为面向不同受众的视图快照。"""

    def __init__(self, runtime: Any) -> None:
        """绑定底层 GameRuntime。"""
        assert runtime is not None, "runtime 不能为空"
        self._runtime = runtime

    def host_view(self) -> dict[str, Any]:
        """主持人视图。"""
        return build_host_snapshot(self._runtime).to_dict()

    def public_view(self) -> dict[str, Any]:
        """公开观众视图。"""
        return build_public_snapshot(self._runtime).to_dict()

    def player_view(self, seat_id: str, user_id: str | None = None) -> dict[str, Any]:
        """指定 seat 的玩家视图。"""
        assert seat_id, "seat_id 不能为空"
        return build_player_snapshot(self._runtime, seat_id, user_id).to_dict()

    def audience_view(self) -> dict[str, Any]:
        """观众视图；当前等同 public_view。"""
        return self.public_view()


__all__ = ["ViewProjector"]

"""GameInstance 工厂与注册表（架构文档 §15 执行链路）。

GameInstanceFactory 把一个已创建的 GameRuntime 包装成 GameInstance。
GameInstanceRegistry 组合现有 SessionRegistry（负责创建/持久化/token/生命周期），
对外只暴露 GameInstance，让 service 层不再直接接触 GameRuntime。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.game_instance.instance import GameInstance
from drama_engine.core.session.registry import SessionRegistry

logger = logging.getLogger(__name__)


class GameInstanceFactory:
    """把 GameRuntime 包装为 GameInstance。"""

    @staticmethod
    def wrap(runtime: Any) -> GameInstance:
        """用一个 GameRuntime 构造 GameInstance。"""
        assert runtime is not None, "runtime 不能为空"
        return GameInstance(runtime)


class GameInstanceRegistry:
    """GameInstance 注册表。

    组合 SessionRegistry：创建/查询/生命周期仍由 SessionRegistry 完成（含持久化与
    token 服务），但对外返回 GameInstance。token_service 直接透传，供 service 层
    校验玩家 token。
    """

    def __init__(self, session_registry: SessionRegistry | None = None, **kwargs: Any) -> None:
        """绑定或新建底层 SessionRegistry。

        参数：
          session_registry — 已有的 SessionRegistry；为空时用 kwargs 新建一个。
          kwargs           — 透传给 SessionRegistry 构造（token_service/store/load_existing）。
        """
        self._registry = session_registry or SessionRegistry(**kwargs)

    @property
    def session_registry(self) -> SessionRegistry:
        """返回底层 SessionRegistry。"""
        return self._registry

    @property
    def token_service(self) -> Any:
        """返回玩家 token 服务。"""
        return self._registry.token_service

    async def create_instance(
        self,
        game_id: str,
        script_path: str,
        seat_ids: list[str],
        params: dict[str, Any] | None = None,
        human_seat_ids: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GameInstance:
        """创建一局游戏并返回 GameInstance。"""
        runtime = await self._registry.create_session(
            game_id=game_id,
            script_path=script_path,
            seat_ids=seat_ids,
            params=params,
            human_seat_ids=human_seat_ids,
            metadata=metadata,
        )
        return GameInstanceFactory.wrap(runtime)

    async def get_instance(self, session_id: str) -> GameInstance:
        """按 session_id 获取 GameInstance。"""
        runtime = await self._registry.get_session(session_id)
        return GameInstanceFactory.wrap(runtime)

    async def list_instances(self) -> list[dict[str, Any]]:
        """列出所有 session 摘要。"""
        return await self._registry.list_sessions()


__all__ = ["GameInstanceFactory", "GameInstanceRegistry"]

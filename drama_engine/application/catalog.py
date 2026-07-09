"""Game catalog for Drama Engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from drama_engine.application.script_library import SCRIPT_LIBRARY_ROOT, iter_builtin_script_paths
from drama_engine.core.script_loader import ScriptLoader, ScriptBundle


@dataclass(frozen=True, slots=True)
class GameDefinition:
    """可创建游戏定义。"""

    game_id: str
    script_path: str
    title: str
    roles: dict[str, dict[str, Any]] | None = None
    recommended_player_role: str | None = None


class GameCatalog:
    """游戏目录 — 使用 ScriptLoader 统一读取脚本元数据和角色信息。"""

    def __init__(self, scripts_root: str | Path | None = None) -> None:
        if scripts_root is None:
            scripts_root = SCRIPT_LIBRARY_ROOT
        self.scripts_root = Path(scripts_root)
        self._loader = ScriptLoader()

    async def list_games_async(self) -> list[GameDefinition]:
        """异步列出 scripts_root 下的游戏。"""
        result = []
        for path in iter_builtin_script_paths(self.scripts_root):
            bundle = await self._loader.load(path)
            meta = bundle.meta
            roles_dict = self._build_roles_dict(bundle.roles)
            game_id = path.name if path.is_dir() else path.stem
            result.append(GameDefinition(
                game_id=game_id,
                script_path=str(path),
                title=meta.display_name or meta.title or game_id.replace("_", " ").title(),
                roles=roles_dict,
                recommended_player_role=meta.recommended_player_role,
            ))
        return result

    def list_games(self) -> list[GameDefinition]:
        """同步列出游戏（兼容旧调用方）。"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 已在 async 上下文中，回退到同步实现避免 nested event loop
            return self._list_games_sync()
        return asyncio.run(self.list_games_async())

    def get_game(self, game_id: str) -> GameDefinition:
        """按 game_id 获取游戏定义。"""
        assert game_id, "game_id 不能为空"
        for game in self.list_games():
            if game.game_id == game_id:
                return game
        raise KeyError(f"游戏不存在: {game_id}")

    async def get_game_async(self, game_id: str) -> GameDefinition:
        """异步按 game_id 获取游戏定义。"""
        assert game_id, "game_id 不能为空"
        games = await self.list_games_async()
        for game in games:
            if game.game_id == game_id:
                return game
        raise KeyError(f"游戏不存在: {game_id}")

    def _list_games_sync(self) -> list[GameDefinition]:
        """同步 fallback（已在 event loop 中时使用）。"""
        import yaml
        result = []
        for path in iter_builtin_script_paths(self.scripts_root):
            if path.is_dir():
                game_id = path.name
                roles, recommended, title = self._extract_roles_from_package(path)
            else:
                game_id = path.stem
                roles, recommended = self._extract_roles_from_script(path)
                title = None
            result.append(GameDefinition(
                game_id=game_id,
                script_path=str(path),
                title=title or game_id.replace("_", " ").title(),
                roles=roles,
                recommended_player_role=recommended,
            ))
        return result

    def _build_roles_dict(self, roles: list[dict[str, Any]]) -> dict[str, dict[str, Any]] | None:
        """从 bundle.roles 列表构建 {role_id: role_data} 字典。"""
        if not roles:
            return None
        roles_dict: dict[str, dict[str, Any]] = {}
        for role in roles:
            if isinstance(role, dict):
                role_id = role.get("name")
                if role_id:
                    roles_dict[role_id] = {
                        "name": role_id,
                        "display_name": role.get("display_name", role_id),
                        "description": role.get("description", ""),
                        "portrait_url": role.get("portrait_url", ""),
                        "emoji": role.get("emoji", ""),
                        "voice_id": role.get("voice_id", ""),
                        "faction": role.get("faction", ""),
                    }
        return roles_dict if roles_dict else None

    def _extract_roles_from_package(self, pkg_dir: Path) -> tuple[dict[str, dict[str, Any]] | None, str | None, str | None]:
        """从包目录提取 roles 信息（同步 fallback）。"""
        try:
            import yaml
            manifest_path = pkg_dir / "manifest.yaml"
            meta = {}
            if manifest_path.exists():
                doc = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
                meta = doc.get("meta", {})
            recommended = meta.get("recommended_player_role")
            title = meta.get("display_name") or meta.get("title")

            roles_path = pkg_dir / "roles.yaml"
            if not roles_path.exists():
                return None, recommended, title
            roles_data = yaml.safe_load(roles_path.read_text(encoding="utf-8")) or {}
            roles_list = roles_data.get("roles", []) if isinstance(roles_data, dict) else roles_data
            if not roles_list:
                return None, recommended, title

            roles_dict = {}
            for role in roles_list:
                if isinstance(role, dict):
                    role_id = role.get("name")
                    if role_id:
                        roles_dict[role_id] = {
                            "name": role_id,
                            "display_name": role.get("display_name", role_id),
                            "description": role.get("description", ""),
                            "portrait_url": role.get("portrait_url", ""),
                            "emoji": role.get("emoji", ""),
                            "voice_id": role.get("voice_id", ""),
                            "faction": role.get("faction", ""),
                        }
            return roles_dict if roles_dict else None, recommended, title
        except Exception:
            return None, None, None

    def _extract_roles_from_script(self, script_path: Path) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
        """从脚本中提取 roles 信息（同步 fallback）。"""
        try:
            import yaml
            doc = yaml.safe_load(script_path.read_text(encoding="utf-8")) or {}
            meta = doc.get("meta", {})
            recommended = meta.get("recommended_player_role")
            roles_list = doc.get("roles", [])
            if not roles_list:
                return None, recommended
            roles_dict = {}
            for role in roles_list:
                if isinstance(role, dict):
                    role_id = role.get("name")
                    if role_id:
                        roles_dict[role_id] = {
                            "name": role_id,
                            "display_name": role.get("display_name", role_id),
                            "description": role.get("description", ""),
                            "portrait_url": role.get("portrait_url", ""),
                            "emoji": role.get("emoji", ""),
                            "voice_id": role.get("voice_id", ""),
                            "faction": role.get("faction", ""),
                        }
            return roles_dict if roles_dict else None, recommended
        except Exception:
            return None, None

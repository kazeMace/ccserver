"""Game catalog for Drama Engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from drama_engine.application.script_library import SCRIPT_LIBRARY_ROOT, iter_builtin_script_paths


@dataclass(frozen=True, slots=True)
class GameDefinition:
    """可创建游戏定义。"""

    game_id: str
    script_path: str
    title: str
    roles: dict[str, dict[str, Any]] | None = None  # 新增：游戏角色定义 {role_id: role_data}
    recommended_player_role: str | None = None  # 新增：推荐玩家扮演的角色


class GameCatalog:
    """游戏目录。"""

    def __init__(self, scripts_root: str | Path | None = None) -> None:
        if scripts_root is None:
            scripts_root = SCRIPT_LIBRARY_ROOT
        self.scripts_root = Path(scripts_root)

    def list_games(self) -> list[GameDefinition]:
        """列出 scripts_root 下的 YAML 游戏。"""
        result = []
        for path in iter_builtin_script_paths(self.scripts_root):
            game_id = path.stem
            # 尝试编译脚本以读取 roles 信息
            roles, recommended = self._extract_roles_from_script(path)
            result.append(GameDefinition(
                game_id=game_id,
                script_path=str(path),
                title=game_id.replace("_", " ").title(),
                roles=roles,
                recommended_player_role=recommended,
            ))
        return result

    def get_game(self, game_id: str) -> GameDefinition:
        """按 game_id 获取游戏定义。"""
        assert game_id, "game_id 不能为空"
        for game in self.list_games():
            if game.game_id == game_id:
                return game
        raise KeyError(f"游戏不存在: {game_id}")

    def _extract_roles_from_script(self, script_path: Path) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
        """从脚本中提取 roles 信息（不完整编译，只读 YAML）。

        返回: (roles_dict, recommended_player_role)
        """
        try:
            import yaml
            with open(script_path, 'r', encoding='utf-8') as f:
                doc = yaml.safe_load(f)

            # 读取 meta.recommended_player_role
            meta = doc.get('meta', {})
            recommended = meta.get('recommended_player_role')

            # 读取 roles 定义
            roles_list = doc.get('roles', [])
            if not roles_list:
                return None, recommended

            # 转换为 {role_id: role_data} 格式
            roles_dict = {}
            for role in roles_list:
                if isinstance(role, dict):
                    role_id = role.get('name')
                    if role_id:
                        roles_dict[role_id] = {
                            'name': role_id,
                            'display_name': role.get('display_name', role_id),
                            'description': role.get('description', ''),
                            'portrait_url': role.get('portrait_url', ''),
                            'emoji': role.get('emoji', ''),
                            'voice_id': role.get('voice_id', ''),
                            'faction': role.get('faction', ''),
                        }

            return roles_dict if roles_dict else None, recommended
        except Exception:
            # 读取失败不影响游戏列表（只是没有 roles 信息）
            return None, None

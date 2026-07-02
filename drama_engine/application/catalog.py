"""Game catalog for Drama Engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GameDefinition:
    """可创建游戏定义。"""

    game_id: str
    script_path: str
    title: str


class GameCatalog:
    """游戏目录。"""

    def __init__(self, scripts_root: str | Path | None = None) -> None:
        if scripts_root is None:
            scripts_root = Path(__file__).resolve().parents[1] / "core" / "scripts"
        self.scripts_root = Path(scripts_root)

    def list_games(self) -> list[GameDefinition]:
        """列出 scripts_root 下的 YAML 游戏。"""
        if not self.scripts_root.exists():
            return []
        result = []
        for path in sorted(self.scripts_root.glob("*.yaml")):
            game_id = path.stem
            result.append(GameDefinition(
                game_id=game_id,
                script_path=str(path),
                title=game_id.replace("_", " ").title(),
            ))
        return result

    def get_game(self, game_id: str) -> GameDefinition:
        """按 game_id 获取游戏定义。"""
        assert game_id, "game_id 不能为空"
        for game in self.list_games():
            if game.game_id == game_id:
                return game
        raise KeyError(f"游戏不存在: {game_id}")

"""DSL game pack declarations."""

from drama_engine.core.dsl.game_packs.registry import (
    GamePackRegistry,
    GamePackSpec,
    build_default_game_pack_registry,
)

__all__ = [
    "GamePackSpec",
    "GamePackRegistry",
    "build_default_game_pack_registry",
]

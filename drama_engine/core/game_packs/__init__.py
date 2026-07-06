"""机制库与 GamePack（机制集合声明）。

设计取向（用户明确）：
- 可复用单元是「机制 Mechanism」，不是「游戏」。机制用 Python 实现一次，注册为 DSL
  已有扩展点（effect / condition），DSL 用现成语法按名引用。
- GamePack 是「一组机制的声明式集合」（manifest），声明引入哪些机制 + 默认 config +
  需要哪些 extensions。引入一个 GamePack = 批量引入机制。GamePack 本身不写编排、
  不写硬编码规则，因此 NL→DSL skill 也能生成它。
- 五子棋/象棋/围棋共用 board 机制；大富翁用 board+dice+economy；狼人杀/阿瓦隆用
  social 机制。换个同类游戏要某机制，DSL 里按名引用即可。

本包：
- registry.py：运行层 GamePack manifest 注册（plugin_id → 机制清单 + 默认 config）。
- builtins.py：注册 builtin.board / builtin.dice / builtin.economy / builtin.cards /
  builtin.social 等机制集合，并把机制注册进 PluginRegistry。
- mechanisms/：各领域机制实现（effect/condition handler）。
"""

from drama_engine.core.game_packs.registry import (
    GamePackManifest,
    GamePackRuntimeRegistry,
    build_default_game_pack_runtime_registry,
)

__all__ = [
    "GamePackManifest",
    "GamePackRuntimeRegistry",
    "build_default_game_pack_runtime_registry",
]

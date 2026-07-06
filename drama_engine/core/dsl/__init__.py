"""DSL core registries.

旧的 `YamlCompiler` 已随固定流程 runtime 一起删除。interactive_session 使用自己的
`InteractiveSessionCompiler`（见 core/runtime/interactive_session/compiler.py）。
"""

from drama_engine.core.dsl.registry import (
    ActionPolicySpec,
    DslRegistry,
    SceneTypeSpec,
    build_default_dsl_registry,
)

__all__ = [
    "ActionPolicySpec",
    "DslRegistry",
    "SceneTypeSpec",
    "build_default_dsl_registry",
]

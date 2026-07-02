"""DSL core registries and compiler entry points."""

from importlib import import_module
from typing import Any

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
    "YamlCompiler",
    "build_default_dsl_registry",
]


def __getattr__(name: str) -> Any:
    """Lazily expose compiler symbols without creating import cycles."""
    if name == "YamlCompiler":
        module = import_module("drama_engine.core.dsl.compiler")
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'drama_engine.core.dsl' has no attribute {name!r}")

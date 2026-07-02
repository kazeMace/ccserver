"""Machine-readable DSL schema and capability discovery helpers.

机器可读 DSL 能力发现入口。UGC authoring skill 可以读取这些结构，
了解当前 core DSL 支持哪些 scene/action/response/runtime/view/input 能力。
"""

from drama_engine.core.dsl.schema.core_schema import (
    build_core_dsl_capabilities,
    build_core_dsl_schema,
)
from drama_engine.core.dsl.schema.types import DslCapability, DslField, DslSchema

__all__ = [
    "DslCapability",
    "DslField",
    "DslSchema",
    "build_core_dsl_capabilities",
    "build_core_dsl_schema",
]

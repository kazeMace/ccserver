"""Small data types for machine-readable DSL schema.

这些类型刻意保持简单：只表达名称、说明、字段列表和值域，方便新人维护，
也方便后续 UGC Authoring Skill 直接序列化成 JSON。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DslField:
    """A field in a DSL schema section.

    字段说明。

    Attributes:
        name: Field name in YAML.
        type: Human-readable type expression.
        required: Whether this field is required.
        description: Short explanation for authors and validators.
        allowed_values: Optional allowed value list.
    """

    name: str
    type: str
    required: bool
    description: str
    allowed_values: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Convert field metadata to a JSON-serializable dict."""
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
            "allowed_values": list(self.allowed_values),
        }


@dataclass(frozen=True, slots=True)
class DslCapability:
    """A named DSL capability such as a scene type or response schema."""

    name: str
    kind: str
    description: str = ""
    defaults: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert capability metadata to a JSON-serializable dict."""
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "defaults": dict(self.defaults),
        }


@dataclass(frozen=True, slots=True)
class DslSchema:
    """A machine-readable schema section."""

    name: str
    description: str
    fields: tuple[DslField, ...]

    def to_dict(self) -> dict[str, Any]:
        """Convert schema section to a JSON-serializable dict."""
        return {
            "name": self.name,
            "description": self.description,
            "fields": [field.to_dict() for field in self.fields],
        }

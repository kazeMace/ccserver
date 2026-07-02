"""DSL domain extension declarations."""

from drama_engine.core.dsl.extensions.registry import (
    DomainExtensionRegistry,
    DomainExtensionSpec,
    build_default_domain_extension_registry,
)

__all__ = [
    "DomainExtensionSpec",
    "DomainExtensionRegistry",
    "build_default_domain_extension_registry",
]

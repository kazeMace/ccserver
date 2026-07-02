"""View projection port."""

from __future__ import annotations

from typing import Any

class BaseViewProjector:
    """Base class for runner view projectors."""

    def project(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Project domain state into ViewHost events."""
        _ = args
        _ = kwargs
        raise NotImplementedError

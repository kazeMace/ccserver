from pathlib import Path

from .sdk import (
    estimate_tokens,
    generate_message_id,
    get_block_attr,
    normalize_content_blocks,
)

from .yaml_parser import parse as parse_frontmatter


def safe_path(workdir: Path, p: str) -> Path:
    """
    Resolve p relative to workdir and verify it does not escape the workspace.
    Raises ValueError if the resolved path is outside workdir.
    """
    path = (workdir / p).resolve()
    if not path.is_relative_to(workdir.resolve()):
        raise ValueError(f"Path escapes workspace: {p!r}")
    return path

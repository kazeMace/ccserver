from pathlib import Path


def safe_path(workdir: Path, p: str) -> Path:
    """
    Resolve p relative to workdir and verify it does not escape the workspace.
    Raises ValueError if the resolved path is outside workdir.
    """
    path = (workdir / p).resolve()
    if not path.is_relative_to(workdir.resolve()):
        raise ValueError(f"Path escapes workspace: {p!r}")
    return path

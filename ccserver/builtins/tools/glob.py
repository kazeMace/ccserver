import glob as globlib
import os
from pathlib import Path

from .base import BuiltinTools, ToolParam, ToolResult
from ccserver.utils import safe_path


class BTGlob(BuiltinTools):

    name = "Glob"

    description = (
        "Find files in the workspace by glob pattern (file name / path pattern matching). "
        "Results are sorted by modification time — most recently modified first. "
        "Use this when you know the file name pattern or extension you're looking for. "
        "Use Grep instead when you need to find files by their content. "
        "Supports standard glob syntax: * matches any characters within one directory level; "
        "** matches across directory levels recursively. "
        "Returns a newline-separated list of matching absolute paths, or 'none' if no matches."
    )

    params = {
        "pattern": ToolParam(
            type="string",
            description=(
                "Glob pattern to match files against. "
                "Examples: '**/*.py' (all Python files), 'src/**/*.ts' (TypeScript under src/), "
                "'*.json' (JSON files in current dir), 'tests/**/test_*.py' (test files). "
                "Pattern is matched relative to the search directory."
            ),
        ),
        "path": ToolParam(
            type="string",
            description=(
                "Directory to search in, relative to workspace root. "
                "Default: '.' (entire workspace). "
                "Use to narrow the search scope, e.g. 'src' to search only under src/."
            ),
            required=False,
        ),
    }

    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def run(self, pattern: str, path: str = ".") -> ToolResult:
        try:
            base = str(safe_path(self.workdir, path))
            full_pattern = (base + "/" + pattern).replace("//", "/")
            files = globlib.glob(full_pattern, recursive=True)
            files = sorted(
                files,
                key=lambda f: os.path.getmtime(f) if os.path.isfile(f) else 0,
                reverse=True,
            )
            return ToolResult.ok("\n".join(files) or "none")
        except Exception as e:
            return ToolResult.error(str(e))

import glob as globlib
import re
from pathlib import Path

from .base import BuiltinTools, ToolParam, ToolResult
from ccserver.utils import safe_path


class BTGrep(BuiltinTools):

    name = "Grep"

    description = (
        "Search files in the workspace by regular expression pattern (content search). "
        "Returns matching lines in the format 'filepath:line_number:line_content', "
        "capped at 50 results. "
        "Use this to find where a function, class, variable, or string is defined or referenced — "
        "it's faster than reading files manually. "
        "Use Glob instead when you want to find files by name or extension pattern, "
        "not by content. "
        "Directories and binary files are skipped automatically."
    )

    params = {
        "pattern": ToolParam(
            type="string",
            description=(
                "Python regular expression to search for. "
                "Examples: 'def run' (function definition), 'class Foo\\b' (class name), "
                "'TODO.*fix' (TODO comments), 'import (os|sys)' (import statements). "
                "The pattern is case-sensitive by default; prefix with '(?i)' for case-insensitive."
            ),
        ),
        "path": ToolParam(
            type="string",
            description=(
                "File or directory to search in, relative to workspace root. "
                "Default: '.' (entire workspace). "
                "Pass a file path to search only that file, "
                "or a directory path to limit the search scope."
            ),
            required=False,
        ),
    }

    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def run(self, pattern: str, path: str = ".") -> ToolResult:
        try:
            regex = re.compile(pattern)
            search_root = str(safe_path(self.workdir, path))
            hits = []
            for filepath in globlib.glob(search_root + "/**", recursive=True):
                try:
                    with open(filepath, errors="replace") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                hits.append(f"{filepath}:{line_num}:{line.rstrip()}")
                except Exception:
                    pass
            return ToolResult.ok("\n".join(hits[:50]) or "none")
        except re.error as e:
            return ToolResult.error(f"Invalid regex pattern: {e}")
        except Exception as e:
            return ToolResult.error(str(e))

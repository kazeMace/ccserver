from pathlib import Path

from .bt_base import BaseTool, ToolParam, ToolResult
from .utils import safe_path


class BTRead(BaseTool):

    name = "Read"

    description = (
        "Read the contents of a file inside the workspace. "
        "Output is returned with 1-based line numbers (format: 'N\\tline content') "
        "so you can reference exact lines in subsequent Edit calls. "
        "Use offset and limit to read large files in chunks rather than all at once — "
        "do not read entire large files when you only need a specific section. "
        "Not suitable for binary files (images, archives, compiled binaries). "
        "Returns an error if the path does not exist or is outside the workspace."
    )

    params = {
        "file_path": ToolParam(
            type="string",
            description=(
                "Path to the file, relative to workspace root. "
                "Example: 'src/tools/bt_bash.py', 'README.md'."
            ),
        ),
        "offset": ToolParam(
            type="integer",
            description=(
                "Line number to start reading from, 0-indexed (line 0 = first line). "
                "Default: 0 (start from the beginning). "
                "Use together with limit to page through large files."
            ),
            required=False,
        ),
        "limit": ToolParam(
            type="integer",
            description=(
                "Maximum number of lines to return. "
                "Omit to read from offset to end of file. "
                "Recommended: 200-500 for large files to avoid token overload."
            ),
            required=False,
        ),
    }

    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def run(self, file_path: str, offset: int = 0, limit: int = None) -> ToolResult:
        try:
            lines = safe_path(self.workdir, file_path).read_text().splitlines()
            if offset:
                lines = lines[offset:]
            if limit is not None and limit < len(lines):
                lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
            numbered = [f"{offset + i + 1}\t{line}" for i, line in enumerate(lines)]
            return ToolResult.ok("\n".join(numbered)[:50_000])
        except Exception as e:
            return ToolResult.error(str(e))

from pathlib import Path

from .base import BuiltinTools, ToolParam, ToolResult
from ccserver.utils import safe_path


class BTWrite(BuiltinTools):

    name = "Write"

    description = (
        "Write content to a file inside the workspace. "
        "Creates the file (and any missing parent directories) if it does not exist; "
        "overwrites the entire file if it already exists. "
        "Use Edit instead when you only need to change part of an existing file — "
        "Write replaces ALL content and should be reserved for new files "
        "or complete rewrites. "
        "The destination path must stay within the workspace root."
    )

    params = {
        "file_path": ToolParam(
            type="string",
            description=(
                "Destination path relative to workspace root. "
                "Parent directories are created automatically. "
                "Example: 'src/utils/helpers.py', 'docs/api.md'."
            ),
        ),
        "content": ToolParam(
            type="string",
            description=(
                "Full content to write to the file. "
                "This completely replaces any existing file content. "
                "Ensure correct encoding and line endings for the target file type."
            ),
        ),
    }

    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def run(self, file_path: str, content: str) -> ToolResult:
        try:
            fp = safe_path(self.workdir, file_path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
            return ToolResult.ok(f"Wrote {len(content)} bytes to {file_path}")
        except Exception as e:
            return ToolResult.error(str(e))

from pathlib import Path

from .bt_base import BaseTool, ToolParam, ToolResult
from .utils import safe_path


class BTEdit(BaseTool):

    name = "Edit"

    description = (
        "Replace a specific string in an existing file with new content. "
        "Always call Read first to verify the exact text before editing — "
        "old_text must match the file character-for-character (spaces, tabs, newlines). "
        "If old_text is not unique, provide more surrounding context lines to make it unique, "
        "or set all=true to replace every occurrence. "
        "Use Write instead when you need to replace the entire file content. "
        "Use Bash with 'mv' when you need to move or rename a file."
    )

    params = {
        "file_path": ToolParam(
            type="string",
            description=(
                "Path to the file to modify, relative to workspace root. "
                "Example: 'src/tools/bt_bash.py'."
            ),
        ),
        "old_string": ToolParam(
            type="string",
            description=(
                "The exact text to find and replace. "
                "Must match the file content character-for-character, "
                "including indentation, leading spaces, and line endings. "
                "Include 2-3 surrounding lines as context if the target text is short or repeated."
            ),
        ),
        "new_string": ToolParam(
            type="string",
            description=(
                "The replacement text. "
                "Must maintain correct indentation to avoid syntax errors. "
                "Pass an empty string to delete old_string without replacement."
            ),
        ),
        "replace_all": ToolParam(
            type="boolean",
            description=(
                "If true, replace every occurrence of old_string in the file. "
                "If false (default), the edit fails when old_string appears more than once — "
                "use this as a safety check against unintended multi-replacements."
            ),
            required=False,
        ),
    }

    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def run(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> ToolResult:
        try:
            fp = safe_path(self.workdir, file_path)
            text = fp.read_text()
            if old_string not in text:
                return ToolResult.error(f"old_string not found in {file_path}")
            count = text.count(old_string)
            if not replace_all and count > 1:
                return ToolResult.error(
                    f"old_string appears {count} times in {file_path}. "
                    "Provide more context to make it unique, or set replace_all=true."
                )
            result = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
            fp.write_text(result)
            replaced = count if replace_all else 1
            return ToolResult.ok(f"Replaced {replaced} occurrence(s) in {file_path}")
        except Exception as e:
            return ToolResult.error(str(e))

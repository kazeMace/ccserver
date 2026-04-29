import base64
from pathlib import Path

from .base import BuiltinTools, ToolParam, ToolResult
from ccserver.utils import safe_path


# 支持直接读取并显示的图片扩展名
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 扩展名到 MIME 类型映射
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class BTRead(BuiltinTools):

    name = "Read"
    risk = "low"
    tags = ["fs", "read-only"]

    description = (
        "Read the contents of a file inside the workspace. "
        "Output is returned with 1-based line numbers (format: 'N\\tline content') "
        "so you can reference exact lines in subsequent Edit calls. "
        "Use offset and limit to read large files in chunks rather than all at once — "
        "do not read entire large files when you only need a specific section. "
        "Supports image files (png, jpg, jpeg, gif, webp, bmp) — returns the image directly for visual inspection. "
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
            # 图片文件：允许绝对路径（agent 可能截图后保存到 tmp 目录再读取）
            ext = Path(file_path).suffix.lower()
            if ext in _IMAGE_EXTENSIONS:
                # 绝对路径直接使用；相对路径 resolve 到 workdir
                path = Path(file_path) if Path(file_path).is_absolute() else safe_path(self.workdir, file_path)
            else:
                path = safe_path(self.workdir, file_path)

            # 图片文件：返回多模态内容供 VLM 直接查看
            if ext in _IMAGE_EXTENSIONS:
                img_bytes = path.read_bytes()
                img_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
                media_type = _IMAGE_MIME.get(ext, "image/png")
                return ToolResult.multimodal([
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"图像文件：{file_path}（{len(img_bytes)} bytes，格式 {media_type}）",
                    },
                ])

            # 文本文件：逐行读取并添加行号
            lines = path.read_text().splitlines()
            if offset:
                lines = lines[offset:]
            if limit is not None and limit < len(lines):
                lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
            numbered = [f"{offset + i + 1}\t{line}" for i, line in enumerate(lines)]
            return ToolResult.ok("\n".join(numbered)[:50_000])
        except Exception as e:
            return ToolResult.error(str(e))

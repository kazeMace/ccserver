import glob as globlib
import re
import shutil
import subprocess
from pathlib import Path

from .base import BuiltinTools, ToolParam, ToolResult
from ccserver.utils import safe_path

# 模块加载时检测一次 ripgrep 是否可用，避免每次调用都检测
# ripgrep (rg) 性能远优于纯 Python 实现，优先使用
_RG_BIN: str | None = shutil.which("rg")


def _grep_rg(pattern: str, search_root: str, max_results: int) -> list[str]:
    """
    使用 ripgrep 搜索文件内容。

    ripgrep 会自动跳过二进制文件、.gitignore 中的文件，性能远优于纯 Python 遍历。
    输出格式: filepath:line_number:line_content（与纯 Python 版本保持一致）

    Args:
        pattern: Python 正则表达式字符串（ripgrep 兼容 PCRE/RE2 语法）
        search_root: 搜索根目录的绝对路径字符串
        max_results: 最多返回的行数

    Returns:
        匹配行列表，格式为 "filepath:line_num:content"

    Raises:
        subprocess.SubprocessError: ripgrep 进程异常时抛出
    """
    # --no-heading: 每行带完整路径，不分组显示
    # --line-number: 显示行号
    # --with-filename: 每行显示文件名（多文件时默认开启，单文件时需要显式指定）
    # --max-count 结合 --max-filesize 防止单文件过大拖慢速度
    # -m {max_results}: 全局最多返回 max_results 条结果（ripgrep 不支持全局 -m，用 --max-count 只限单文件）
    # 用 head 在调用侧截断更简单，这里直接让 rg 跑完再切片
    cmd = [
        _RG_BIN,
        "--no-heading",        # 输出格式: file:line:content
        "--line-number",       # 带行号
        "--with-filename",     # 带文件名
        "--color=never",       # 不输出 ANSI 颜色码
        pattern,
        search_root,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        errors="replace",      # 二进制/非 UTF-8 文件不崩溃
    )
    # returncode 1 表示无匹配，不是错误；其他非零值才是真实错误
    # 但这里我们不抛出，让调用方处理空列表即可
    lines = result.stdout.splitlines()
    return lines[:max_results]


def _grep_python(regex: re.Pattern, search_root: str, max_results: int) -> list[str]:
    """
    纯 Python 实现的文件内容搜索（ripgrep 不可用时的 fallback）。

    递归遍历目录下所有文件，逐行进行正则匹配。
    目录和无法读取的文件（二进制、权限不足等）会被静默跳过。

    Args:
        regex: 已编译的 Python 正则表达式对象
        search_root: 搜索根目录的绝对路径字符串
        max_results: 最多返回的行数

    Returns:
        匹配行列表，格式为 "filepath:line_num:content"
    """
    hits = []
    for filepath in globlib.glob(search_root + "/**", recursive=True):
        if len(hits) >= max_results:
            break
        try:
            with open(filepath, errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    if regex.search(line):
                        hits.append(f"{filepath}:{line_num}:{line.rstrip()}")
                        if len(hits) >= max_results:
                            break
        except Exception:
            # 目录、二进制文件、权限不足等情况静默跳过
            pass
    return hits


class BTGrep(BuiltinTools):

    name = "Grep"
    risk = "low"
    tags = ["fs", "read-only"]

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

    # 单次搜索最多返回的结果行数
    MAX_RESULTS = 50

    def __init__(self, workdir: Path):
        self.workdir = workdir

    async def run(self, pattern: str, path: str = ".") -> ToolResult:
        """
        执行正则搜索。优先使用 ripgrep，不可用时自动 fallback 到纯 Python 实现。

        Args:
            pattern: Python 正则表达式字符串
            path: 搜索路径，相对于 workspace 根目录，默认为 "."

        Returns:
            ToolResult.ok: 匹配结果，每行格式 "filepath:line_number:content"；无结果时返回 "none"
            ToolResult.error: 正则语法错误或路径错误时返回错误信息
        """
        # 先验证正则语法，两种实现都需要合法的 pattern
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult.error(f"Invalid regex pattern: {e}")

        # 路径安全校验，防止逃逸到 workspace 外
        try:
            search_root = str(safe_path(self.workdir, path))
        except ValueError as e:
            return ToolResult.error(str(e))

        try:
            if _RG_BIN:
                # ripgrep 可用：性能更好，自动跳过 .gitignore 文件和二进制文件
                hits = _grep_rg(pattern, search_root, self.MAX_RESULTS)
            else:
                # fallback：纯 Python 实现
                hits = _grep_python(regex, search_root, self.MAX_RESULTS)

            return ToolResult.ok("\n".join(hits) or "none")

        except Exception as e:
            return ToolResult.error(str(e))

"""
MCPOutcome — MCP 工具调用结果，替代裸字符串返回。

设计参考 Task.VALID_STATUSES：类常量定义合法状态值 + 普通字符串字段，
简洁直观，代码新人一看就懂。

状态：
  ok     — 调用成功，content 为结果文本
  error  — 调用失败，content 为错误信息

用法：
    outcome = await client.call("tool_name", {"arg": "value"})
    if outcome.is_error:
        print(outcome.content)  # 错误信息
    else:
        print(outcome.content)  # 成功结果
"""

from __future__ import annotations

# 常见的错误内容前缀/模式，MCP server 返回这些时视为调用失败
# 英文模式（小写匹配）
_ERROR_PATTERNS_LOWER = (
    "error:",
    "[error]",
    "exception:",
    "502",
    "503",
    "timeout",
    "not found",
    "not_found",
    "unavailable",
    "connection refused",
    "connection reset",
)
# 中文模式（原始大小写匹配）
_ERROR_PATTERNS_CASE = (
    "错误",
    "异常",
    "失败",
    "技术问题",
    "无法生成",
    "服务端错误",
    "服务端异常",
    "服务异常",
    "不可用",
    "连接失败",
    "网络错误",
)


class MCPOutcome:
    """
    MCP 工具调用结果，统一成功/失败判断接口。
    """

    # 类常量 — 合法状态值（参考 Task.VALID_STATUSES 模式）
    OK = "ok"
    ERROR = "error"

    def __init__(self, status: str, content: str, server: str | None = None):
        """
        :param status:   调用状态，OK 或 ERROR
        :param content:  成功时为结果文本，失败时为错误信息
        :param server:  来源 server 名称
        """
        assert status in (self.OK, self.ERROR), f"Invalid status: {status!r}"
        self.status = status
        self.content = content
        self.server = server

    @property
    def is_error(self) -> bool:
        """判断是否为错误结果。"""
        return self.status == self.ERROR

    @classmethod
    def ok(cls, content: str, server: str | None = None) -> "MCPOutcome":
        """
        构建结果，自动检测 content 是否包含错误模式。

        content 中出现常见错误关键字（502/503/timeout/error 等）时，
        自动将 status 设为 ERROR，避免 MCP server 通过响应文本返回错误
        时被误认为成功。
        """
        if cls._looks_like_error(content):
            return cls(status=cls.ERROR, content=content, server=server)
        return cls(status=cls.OK, content=content, server=server)

    @classmethod
    def error(cls, message: str, server: str | None = None) -> "MCPOutcome":
        """构建错误结果。"""
        return cls(status=cls.ERROR, content=message, server=server)

    @staticmethod
    def _looks_like_error(content: str) -> bool:
        """检测 content 是否包含错误模式（中英文）。"""
        if not content:
            return False
        # 精确匹配：内容整体就是 "Error: ..." 格式
        stripped = content.strip()
        if stripped.startswith("Error:") or stripped.startswith("error:"):
            return True
        # 英文模式（小写匹配）
        lower = content.lower()
        if any(p in lower for p in _ERROR_PATTERNS_LOWER):
            return True
        # 中文模式（原始大小写匹配）
        if any(p in content for p in _ERROR_PATTERNS_CASE):
            return True
        return False

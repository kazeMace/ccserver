"""
log — centralized loguru configuration.
日志配置中心，负责格式定义、颜色主题、文件轮转，以及标准 logging → loguru 桥接。

Call setup_logging() once at process startup (tui.py / server.py).
All other modules just: from loguru import logger
"""

import logging
import sys
from pathlib import Path

from loguru import logger

# ─── 文件日志格式（纯文本，无 ANSI，方便 grep）──────────────────────────────────
# 示例：2026-04-23 10:23:45.123 | INFO     | ccserver.agent:run:42 | 消息内容
_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level:<8} | "
    "{name}:{function}:{line} | "
    "{message}"
)

# ─── 终端日志格式（带 loguru 颜色标签）──────────────────────────────────────────
#
# 布局（所有行左对齐，来源标签固定宽度在最左列）：
#
#   [ccserver  ]  11:42:20.123  INFO      agent:1630  MCP call | session=abc
#   [MCP:db    ]  11:42:20.124  DEBUG     Processing request of type CallToolRequest
#   [uvicorn   ]  11:42:20.125  INFO      POST /chat/stream → 200  12ms
#
# 颜色方案：
#   来源标签   → 蓝色 <blue>（固定 12 字符宽，含方括号）
#   时间       → 灰色 <dim>
#   TRACE      → 暗青色
#   DEBUG      → 紫色（MCP debug 最常见，与业务 INFO 绿色明显区分）
#   INFO       → 绿色
#   SUCCESS    → 亮绿色
#   WARNING    → 黄色
#   ERROR      → 红色
#   CRITICAL   → 粗红色
#   模块名     → 青色 <cyan>（业务日志专用列，第三方桥接日志省略）
#
# extra 字段约定：
#   source_tag  : str  来源标签原始值，如 "MCP:db"、"uvicorn"、"ccserver"
#   intercepted : bool 来自第三方桥接时为 True，格式串省略 name:line 列
#
# loguru 颜色标签参考：https://loguru.readthedocs.io/en/stable/api/logger.html#color

# 来源标签列宽（含两侧方括号），所有行对齐到此宽度
_TAG_WIDTH = 12

# 业务日志格式：含 name:line 定位列
_STDERR_FORMAT = (
    "<blue>[{{extra[source_tag]:<{w}}}]</blue> "
    "| <dim>{{time:HH:mm:ss.SSS}}</dim> "
    "| <level>{{level:<8}}</level> "
    "| <cyan>{{name}}</cyan>:<cyan>{{line}}</cyan> "
    "- <level>{{message}}</level>"
).format(w=_TAG_WIDTH - 2)   # -2 是因为方括号占 2 字符

# 第三方桥接日志格式：省略 name:line（那里只会显示桥接代码位置，无意义）
_INTERCEPTED_FORMAT = (
    "<blue>[{{extra[source_tag]:<{w}}}]</blue> "
    "| <dim>{{time:HH:mm:ss.SSS}}</dim> "
    "| <level>{{level:<8}}</level> "
    "| <level>{{message}}</level>"
).format(w=_TAG_WIDTH - 2)

# ─── 自定义 level 颜色（覆盖 loguru 默认配色）───────────────────────────────────
_LEVEL_COLORS: list[tuple[str, str]] = [
    ("TRACE",    "<dim><cyan>"),
    ("DEBUG",    "<magenta>"),       # 紫色，与 INFO 绿色区分，MCP debug 常见
    ("INFO",     "<green>"),
    ("SUCCESS",  "<bold><green>"),
    ("WARNING",  "<yellow>"),
    ("ERROR",    "<red>"),
    ("CRITICAL", "<bold><red>"),
]

# ─── 需要接管的第三方 logging 来源 ────────────────────────────────────────────
# 格式：(logger_name_prefix, 终端显示的来源标签)
# 按前缀匹配：所有以该前缀开头的子 logger 都使用同一来源标签。
# 例如 "mcp" 可匹配 mcp.server.db、mcp.server.fastmcp 等动态创建的子 logger。
# 匹配规则：record.name == prefix 或 record.name.startswith(prefix + ".")
_INTERCEPT_LOGGERS: list[tuple[str, str]] = [
    ("mcp",      "MCP"),
    ("uvicorn",  "uvicorn"),
    ("fastapi",  "fastapi"),
]


def _apply_level_colors() -> None:
    """将自定义颜色应用到各日志级别，使终端输出更清晰。"""
    for name, color in _LEVEL_COLORS:
        try:
            existing = logger.level(name)
            logger.level(name, no=existing.no, color=color, icon=existing.icon)
        except Exception:
            pass


class _InterceptHandler(logging.Handler):
    """
    将标准 logging 消息桥接到 loguru（前缀路由版本）。

    安装在顶层 logger（如 "mcp"、"uvicorn"）上，
    所有子 logger（如 mcp.server.db）的消息通过 propagate 自然冒泡到此处，
    由 _prefix_to_tag() 按前缀映射出统一的来源标签。

    参考：https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    """

    def emit(self, record: logging.LogRecord) -> None:
        # 将标准 logging level 映射到 loguru level 名称
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno  # 找不到对应 level 时用数字

        # 按前缀表查找来源标签；找不到时直接用 record.name 顶级段（如 "mcp"）
        tag = _prefix_to_tag(record.name)

        # source_tag 放入 extra，由格式串统一渲染到最左列
        # intercepted=True 让 _fmt() 选择省略 name:line 的格式串（那里只会是桥接代码位置）
        # 消息体保留 record.name:lineno，方便定位具体子模块
        logger.bind(intercepted=True, source_tag=tag).opt(exception=record.exc_info).log(
            level, "{}:{}  {}", record.name, record.lineno, record.getMessage()
        )


def _prefix_to_tag(logger_name: str) -> str:
    """
    按 _INTERCEPT_LOGGERS 中的前缀规则，将 logger 名称映射为来源标签。

    匹配规则（优先最长前缀）：
        - 精确匹配：logger_name == prefix  → tag
        - 子模块：  logger_name.startswith(prefix + ".")  → "tag:剩余部分"

    示例：
        "mcp"             → "MCP"
        "mcp.server.db"   → "MCP:server.db"
        "uvicorn.access"  → "uvicorn:access"
        "unknown.lib"     → "unknown.lib"（无匹配时原样返回）
    """
    best_tag = logger_name   # 无匹配时降级显示原始名称
    best_len = -1
    for prefix, tag in _INTERCEPT_LOGGERS:
        if logger_name == prefix:
            if len(prefix) > best_len:
                best_tag = tag
                best_len = len(prefix)
        elif logger_name.startswith(prefix + "."):
            if len(prefix) > best_len:
                suffix = logger_name[len(prefix) + 1:]   # 去掉 "prefix." 后的部分
                # 列宽限制：_TAG_WIDTH - 2 是方括号内可用字符数
                # 固定保留 "tag:" 前缀，suffix 超出时从末尾截取最有区分度的部分
                max_suffix = _TAG_WIDTH - 2 - len(tag) - 1  # -1 是冒号
                if max_suffix > 0 and len(suffix) > max_suffix:
                    suffix = suffix[-max_suffix:]
                best_tag = f"{tag}:{suffix}"
                best_len = len(prefix)
    return best_tag


# 单例 handler，挂载到各顶层 logger，子 logger 通过 propagate 冒泡到这里
_SHARED_HANDLER = _InterceptHandler()


def _setup_intercept_handlers() -> None:
    """
    为 _INTERCEPT_LOGGERS 中每个顶层前缀安装共享 handler。

    策略：
    - 只在顶层 logger（如 "mcp"）上安装 handler，子 logger 保持 propagate=True（默认），
      消息自动冒泡，不需要逐一注册动态创建的子 logger。
    - 顶层 logger 本身设 propagate=False，防止消息再冒泡到 root logger 重复输出。
    """
    for prefix, _ in _INTERCEPT_LOGGERS:
        std_logger = logging.getLogger(prefix)
        # DEBUG(10) 让所有级别都能到达 handler，实际过滤交由 loguru sink 的 level 决定
        std_logger.setLevel(logging.DEBUG)
        # 替换为共享 handler，移除原有 StreamHandler 防止重复输出
        std_logger.handlers = [_SHARED_HANDLER]
        std_logger.propagate = False  # 顶层截断，不再往 root 冒泡


def setup_logging(
    log_dir: Path | None = None,
    level: str | None = None,
    stderr: bool = False,
) -> None:
    """
    初始化 loguru 日志配置，安全可重入（每次调用先清除已有 sink）。

    参数：
        log_dir: 日志文件目录，默认读取 config.LOG_DIR
        level:   最低日志级别，默认读取 config.LOG_LEVEL（如 "INFO"）
        stderr:  True 时额外向终端输出彩色日志（server.py 使用）

    文件 sink：
        - 路径：log_dir/ccserver.log
        - 轮转：10 MB，保留 7 天，压缩为 zip
        - 异步写入（enqueue=True），多进程安全

    终端 sink（stderr=True）：
        - 优先写入 /dev/tty（避免与 stdout 混淆）
        - 无 tty 环境（Docker/CI）降级到 sys.stderr，关闭颜色
    """
    # 延迟导入 config，避免模块加载时产生副作用（读取环境变量、计算路径）
    if log_dir is None or level is None:
        from .config import LOG_DIR, LOG_LEVEL
        log_dir = log_dir or LOG_DIR
        level = level or LOG_LEVEL

    logger.remove()  # 移除所有已有 sink，防止重复输出

    log_dir.mkdir(parents=True, exist_ok=True)
    _apply_level_colors()
    _setup_intercept_handlers()  # 桥接第三方 logging → loguru

    # 业务日志默认绑定 source_tag=ccserver，与第三方来源对齐到同一列
    logger.configure(extra={"source_tag": "ccserver", "intercepted": False})

    # 文件 sink：无颜色，含模块全路径，适合 grep / ELK 接入
    logger.add(
        log_dir / "ccserver.log",
        level=level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        enqueue=True,   # async-safe: 单独写入线程，不阻塞事件循环
        format=_FILE_FORMAT,
        colorize=False,
    )

    if not stderr:
        return

    def _fmt(record: dict) -> str:
        """
        根据日志来源选择格式串：
          - intercepted=True（第三方桥接）→ 省略 name:line 列，真实来源已在消息体中
          - 业务日志               → 保留 name:line 定位列
        """
        if record["extra"].get("intercepted"):
            return _INTERCEPTED_FORMAT + "\n"
        return _STDERR_FORMAT + "\n"

    # 终端 sink：彩色多层次格式
    # 使用 sys.stderr 而非 /dev/tty：
    #   - /dev/tty 绕过终端仿真器的行追踪，导致终端宽度变化时滚动条位置错乱
    #   - sys.stderr 由终端仿真器正常管理，reflow 行为正确
    #   - colorize=True + sys.stderr：loguru 自动检测 isatty()，有 tty 时彩色，无 tty（Docker/CI）时自动关闭
    # 若需恢复 /dev/tty 行为（无论重定向都强制输出到终端），将下面两行替换为：
    #   tty = open("/dev/tty", "w")
    #   logger.add(tty, level=level, format=_fmt, colorize=True, diagnose=False)
    logger.add(sys.stderr, level=level, format=_fmt, colorize=True, diagnose=False)

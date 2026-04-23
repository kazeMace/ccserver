"""
MCPClient — 封装单个 MCP server 的连接、schema 获取和工具调用。

使用 stdio 传输，通过子进程启动 MCP server。
"""

import os
import asyncio
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from loguru import logger

from .outcome import MCPOutcome


def _emit_mcp_line(server_name: str, line: str) -> None:
    """
    将 MCP server 子进程输出的一行文本以合适级别转发给 loguru。

    Args:
        server_name: MCP server 名称，用于日志标记
        line: 子进程 stderr 中的一行文本
    """
    if not line:
        return
    tag = f"MCP:{server_name}"
    bound = logger.bind(intercepted=True, source_tag=tag)
    upper = line.upper()
    if "ERROR" in upper or "EXCEPTION" in upper or "TRACEBACK" in upper:
        bound.error("{}", line)
    elif "WARNING" in upper or "WARN" in upper:
        bound.warning("{}", line)
    else:
        bound.debug("{}", line)


async def _pipe_stderr_to_loguru(server_name: str, read_fd: int) -> None:
    """
    从管道读端 read_fd 异步读取 MCP server 的 stderr 输出，逐行转发给 loguru。

    注意：此协程应作为后台任务运行（asyncio.ensure_future），不需要 await 结果。

    Args:
        server_name: MCP server 名称
        read_fd: os.pipe() 返回的读端文件描述符
    """
    buf = b""
    loop = asyncio.get_event_loop()
    # 将文件描述符包装为 asyncio 流，以便异步读取
    reader = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader),
        os.fdopen(read_fd, "rb", buffering=0),
    )
    try:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buf += chunk
            # 按换行符切割，逐行转发
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                _emit_mcp_line(server_name, line.decode("utf-8", errors="replace").rstrip())
        # 处理末尾不完整行
        if buf:
            _emit_mcp_line(server_name, buf.decode("utf-8", errors="replace").rstrip())
    except Exception:
        pass
    finally:
        transport.close()


class MCPClient:
    """
    管理与单个 MCP server 的连接生命周期。

    使用方式：
        client = MCPClient("weather", "python3", ["server.py"], {})
        await client.connect()
        schemas = client.schemas()          # 传给 Anthropic API 的 tools 参数
        result = await client.call("get_weather", {"city": "上海"})
        await client.close()
    """

    def __init__(
        self,
        server_name: str,
        command: str,
        args: list[str],
        env: dict[str, str],
        cwd: str | None = None,
    ):
        self.server_name = server_name
        self._command = command
        self._args = args
        self._env = env or {}
        self._cwd = cwd
        self._session: ClientSession | None = None
        self._exit_stack = AsyncExitStack()
        self._tools: list = []
        self._stderr_task: asyncio.Task | None = None  # 持有引用，close() 时取消

    def _merged_env(self) -> dict[str, str]:
        """将 .mcp.json 中的 env 与当前进程环境变量合并，mcp.json 中的值优先。"""
        merged = dict(os.environ)
        merged.update(self._env)
        return merged

    async def connect(self):
        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=self._merged_env(),
            cwd=self._cwd,
        )
        logger.debug(
            "MCP connecting | server={} command={} args={} cwd={}",
            self.server_name, self._command, self._args, self._cwd,
        )
        try:
            # 创建真实管道：write_fd 作为子进程 stderr，read_fd 由后台任务异步读取转发给 loguru
            # 不能传纯 Python IO 对象给 stdio_client，因为 anyio 底层需要真实文件描述符
            read_fd, write_fd = os.pipe()
            errlog = os.fdopen(write_fd, "w")
            # 保存任务引用，防止 GC 提前回收；close() 时关闭 errlog 触发 EOF，任务自然退出
            self._stderr_task = asyncio.ensure_future(
                _pipe_stderr_to_loguru(self.server_name, read_fd)
            )

            stdio_transport = await self._exit_stack.enter_async_context(stdio_client(params, errlog=errlog))
            stdio, write = stdio_transport
            self._session = await self._exit_stack.enter_async_context(ClientSession(stdio, write))
            await self._session.initialize()

            result = await self._session.list_tools()
            self._tools = result.tools
            logger.info("MCP connected | server={} tools={}", self.server_name, [t.name for t in self._tools])
        except FileNotFoundError as e:
            # 更详细的错误信息：排查 command 路径问题
            import shutil
            cmd_path = shutil.which(self._command)
            raise FileNotFoundError(
                f"Command not found: '{self._command}' "
                f"(resolved path: {cmd_path}, cwd: {self._cwd}, error: {e})"
            ) from None
        except Exception as e:
            # 保留原始异常链，方便定位真实原因（如 ModuleNotFoundError、ImportError 等）
            raise RuntimeError(
                f"MCP server failed to start | server={self.server_name}, "
                f"command='{self._command}', args={self._args}, cwd={self._cwd} | "
                f"cause={type(e).__name__}: {e}"
            ) from e

    def schemas(self) -> list[dict]:
        """返回 Anthropic API 格式的工具 schema 列表，工具名加 mcp__<server>__ 前缀。"""
        result = []
        for t in self._tools:
            schema = {
                "name": f"mcp__{self.server_name}__{t.name}",
                "description": t.description or "",
                "input_schema": t.inputSchema if isinstance(t.inputSchema, dict) else t.inputSchema.model_dump(),
            }
            result.append(schema)
        return result

    async def call(self, tool_name: str, input_: dict) -> MCPOutcome:
        """
        调用 MCP server 上的工具，返回 MCPOutcome。

        每次调用都重新建立连接，避免跨 asyncio task 共享 anyio cancel scope 的问题。
        """
        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=self._merged_env(),
            cwd=self._cwd,
        )
        try:
            async with AsyncExitStack() as stack:
                # 同 connect()：用真实管道传递 stderr，后台任务转发给 loguru
                read_fd, write_fd = os.pipe()
                errlog = os.fdopen(write_fd, "w")
                stderr_task = asyncio.ensure_future(
                    _pipe_stderr_to_loguru(self.server_name, read_fd)
                )
                stdio_transport = await stack.enter_async_context(stdio_client(params, errlog=errlog))
                stdio, write = stdio_transport
                session = await stack.enter_async_context(ClientSession(stdio, write))
                await session.initialize()
                result = await session.call_tool(tool_name, input_)
                parts = []
                for item in result.content:
                    if hasattr(item, "text"):
                        parts.append(item.text)
                    else:
                        parts.append(str(item))
                # 关闭 errlog（write_fd），让 _pipe_stderr_to_loguru 收到 EOF 自然退出
                errlog.close()
                try:
                    await asyncio.wait_for(stderr_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    stderr_task.cancel()
                return MCPOutcome.ok("\n".join(parts), server=self.server_name)
        except Exception as e:
            logger.error(
                "MCP call failed | server={} tool={} error={}",
                self.server_name, tool_name, e,
            )
            return MCPOutcome.error(str(e), server=self.server_name)

    async def close(self):
        await self._exit_stack.aclose()
        # errlog（write_fd）由 exit_stack 或子进程退出后关闭，_pipe_stderr_to_loguru 会收到 EOF 自然退出。
        # 若任务仍未结束（极少数情况），等待最多 2 秒后取消，避免 "Task was destroyed" 警告。
        if self._stderr_task and not self._stderr_task.done():
            try:
                await asyncio.wait_for(self._stderr_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._stderr_task.cancel()
        logger.debug("MCP disconnected | server={}", self.server_name)

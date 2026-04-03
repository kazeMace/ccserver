"""
MCPClient — 封装单个 MCP server 的连接、schema 获取和工具调用。

使用 stdio 传输，通过子进程启动 MCP server。
"""

import json
import os
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from loguru import logger


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
        stdio_transport = await self._exit_stack.enter_async_context(stdio_client(params))
        stdio, write = stdio_transport
        self._session = await self._exit_stack.enter_async_context(ClientSession(stdio, write))
        await self._session.initialize()

        result = await self._session.list_tools()
        self._tools = result.tools
        logger.info("MCP connected | server={} tools={}", self.server_name, [t.name for t in self._tools])

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

    async def call(self, tool_name: str, input_: dict) -> str:
        """
        调用 MCP server 上的工具，返回文本结果。

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
                stdio_transport = await stack.enter_async_context(stdio_client(params))
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
                return "\n".join(parts)
        except Exception as e:
            logger.error("MCP call failed | server={} tool={} error={}", self.server_name, tool_name, e)
            return f"Error: {e}"

    async def close(self):
        await self._exit_stack.aclose()
        logger.debug("MCP disconnected | server={}", self.server_name)

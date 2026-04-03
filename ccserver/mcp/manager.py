"""
MCPManager — 读取 mcp.json，管理所有 MCPClient 的生命周期。
"""

import json
from pathlib import Path

from loguru import logger

from .client import MCPClient
from ..config import PROJECT_DIR as _PROJECT_DIR


class MCPManager:
    """
    从 mcp.json 加载配置，统一管理所有 MCP server 连接。

    挂载在 Session 上，session 创建时 connect，session 结束时 close。
    """

    def __init__(self, clients: dict[str, MCPClient]):
        self._clients: dict[str, MCPClient] = clients
        self._connected = False

    @classmethod
    def from_config(
        cls,
        config_path: Path,
        project_dir: Path | None = None,
        enabled_servers: list[str] | None = None,
    ) -> "MCPManager":
        """
        从 mcp.json 文件加载配置，返回未连接的 MCPManager。

        enabled_servers: 允许连接的 server 名称列表，None 表示全部允许。
        """
        resolved_cwd = str(project_dir or _PROJECT_DIR)

        if not config_path.exists():
            logger.debug("mcp.json not found, MCP disabled | path={}", config_path)
            return cls({})

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to parse mcp.json | path={} error={}", config_path, e)
            return cls({})

        clients = {}
        for server_name, server_cfg in config.get("mcpServers", {}).items():
            if enabled_servers is not None and server_name not in enabled_servers:
                logger.debug("MCP server disabled by settings | server={}", server_name)
                continue
            clients[server_name] = MCPClient(
                server_name=server_name,
                command=server_cfg.get("command", ""),
                args=server_cfg.get("args", []),
                env=server_cfg.get("env", {}),
                cwd=resolved_cwd,
            )

        return cls(clients)

    async def connect_all(self):
        """并发连接所有 MCP server，已连接时跳过。"""
        if self._connected:
            return
        import asyncio
        tasks = [client.connect() for client in self._clients.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failed = []
        for server_name, result in zip(list(self._clients.keys()), results):
            if isinstance(result, Exception):
                logger.error("MCP connect failed | server={} error={}", server_name, result)
                failed.append(server_name)
        for server_name in failed:
            self._clients.pop(server_name, None)
        self._connected = True

    async def close_all(self):
        import asyncio
        await asyncio.gather(*[c.close() for c in self._clients.values()], return_exceptions=True)

    def schemas(self) -> list[dict]:
        """返回所有已连接 MCP server 的工具 schema 列表。"""
        result = []
        for client in self._clients.values():
            result.extend(client.schemas())
        return result

    def get_client(self, server_name: str) -> MCPClient | None:
        return self._clients.get(server_name)

    def __bool__(self):
        return bool(self._clients)

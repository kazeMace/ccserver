"""Code 执行器 — 执行内联代码片段。

职责：在受限环境中执行用户提供的代码 → 返回结果。

DSL 可配参数:
    executor: code
    language: python            # python / shell / bun_js
    code: "..."                 # 代码内容
    env: {...}                  # 可选环境变量
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from drama_engine.core.executor.base import BaseExecutor, ExecutorRequest, ExecutorResponse

logger = logging.getLogger(__name__)


class CodeExecutor(BaseExecutor):
    """Code 执行器。

    支持 python / shell 内联代码执行。
    Python 代码通过 exec() 在隔离命名空间中执行。
    Shell 代码通过 subprocess 执行。
    """

    SUPPORTED_LANGUAGES = {"python", "shell", "bun_js"}

    async def execute(self, request: ExecutorRequest) -> ExecutorResponse:
        """执行内联代码。

        request.config 必须包含 "language" 和 "code" 字段。
        request.config.env 作为环境变量注入。
        request.payload 中的 state 等可供代码访问。
        """
        language = request.config.get("language", "python")
        code = request.config.get("code")
        assert code, "CodeExecutor 要求 config 中包含 code 字段"
        assert language in self.SUPPORTED_LANGUAGES, (
            f"不支持的语言: {language}，支持: {self.SUPPORTED_LANGUAGES}"
        )

        env = dict(request.config.get("env") or {})

        if language == "python":
            return await self._execute_python(code, env, request.payload)
        elif language == "shell":
            return await self._execute_shell(code, env)
        else:
            return ExecutorResponse(
                success=False,
                error=f"语言 '{language}' 暂未实现",
            )

    async def _execute_python(
        self,
        code: str,
        env: dict[str, str],
        payload: dict[str, Any],
    ) -> ExecutorResponse:
        """在隔离命名空间中执行 Python 代码。"""
        namespace: dict[str, Any] = {
            "env": env,
            "state": payload.get("state", {}),
            "payload": payload,
            "result": None,
        }
        try:
            exec(code, {"__builtins__": __builtins__}, namespace)  # noqa: S102
        except Exception as exc:
            logger.error("[CodeExecutor] Python 执行失败: %s", exc)
            return ExecutorResponse(success=False, error=str(exc))

        result = namespace.get("result")
        if isinstance(result, bool):
            return ExecutorResponse(success=True, data={"result": result})
        if isinstance(result, dict):
            return ExecutorResponse(success=True, data=result)
        return ExecutorResponse(success=True, data={"result": result})

    async def _execute_shell(
        self,
        code: str,
        env: dict[str, str],
    ) -> ExecutorResponse:
        """执行 shell 命令。"""
        import os

        full_env = {**os.environ, **env}
        try:
            proc = await asyncio.create_subprocess_shell(
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace")
                return ExecutorResponse(success=False, error=error_msg)
            output = stdout.decode("utf-8", errors="replace").strip()
            return ExecutorResponse(success=True, data={"output": output})
        except Exception as exc:
            logger.error("[CodeExecutor] Shell 执行失败: %s", exc)
            return ExecutorResponse(success=False, error=str(exc))


__all__ = ["CodeExecutor"]

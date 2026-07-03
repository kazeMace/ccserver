"""Code-backed condition evaluator."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from drama_engine.core.engine import State


class CodeConditionEvaluator:
    """Evaluate trusted condition code for code/python legacy conditions."""

    def __init__(self, entity_matches_filter: Callable[[str, dict, State], bool]):
        """
        Initialize the code evaluator.

        Args:
            entity_matches_filter: Function used by Python helper `entities()`.
        """
        self._entity_matches_filter = entity_matches_filter

    def evaluate(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate an `evaluator: code` condition."""
        runtime = str(cond.get("runtime") or cond.get("language") or "python")
        timeout = int(cond.get("timeout_ms") or 1000) / 1000
        env = {str(k): str(v) for k, v in dict(cond.get("env") or {}).items()}
        code = cond.get("code")
        if not code:
            raise ValueError(f"code evaluator 缺少 code: {cond}")
        if runtime == "python":
            return self.evaluate_python(
                {"code": code, "env": env},
                state=state,
                actor=actor,
                candidate=candidate,
            )
        return self._eval_subprocess_code(
            runtime=runtime,
            code=str(code),
            timeout=timeout,
            env=env,
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
        )

    def evaluate_python(
        self,
        spec: Any,
        state: State,
        actor: str | None,
        candidate: str | None,
    ) -> bool:
        """Execute the legacy trusted Python condition form."""
        extra_env = {}
        if isinstance(spec, str):
            expr = spec
            code = None
        elif isinstance(spec, dict):
            expr = spec.get("expr")
            code = spec.get("code")
            extra_env = dict(spec.get("env") or {})
        else:
            raise ValueError(f"python 条件必须是字符串或字典，收到 {type(spec)}")

        def attr(entity: str, key: str, default: Any = None) -> Any:
            value = state.get_attr(entity, key)
            return default if value is None else value

        def entities(filter_spec: dict | None = None) -> list[str]:
            names = [item for item in state.all_entities() if item != "GAME"]
            if filter_spec is None:
                return names
            return [
                item for item in names
                if self._entity_matches_filter(item, filter_spec, state)
            ]

        def count(filter_spec: dict | None = None) -> int:
            return len(entities(filter_spec))

        def having(**filter_spec: Any) -> list[str]:
            return entities(filter_spec)

        def related(relation: str, who: str) -> set[str]:
            return state.related(relation, who)

        def state_value(path: str, default: Any = None) -> Any:
            if "." not in path:
                return default
            entity_name, attr_name = path.split(".", 1)
            value = state.get_attr(entity_name, attr_name)
            return default if value is None else value

        def env_value(name: str, default: Any = None) -> Any:
            return extra_env.get(name, default)

        safe_builtins = {
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
        }
        env = {
            "actor": actor,
            "candidate": candidate,
            "attr": attr,
            "count": count,
            "entities": entities,
            "having": having,
            "related": related,
            "state": state_value,
            "env": env_value,
        }
        globals_env = {"__builtins__": safe_builtins, **env}
        if expr is not None:
            return bool(eval(expr, globals_env, env))
        if code is not None:
            exec(code, globals_env, env)
            if "result" not in env:
                raise ValueError("python.code 条件必须设置 result 变量")
            return bool(env["result"])
        raise ValueError(f"python 条件缺少 expr 或 code: {spec}")

    def _eval_subprocess_code(
        self,
        runtime: str,
        code: str,
        timeout: float,
        env: dict[str, str],
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Run shell/node/bun condition code."""
        payload = {
            "state": self._state_snapshot(state),
            "actor": actor,
            "candidate": candidate,
            "responses": responses or [],
            "extra": extra or {},
            "entity": entity,
        }
        command = self._code_command(runtime, code)
        process_env = os.environ.copy()
        process_env.update(env)
        process_env["DRAMA_CONDITION_CONTEXT"] = json.dumps(payload, ensure_ascii=False)
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=process_env,
            check=False,
        )
        if runtime == "shell":
            return completed.returncode == 0
        if completed.returncode != 0:
            raise ValueError(
                f"{runtime} condition 退出码 {completed.returncode}: {completed.stderr.strip()}"
            )
        output = completed.stdout.strip()
        if not output:
            return False
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            return output.lower() in {"1", "true", "yes", "ok"}
        if isinstance(decoded, dict):
            return bool(decoded.get("result"))
        return bool(decoded)

    def _code_command(self, runtime: str, code: str) -> list[str]:
        """Return the subprocess command for a code runtime."""
        if runtime == "shell":
            return ["sh", "-c", code]
        if runtime == "node":
            return ["node", "-e", code]
        if runtime in {"bun", "bun_js"}:
            return ["bun", "-e", code]
        raise ValueError(f"code evaluator 不支持 runtime: {runtime}")

    def _state_snapshot(self, state: State) -> dict[str, dict[str, Any]]:
        """Build a read-only state snapshot for external code."""
        return {
            entity: {
                key: state.get_attr(entity, key)
                for key in getattr(state, "_attrs", {}).get(entity, {})
            }
            for entity in sorted(state.all_entities())
        }


__all__ = ["CodeConditionEvaluator"]

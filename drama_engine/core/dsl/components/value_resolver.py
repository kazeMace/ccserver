# drama_engine/components/value_resolver.py
"""
通用值解析器（ValueResolver）。

DSL 中很多位置都需要读取“当前上下文里的值”，例如：
  - actor / candidate / winner
  - data.target / data.targets[0]
  - responses[0].data.target
  - selection_result.winner / selection_result.counts.Player_1
  - {state: GAME.round}

这个模块把路径解析集中到一个地方，避免 effects、conditions、compiler
各自实现一套近似但不完全一致的字符串解析规则。
"""

from __future__ import annotations

import re
from typing import Any

from drama_engine.core.engine import State


_TOKEN_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?")


class ValueResolver:
    """
    从运行上下文中解析 DSL 值路径。

    参数约定：
      state     — 当前世界状态
      responses — 当前 scene 的响应列表
      actor     — 当前执行者，可为空
      candidate — 当前候选目标，可为空
      extra     — 扩展上下文，如 winner / selection_result / item
    """

    def __init__(self, plugin_registry: Any = None):
        """初始化值解析器。"""
        self._plugins = plugin_registry

    def resolve(
        self,
        source: Any,
        state: State | None = None,
        responses: list | None = None,
        actor: str | None = None,
        candidate: str | None = None,
        extra: dict | None = None,
    ) -> Any:
        """
        解析一个 DSL 来源值。

        返回：
          Any — 解析后的实际值；无法解析时返回 None 或原字面量。
        """
        responses = responses or []
        extra = extra or {}
        if state is None:
            state = extra.get("__state")

        if isinstance(source, dict):
            if "ref" in source:
                return self._resolve_ref(source["ref"], state, responses, actor, candidate, extra)
            if "state" in source:
                return self._resolve_state_path(source["state"], state, actor, candidate)
            if "value" in source:
                return self.resolve(source["value"], state, responses, actor, candidate, extra)
            return {
                key: self.resolve(value, state, responses, actor, candidate, extra)
                for key, value in source.items()
            }

        if isinstance(source, list):
            return [
                self.resolve(value, state, responses, actor, candidate, extra)
                for value in source
            ]

        if not isinstance(source, str):
            return source

        if source.startswith("@"):
            return source[1:]

        if source == "actor":
            if actor is not None:
                return actor
            if responses:
                return responses[0].get("actor")
            return None

        if source == "candidate":
            return candidate

        if source == "entity":
            return extra.get("entity")

        if source.startswith("entity."):
            entity = extra.get("entity")
            if entity is None or state is None:
                return None
            return self._resolve_nested_path(
                state.get_attr(str(entity), source.split(".", 1)[1]),
                "",
                state,
            )

        if source == "winner":
            return extra.get("winner")

        if source.startswith("winner."):
            winner = extra.get("winner")
            if winner is None or state is None:
                return None
            return self._resolve_nested_path(
                state.get_attr(str(winner), source.split(".", 1)[1]),
                "",
                state,
            )

        if source == "data":
            return self._first_data(responses)

        if source.startswith("data."):
            return self._resolve_nested_path(self._first_data(responses), source[5:], state)

        if source == "responses":
            return responses

        if source.startswith("responses."):
            return self._resolve_nested_path(responses, source.split(".", 1)[1], state)

        if source == "selection_result":
            return extra.get("selection_result")

        if source.startswith("selection_result."):
            return self._resolve_nested_path(
                extra.get("selection_result"),
                source.split(".", 1)[1],
                state,
            )

        if source == "item":
            return extra.get("item")

        if source.startswith("item."):
            return self._resolve_nested_path(extra.get("item"), source.split(".", 1)[1], state)

        if source == "result":
            return extra.get("result")

        if source.startswith("result."):
            return self._resolve_nested_path(extra.get("result"), source.split(".", 1)[1], state)

        if "." in source and state is not None:
            maybe_state_value = self._resolve_state_path(source, state, actor, candidate)
            if maybe_state_value is not None:
                return maybe_state_value

        return source

    def _resolve_ref(
        self,
        ref: Any,
        state: State | None,
        responses: list,
        actor: str | None,
        candidate: str | None,
        extra: dict,
    ) -> Any:
        """
        严格解析 `{ref: ...}`。

        与普通字符串不同，ref 表示“必须读取上下文/状态路径”。读取不到时返回
        None，不回退为原始字符串，避免把 `GAME.xxx` 当成实体名或字面量。
        """
        if not isinstance(ref, str):
            return self.resolve(ref, state, responses, actor, candidate, extra)

        if ref == "actor":
            if actor is not None:
                return actor
            if responses:
                return responses[0].get("actor")
            return None
        if ref == "candidate":
            return candidate
        if ref == "entity":
            return extra.get("entity")

        if ref == "data":
            return self._first_data(responses)
        if ref.startswith("data."):
            return self._resolve_nested_path(self._first_data(responses), ref[5:], state)

        if ref == "responses":
            return responses
        if ref.startswith("responses."):
            return self._resolve_nested_path(responses, ref.split(".", 1)[1], state)

        if ref == "winner":
            return extra.get("winner")
        if ref.startswith("winner."):
            winner = extra.get("winner")
            if winner is None or state is None:
                return None
            return self._resolve_nested_path(
                state.get_attr(str(winner), ref.split(".", 1)[1]),
                "",
                state,
            )

        if ref == "selection_result":
            return extra.get("selection_result")
        if ref.startswith("selection_result."):
            return self._resolve_nested_path(
                extra.get("selection_result"),
                ref.split(".", 1)[1],
                state,
            )

        if ref == "item":
            return extra.get("item")
        if ref.startswith("item."):
            return self._resolve_nested_path(extra.get("item"), ref.split(".", 1)[1], state)

        if ref == "result":
            return extra.get("result")
        if ref.startswith("result."):
            return self._resolve_nested_path(extra.get("result"), ref.split(".", 1)[1], state)

        if ref == "EVENT":
            return extra.get("event")
        if ref.startswith("EVENT."):
            return self._resolve_nested_path(extra.get("event"), ref.split(".", 1)[1], state)

        if ref == "MESSAGE":
            return extra.get("message") or extra.get("event")
        if ref.startswith("MESSAGE."):
            message = extra.get("message") or extra.get("event")
            return self._resolve_nested_path(message, ref.split(".", 1)[1], state)

        if ":" in ref and self._plugins is not None:
            prefix = ref.split(":", 1)[0]
            if self._plugins.has_value_resolver(prefix):
                return self._plugins.resolve_value(
                    ref,
                    {
                        "state": state,
                        "responses": responses,
                        "actor": actor,
                        "candidate": candidate,
                        "extra": extra,
                    },
                )

        if ref.startswith("entity."):
            entity = extra.get("entity")
            if entity is None or state is None:
                return None
            return self._resolve_state_path(
                f"{entity}.{ref.split('.', 1)[1]}",
                state,
                actor,
                candidate,
            )

        return self._resolve_state_path(ref, state, actor, candidate)

    def resolve_entity(
        self,
        entity: Any,
        state: State,
        responses: list | None = None,
        actor: str | None = None,
        candidate: str | None = None,
        extra: dict | None = None,
    ) -> str:
        """
        解析 entity 字段，并断言结果是实体名字符串。
        """
        value = self.resolve(entity, state, responses, actor, candidate, extra)
        assert isinstance(value, str) and value, f"entity 解析结果必须是非空字符串，收到 {value!r}"
        return value

    def _first_data(self, responses: list) -> dict:
        """读取第一个 response 的 data 字典。"""
        if not responses:
            return {}
        data = responses[0].get("data") or {}
        assert isinstance(data, dict), f"response.data 必须是 dict，收到 {type(data)}"
        return data

    def _resolve_state_path(
        self,
        path: str,
        state: State | None,
        actor: str | None,
        candidate: str | None,
    ) -> Any:
        """解析 entity.attr 形式的 State 路径。"""
        if state is None:
            return None
        if path == "actor":
            return actor
        if path == "candidate":
            return candidate
        if path == "EVENT":
            return None
        if path == "MESSAGE":
            return None
        parts = path.split(".", 1)
        if len(parts) != 2:
            return None
        entity, attr_path = parts
        if entity == "actor":
            entity = actor
        elif entity == "candidate":
            entity = candidate
        if not entity:
            return None
        attr_parts = attr_path.split(".", 1)
        value = state.get_attr(str(entity), attr_parts[0])
        if len(attr_parts) == 1:
            return value
        return self._resolve_nested_path(value, attr_parts[1], state)

    def _resolve_nested_path(self, root: Any, path: str, state: State | None = None) -> Any:
        """
        在 dict/list/object 上读取 a.b[0].c 形式的路径。
        """
        if path == "":
            return root
        value = root
        for raw_token in path.split("."):
            match = _TOKEN_PATTERN.fullmatch(raw_token)
            if not match:
                return None
            key, index_text = match.groups()
            value = self._get_child(value, key, state)
            if index_text is not None:
                if value is None:
                    return None
                index = int(index_text)
                if not isinstance(value, (list, tuple)) or index >= len(value):
                    return None
                value = value[index]
        return value

    def _get_child(self, value: Any, key: str, state: State | None = None) -> Any:
        """读取一个子字段。"""
        if value is None:
            return None
        if isinstance(value, dict):
            return value.get(key)
        if isinstance(value, (list, tuple)) and key.isdigit():
            index = int(key)
            return value[index] if index < len(value) else None
        if isinstance(value, str) and state is not None:
            state_value = state.get_attr(value, key)
            if state_value is not None:
                return state_value
        return getattr(value, key, None)

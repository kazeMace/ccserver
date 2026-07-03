"""Response model factory for interactive_session actions."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field, create_model


class ResponseModelFactory:
    """Create Pydantic collect models from participant_action.response."""

    def build(self, action_kind: str, response_spec: dict[str, Any], target: str = "none") -> Any:
        """Build a collect model, or None for text/no response."""
        assert action_kind, "action_kind 不能为空"
        response_spec = dict(response_spec or {})
        mode = str(response_spec.get("mode") or ("text" if action_kind == "speak" else "structured"))
        schema = response_spec.get("schema")
        if mode in {"none", "text"} or schema in {None, "none", "text"}:
            return None
        if isinstance(schema, dict):
            return self._custom_model(schema)
        if schema == "vote":
            return create_model(
                "InteractiveVoteModel",
                vote=(str, Field(..., description="投票目标")),
                **self._reason_field(response_spec, True),
            )
        if schema == "choose":
            return create_model(
                "InteractiveChooseModel",
                choose=(str, Field(..., description="选择项 id 或目标")),
                **self._reason_field(response_spec, True),
            )
        if schema == "action":
            fields = {"action": (bool, Field(..., description="是否执行行动"))}
            if target == "optional":
                fields["target"] = (Optional[str], Field(None, description="可选目标"))
            elif target == "required":
                fields["target"] = (str, Field(..., description="行动目标"))
            fields.update(self._reason_field(response_spec, False))
            return create_model("InteractiveActionModel", **fields)
        if schema == "target":
            return create_model(
                "InteractiveTargetModel",
                target=(str, Field(..., description="目标")),
                **self._reason_field(response_spec, True),
            )
        if schema == "targets":
            return create_model(
                "InteractiveTargetsModel",
                targets=(list[str], Field(..., description="多个目标")),
                **self._reason_field(response_spec, True),
            )
        if schema == "custom":
            return self._custom_model(response_spec.get("fields") or {})
        return None

    def _reason_field(self, response_spec: dict[str, Any], default: bool) -> dict[str, Any]:
        """Return optional reason field definition."""
        include = bool(response_spec.get("include_reason", default))
        if not include:
            return {}
        return {"reason": (str, Field(..., description="理由"))}

    def _custom_model(self, schema: Any) -> Any:
        """Build a model from dict or list field schema."""
        fields_spec = self._normalize_fields(schema)
        if not fields_spec:
            return None
        type_map = {
            "string": str,
            "str": str,
            "integer": int,
            "int": int,
            "number": float,
            "float": float,
            "boolean": bool,
            "bool": bool,
            "list": list,
            "array": list,
            "dict": dict,
            "object": dict,
        }
        fields: dict[str, Any] = {}
        for field_name, field_spec in fields_spec.items():
            if isinstance(field_spec, str):
                field_type = type_map.get(field_spec, str)
                required = True
                description = field_name
            elif isinstance(field_spec, dict):
                field_type = type_map.get(str(field_spec.get("type") or "string"), str)
                required = bool(field_spec.get("required", True))
                description = str(field_spec.get("description") or field_name)
            else:
                field_type = str
                required = True
                description = field_name
            fields[field_name] = (
                field_type,
                Field(... if required else None, description=description),
            )
        return create_model("InteractiveCustomResponseModel", **fields)

    def _normalize_fields(self, schema: Any) -> dict[str, Any]:
        """Normalize custom fields from either list or dict syntax."""
        if isinstance(schema, dict) and "fields" in schema:
            schema = schema["fields"]
        if isinstance(schema, dict):
            return dict(schema)
        if isinstance(schema, list):
            result = {}
            for item in schema:
                if isinstance(item, dict) and item.get("name"):
                    result[str(item["name"])] = {
                        "type": item.get("type", "string"),
                        "required": item.get("required", True),
                        "description": item.get("description", item["name"]),
                    }
            return result
        return {}

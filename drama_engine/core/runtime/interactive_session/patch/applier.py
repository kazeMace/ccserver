"""Apply flow patches to the live interactive_session script."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from drama_engine.core.dsl.components.value_resolver import parse_state_path
from drama_engine.core.engine import SetAttr
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import InteractiveScript
from drama_engine.core.runtime.interactive_session.patch.materializer import FlowMaterializer


class FlowPatchApplier:
    """Apply validated flow patches to the in-memory script snapshot."""

    def __init__(self) -> None:
        """Initialize the applier."""
        self._compiler = InteractiveSessionCompiler()
        self._materializer = FlowMaterializer()

    def preview(self, ctx: InteractiveExecutionContext, patch: dict[str, Any]) -> InteractiveScript:
        """Compile the materialized script with one candidate patch.

        Args:
            ctx: Runtime execution context.
            patch: Candidate flow patch.

        Returns:
            Compiled script produced by base_raw + current journal + patch.

        Raises:
            ValueError: When the patch cannot be materialized or compiled.
        """
        patch_type = str(patch.get("type") or "")
        if patch_type == "set_state":
            return ctx.script
        if patch_type not in {"add_scene", "add_transition"}:
            raise ValueError(f"未知 flow_patch.type: {patch_type}")
        raw = self._materializer.materialize_raw(
            ctx.script,
            ctx.patch_journal,
            ctx.base_raw,
            extra_flow_patch=patch,
        )
        return self._compiler.compile_doc(raw)

    def apply(self, ctx: InteractiveExecutionContext, patch: dict[str, Any]) -> None:
        """Apply one flow patch to runtime memory.

        Args:
            ctx: Runtime execution context.
            patch: Validated flow_patch dictionary.

        Raises:
            ValueError: When patch type is unsupported.
        """
        patch_type = str(patch.get("type") or "")
        if patch_type == "add_scene":
            self._refresh_materialized_script(ctx)
            return
        if patch_type == "add_transition":
            self._refresh_materialized_script(ctx)
            return
        if patch_type == "set_state":
            self._apply_set_state(ctx, patch)
            return
        raise ValueError(f"未知 flow_patch.type: {patch_type}")

    def _apply_set_state(self, ctx: InteractiveExecutionContext, patch: dict[str, Any]) -> None:
        """Apply a state change flow patch."""
        path = patch.get("path")
        if not path and patch.get("entity") and patch.get("attr"):
            path = str(patch["entity"]) + "." + str(patch["attr"])
        if not path:
            raise ValueError("set_state patch 需要 path 或 entity/attr")
        entity, attr = parse_state_path(str(path))
        if not ctx.state.has_entity(entity):
            ctx.state.register_entity(entity, {})
        value = ctx.value_resolver.resolve(
            patch.get("value"),
            state=ctx.state,
            responses=ctx.last_responses,
            extra=ctx.runtime_extra(),
        )
        ctx.writer.apply(SetAttr(entity, attr, value))

    def _refresh_materialized_script(self, ctx: InteractiveExecutionContext) -> None:
        """Compile base_raw + journal and replace executable runtime fields."""
        raw = self._materializer.materialize_raw(ctx.script, ctx.patch_journal, ctx.base_raw)
        compiled = self._compiler.compile_doc(raw)
        ctx.script.flow = compiled.flow
        ctx.script.scenes = compiled.scenes
        ctx.script.scopes = compiled.scopes
        ctx.script.referee = compiled.referee
        ctx.script.raw = deepcopy(ctx.base_raw or ctx.script.raw)

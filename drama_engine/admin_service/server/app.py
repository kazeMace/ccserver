"""FastAPI app for Drama Engine admin developer console."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from drama_engine.admin_service.server.schemas import (
    GenerateScriptRequest,
    PlaytestCreateRequest,
    PlaytestStepRequest,
    PluginRunRequest,
    PromoteScriptRequest,
    UpdateScriptRequest,
)
from drama_engine.core.dsl.validator import DslValidator
from drama_engine.application.flow_inspector import FlowInspector
from drama_engine.application.playtest import PlaytestManager
from drama_engine.application.script_generation import ScriptGenerationRequest, TemplateScriptGenerationProvider
from drama_engine.application.script_inspector import ScriptInspector
from drama_engine.application.script_plugins import ScriptPluginRegistry
from drama_engine.application.script_repository import ScriptRepository

logger = logging.getLogger(__name__)


MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def create_app(
    repository: ScriptRepository | None = None,
    validator: DslValidator | None = None,
    inspector: ScriptInspector | None = None,
    flow_inspector: FlowInspector | None = None,
    playtests: PlaytestManager | None = None,
    plugins: ScriptPluginRegistry | None = None,
) -> FastAPI:
    """Create admin FastAPI app.

    管理端独立于普通游戏 service。它只管理 DSL 开发、检查、可视化和试玩。
    """
    app = FastAPI(title="Drama Engine Admin Console", version="0.1.0")
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    frontend_dist_dir = frontend_dir / "dist"
    static_dir = frontend_dist_dir if frontend_dist_dir.exists() else frontend_dir
    app.state.repository = repository or ScriptRepository()
    app.state.validator = validator or DslValidator()
    app.state.inspector = inspector or ScriptInspector(app.state.validator)
    app.state.flow_inspector = flow_inspector or FlowInspector()
    app.state.playtests = playtests or PlaytestManager()
    app.state.plugins = plugins or ScriptPluginRegistry()
    app.state.generation_provider = TemplateScriptGenerationProvider()
    app.state.frontend_dir = frontend_dir

    if static_dir.exists():
        app.mount("/admin/frontend", StaticFiles(directory=str(static_dir)), name="drama_admin_frontend")

    @app.get("/")
    async def root() -> FileResponse:
        index_path = (frontend_dist_dir / "index.html") if frontend_dist_dir.exists() else (frontend_dir / "index.html")
        return FileResponse(str(index_path))

    @app.get("/admin")
    async def admin_index() -> FileResponse:
        index_path = (frontend_dist_dir / "index.html") if frontend_dist_dir.exists() else (frontend_dir / "index.html")
        return FileResponse(str(index_path))

    @app.get("/admin/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "drama_engine_admin"}

    @app.get("/admin/api/scripts")
    async def list_scripts() -> dict[str, Any]:
        records = app.state.repository.list_scripts(include_builtin=True)
        return {"scripts": [record.to_dict() for record in records]}

    @app.post("/admin/api/scripts/upload")
    async def upload_script(
        file: UploadFile = File(...),
        name: str = Form(""),
        description: str = Form(""),
    ) -> dict[str, Any]:
        content_bytes = await file.read()
        if len(content_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="上传文件过大，最大 2MB。")
        filename = file.filename or "uploaded.yaml"
        if not filename.endswith((".yaml", ".yml")):
            raise HTTPException(status_code=400, detail="只支持 .yaml/.yml 文件。")
        content = content_bytes.decode("utf-8")
        script_name = name.strip() or Path(filename).stem
        record = app.state.repository.create_script_from_text(script_name, content, description=description)
        report = app.state.validator.validate_text(content, source_name=record.path)
        app.state.repository.update_validation_summary(record.script_id, report.summary())
        record = app.state.repository.get_script(record.script_id)
        return {"script": record.to_dict(), "validation": report.to_dict()}

    @app.get("/admin/api/scripts/{script_id}")
    async def get_script(script_id: str) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        content = Path(record.path).read_text(encoding="utf-8")
        return {"script": record.to_dict(), "content": content}

    @app.put("/admin/api/scripts/{script_id}")
    async def update_script(script_id: str, request: UpdateScriptRequest) -> dict[str, Any]:
        try:
            record = app.state.repository.update_script_text(script_id, request.content)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"script": record.to_dict(), "saved": True}

    @app.delete("/admin/api/scripts/{script_id}")
    async def delete_script(script_id: str) -> dict[str, Any]:
        try:
            app.state.repository.delete_script(script_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": True, "script_id": script_id}

    @app.post("/admin/api/scripts/{script_id}/validate")
    async def validate_script(script_id: str) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        report = app.state.validator.validate_file(record.path)
        updated = app.state.repository.update_validation_summary(script_id, report.summary())
        return {"script": updated.to_dict(), "validation": report.to_dict()}

    @app.post("/admin/api/scripts/{script_id}/compile-check")
    async def compile_check(script_id: str) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        report = app.state.validator.validate_file(record.path)
        summary = report.summary()
        return {"script_id": script_id, "compiled": summary["fatal"] == 0 and summary["error"] == 0, "validation": report.to_dict()}

    @app.get("/admin/api/scripts/{script_id}/inspect")
    async def inspect_script(script_id: str) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        return {"script": record.to_dict(), "inspection": app.state.inspector.inspect_file(record.path)}

    @app.get("/admin/api/scripts/{script_id}/flow")
    async def flow_all(script_id: str) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        return {"script": record.to_dict(), "flow": app.state.flow_inspector.inspect_file(record.path)}

    @app.get("/admin/api/scripts/{script_id}/flow/sequence")
    async def flow_sequence(script_id: str) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        flow = app.state.flow_inspector.inspect_file(record.path)
        return {"script_id": script_id, "sequence": flow["sequence"]}

    @app.get("/admin/api/scripts/{script_id}/flow/state-machine")
    async def flow_state_machine(script_id: str) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        flow = app.state.flow_inspector.inspect_file(record.path)
        return {"script_id": script_id, "state_machine": flow["state_machine"]}

    @app.get("/admin/api/scripts/{script_id}/flow/tree")
    async def flow_tree(script_id: str) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        flow = app.state.flow_inspector.inspect_file(record.path)
        return {"script_id": script_id, "tree": flow["tree"]}

    @app.post("/admin/api/scripts/{script_id}/promote")
    async def promote_script(script_id: str, request: PromoteScriptRequest) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        report = app.state.validator.validate_file(record.path)
        app.state.repository.update_validation_summary(script_id, report.summary())
        summary = report.summary()
        if summary["fatal"] or summary["error"]:
            raise HTTPException(status_code=400, detail={"message": "存在 fatal/error，不能发布。", "validation": report.to_dict()})
        try:
            promoted = app.state.repository.promote(script_id, force=request.force)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"script": promoted.to_dict(), "validation": report.to_dict()}

    @app.post("/admin/api/scripts/{script_id}/playtests")
    async def create_playtest(script_id: str, request: PlaytestCreateRequest) -> dict[str, Any]:
        record = _get_record_or_404(app, script_id)
        session = await app.state.playtests.create(
            script_id=script_id,
            script_path=record.path,
            mode=request.mode,
            human_player_count=request.human_player_count,
            step_mode=request.step_mode,
        )
        return {"playtest": session.to_dict(), "admin_url": f"/admin/playtests/{session.playtest_id}"}

    @app.get("/admin/api/playtests")
    async def list_playtests() -> dict[str, Any]:
        return {"playtests": [session.to_dict() for session in app.state.playtests.list()]}

    @app.get("/admin/api/playtests/{playtest_id}")
    async def get_playtest(playtest_id: str) -> dict[str, Any]:
        try:
            session = app.state.playtests.get(playtest_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"playtest": session.to_dict()}

    @app.post("/admin/api/playtests/{playtest_id}/step")
    async def step_playtest(playtest_id: str, request: PlaytestStepRequest) -> dict[str, Any]:
        try:
            session = await app.state.playtests.step(playtest_id, count=request.count)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"playtest": session.to_dict()}

    @app.post("/admin/api/playtests/{playtest_id}/assign")
    async def assign_playtest(playtest_id: str) -> dict[str, Any]:
        try:
            session = await app.state.playtests.assign(playtest_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"playtest": session.to_dict()}

    @app.post("/admin/api/playtests/{playtest_id}/start")
    async def start_playtest(playtest_id: str) -> dict[str, Any]:
        try:
            session = await app.state.playtests.start(playtest_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"playtest": session.to_dict()}

    @app.get("/admin/api/playtests/{playtest_id}/runtime")
    async def playtest_runtime(playtest_id: str) -> dict[str, Any]:
        try:
            runtime = await app.state.playtests.runtime_summary(playtest_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"runtime": runtime}

    @app.post("/admin/api/playtests/{playtest_id}/reset")
    async def reset_playtest(playtest_id: str) -> dict[str, Any]:
        try:
            session = await app.state.playtests.reset(playtest_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"playtest": session.to_dict()}

    @app.get("/admin/api/plugins")
    async def list_plugins() -> dict[str, Any]:
        return {"plugins": app.state.plugins.list_plugins()}

    @app.post("/admin/api/plugins/{plugin_id}/run")
    async def run_plugin(plugin_id: str, request: PluginRunRequest) -> dict[str, Any]:
        try:
            return app.state.plugins.run_plugin(plugin_id, request.input)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/admin/api/scripts/generate")
    async def generate_script(request: GenerateScriptRequest) -> dict[str, Any]:
        result = app.state.generation_provider.generate_script(ScriptGenerationRequest(
            prompt=request.prompt,
            materials=request.materials,
            base_script_id=request.base_script_id,
            options=request.options,
        ))
        record = app.state.repository.create_script_from_text(result.name, result.content, description="Generated from natural language prompt")
        report = app.state.validator.validate_text(result.content, source_name=record.path)
        app.state.repository.update_validation_summary(record.script_id, report.summary())
        record = app.state.repository.get_script(record.script_id)
        return {"script": record.to_dict(), "notes": result.notes, "validation": report.to_dict()}

    @app.get("/admin/{path:path}")
    async def admin_spa(path: str) -> FileResponse:
        del path
        index_path = (frontend_dist_dir / "index.html") if frontend_dist_dir.exists() else (frontend_dir / "index.html")
        return FileResponse(str(index_path))


    return app


def _get_record_or_404(app: FastAPI, script_id: str):
    """Fetch script record or raise HTTP 404."""
    try:
        return app.state.repository.get_script(script_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

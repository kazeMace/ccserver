"""Party Game DSL command line tools.

This module provides the authoring/publish commands described in the party
game DSL plans:

  python -m drama_engine.cli validate game.yaml
  python -m drama_engine.cli lint game.yaml
  python -m drama_engine.cli simulate game.yaml
  python -m drama_engine.cli preview game.yaml
  python -m drama_engine.cli package game.yaml --output dist/game.zip
  python -m drama_engine.cli author "做一个四人卡牌游戏" --output game.yaml

The commands intentionally reuse the existing compiler, validator and
inspector. They are small orchestration entry points rather than a second DSL
implementation.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from drama_engine.core.dsl.compiler import YamlCompiler
from drama_engine.core.dsl.validator import DslValidator, ValidationReport
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler
from drama_engine.application.script_inspector import ScriptInspector
from drama_engine.run_script import parse_cli_params


class CliError(RuntimeError):
    """User-facing CLI error."""


def build_parser() -> argparse.ArgumentParser:
    """Build the Party Game DSL CLI parser."""
    parser = argparse.ArgumentParser(description="Drama Engine Party Game DSL tools")
    subcommands = parser.add_subparsers(dest="command", required=True)

    for command in ("validate", "lint", "simulate", "preview"):
        subparser = subcommands.add_parser(command, help=f"Run {command} on one DSL script")
        subparser.add_argument("script", help="YAML script path")
        subparser.add_argument("--param", action="append", default=[], dest="params", metavar="KEY=VALUE", help="Override script parameter; repeatable")
        subparser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
        subparser.add_argument("--output", default=None, help="Write report JSON to this path")

    package_parser = subcommands.add_parser("package", help="Create a publishable game package")
    package_parser.add_argument("script", help="YAML script path")
    package_parser.add_argument("--param", action="append", default=[], dest="params", metavar="KEY=VALUE", help="Override script parameter; repeatable")
    package_parser.add_argument("--output", required=True, help="Output .zip package path")
    package_parser.add_argument("--json", action="store_true", help="Print package report as JSON")

    author_parser = subcommands.add_parser("author", help="Create a DSL script from a natural-language idea")
    author_parser.add_argument("idea", help="Natural-language game idea")
    author_parser.add_argument("--output", required=True, help="Generated YAML output path")
    author_parser.add_argument("--package-output", default=None, help="Optional generated .zip package path")
    author_parser.add_argument("--answer", action="append", default=[], dest="answers", metavar="KEY=VALUE", help="Authoring answer/override; repeatable")
    author_parser.add_argument("--checklist", action="store_true", help="Only print authoring questions/defaults; do not write YAML")
    author_parser.add_argument("--json", action="store_true", help="Print authoring result as JSON")
    return parser


def run_cli(args: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    parsed = parser.parse_args(args)
    params = parse_cli_params(getattr(parsed, "params", []) or [])
    try:
        with contextlib.redirect_stdout(sys.stderr):
            report, passed = _run_command(parsed, params)
        _emit_report(report, parsed)
        return 0 if passed else 1
    except CliError as exc:
        print(f"[drama] {exc}", file=sys.stderr)
        return 2


def _run_command(parsed: argparse.Namespace, params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Execute one command while caller controls stdout handling."""
    if parsed.command == "validate":
        report = validate_script(parsed.script, params=params)
        return report.to_dict(), report.passed()
    if parsed.command == "lint":
        report = lint_script(parsed.script, params=params)
        return report.to_dict(), report.passed()
    if parsed.command == "simulate":
        report = simulate_script(parsed.script, params=params)
        return report, bool(report["passed"])
    if parsed.command == "preview":
        report = preview_script(parsed.script, params=params)
        return report, bool(report["issues"]["passed"])
    if parsed.command == "package":
        report = package_script(parsed.script, parsed.output, params=params)
        return report, bool(report["passed"])
    if parsed.command == "author":
        report = author_script(
            idea=parsed.idea,
            output_path=parsed.output,
            answers=parse_cli_params(getattr(parsed, "answers", []) or []),
            package_path=parsed.package_output,
            checklist_only=bool(parsed.checklist),
        )
        if parsed.checklist:
            return report, True
        validation_passed = bool(report["validation"]["passed"])
        simulation_passed = bool(report["simulation"]["passed"])
        preview_passed = bool(report["preview"]["issues"]["passed"])
        package_report = report.get("package")
        package_passed = True if package_report is None else bool(package_report["passed"])
        return report, bool(validation_passed and simulation_passed and preview_passed and package_passed)
    raise CliError(f"未知命令: {parsed.command}")


def author_script(
    idea: str,
    output_path: str | Path,
    answers: dict[str, Any] | None = None,
    package_path: str | Path | None = None,
    checklist_only: bool = False,
) -> dict[str, Any]:
    """Create one script from a natural-language game idea."""
    from drama_engine.application.authoring import PartyGameAuthor

    author = PartyGameAuthor()
    if checklist_only:
        report = author.checklist(idea)
        report["kind"] = "author_checklist"
        return report
    result = author.create(
        idea=idea,
        output_path=output_path,
        answers=answers or {},
        package_path=package_path,
    )
    data = result.to_dict()
    data["kind"] = "author"
    return data


def validate_script(script_path: str | Path, params: dict[str, Any] | None = None) -> ValidationReport:
    """Validate one script and return the validation report."""
    return DslValidator().validate_file(script_path, params=params)


def lint_script(script_path: str | Path, params: dict[str, Any] | None = None) -> ValidationReport:
    """Lint one script.

    The first version uses the static DSL validator as the lint engine. It
    already reports structural errors, stale scene fields, reference problems,
    state read/write risks and runtime warnings.
    """
    return DslValidator().validate_file(script_path, params=params)


def preview_script(script_path: str | Path, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a preview document for one script."""
    path = _existing_script_path(script_path)
    inspector = ScriptInspector()
    inspection = inspector.inspect_file(path, params=params)
    return {
        "kind": "preview",
        "script_path": str(path),
        "generated_at": _now_iso(),
        "overview": inspection.get("overview", {}),
        "roles": inspection.get("roles", []),
        "scopes": inspection.get("scopes", []),
        "scenes": inspection.get("scenes", []),
        "states": inspection.get("states", []),
        "effects": inspection.get("effects", []),
        "issues": inspection.get("issues", {}),
    }


def simulate_script(script_path: str | Path, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a deterministic static simulation pass for one script.

    This is not a full game playthrough yet. It proves parse -> validate ->
    compile, checks runtime support, and emits a deterministic flow/action
    summary that authoring tools can use before full dry-run playtesting.
    """
    path = _existing_script_path(script_path)
    validation = validate_script(path, params=params)
    result: dict[str, Any] = {
        "kind": "simulation",
        "script_path": str(path),
        "generated_at": _now_iso(),
        "validation": validation.to_dict(),
        "passed": False,
        "runtime_type": None,
        "scene_count": 0,
        "action_scene_count": 0,
        "narration_scene_count": 0,
        "warnings": [],
    }
    if not validation.passed():
        result["warnings"].append("validation failed; compile simulation skipped")
        return result

    compile_params = dict(params or {})
    compile_params["dry_run"] = True
    runtime_type = _runtime_type_from_file(path)
    if runtime_type == "interactive_session":
        script = InteractiveSessionCompiler().compile(str(path), params=compile_params)
        scenes = list(script.scenes.values())
        result["runtime_type"] = runtime_type
        result["scene_count"] = len(scenes)
        result["action_scene_count"] = sum(
            1 for scene in scenes
            if scene.participant_action.kind not in {"none", "narration"}
            or scene.controller_action.enabled
        )
        result["narration_scene_count"] = sum(
            1 for scene in scenes
            if scene.controller_action.kind == "narration"
            or scene.participant_action.kind == "narration"
        )
        result["passed"] = True
        return result

    compiler = YamlCompiler()
    script = compiler.compile(str(path), params=compile_params)
    scenes = list(getattr(script.flow, "scenes", []) or [])
    runtime_type = getattr(getattr(script, "runtime", None), "type", runtime_type)
    result["runtime_type"] = runtime_type
    result["scene_count"] = len(scenes)
    result["action_scene_count"] = sum(1 for scene in scenes if scene.response_model is not None)
    result["narration_scene_count"] = sum(
        1 for scene in scenes
        if scene.__class__.__name__ == "Scene"
        and scene.dialogue_policy.__class__.__name__ == "Narration"
    )
    if runtime_type not in {"game_session", "group_chat", "dynamic_story", "interactive_session"}:
        result["warnings"].append(f"runtime {runtime_type} is recognized but not supported by simulation")
        return result
    result["passed"] = True
    return result


def _runtime_type_from_file(path: Path) -> str:
    """Read runtime.type from a YAML file without full compilation."""
    import yaml

    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    runtime = doc.get("runtime")
    if isinstance(runtime, str):
        return runtime
    if isinstance(runtime, dict):
        return str(runtime.get("type") or "game_session")
    return "game_session"


def package_script(
    script_path: str | Path,
    output_path: str | Path,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a zip package with script, validation, simulation and preview data."""
    path = _existing_script_path(script_path)
    output = Path(output_path).expanduser().resolve()
    assert output.suffix == ".zip", "package output 必须是 .zip 文件"
    output.parent.mkdir(parents=True, exist_ok=True)

    validation = validate_script(path, params=params).to_dict()
    simulation = simulate_script(path, params=params)
    preview = preview_script(path, params=params)
    passed = bool(validation["passed"] and simulation["passed"] and preview["issues"]["passed"])
    manifest = {
        "kind": "party_game_package",
        "schema_version": "0.1",
        "script_file": path.name,
        "created_at": _now_iso(),
        "params": dict(params or {}),
        "validation_passed": validation["passed"],
        "simulation_passed": simulation["passed"],
        "preview_included": True,
    }

    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(path, arcname=path.name)
        archive.writestr("manifest.json", _to_json(manifest))
        archive.writestr("validation_report.json", _to_json(validation))
        archive.writestr("simulation_report.json", _to_json(simulation))
        archive.writestr("preview.json", _to_json(preview))
        archive.writestr("changelog.md", "# Changelog\n\n- Initial package generated by drama CLI.\n")

    return {
        "kind": "package",
        "passed": passed,
        "output": str(output),
        "manifest": manifest,
        "files": [
            path.name,
            "manifest.json",
            "validation_report.json",
            "simulation_report.json",
            "preview.json",
            "changelog.md",
        ],
    }


def _existing_script_path(script_path: str | Path) -> Path:
    """Resolve and assert an existing script path."""
    path = Path(script_path).expanduser().resolve()
    if not path.exists():
        raise CliError(f"剧本文件不存在: {path}")
    if not path.is_file():
        raise CliError(f"剧本路径不是文件: {path}")
    return path


def _emit_report(report: dict[str, Any], parsed: argparse.Namespace) -> None:
    """Print and optionally write one report."""
    if getattr(parsed, "output", None) and parsed.command not in {"package", "author"}:
        output = Path(parsed.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_to_json(report), encoding="utf-8")
    if getattr(parsed, "json", False):
        print(_to_json(report))
    else:
        print(_human_summary(report))


def _human_summary(report: dict[str, Any]) -> str:
    """Format one report for humans."""
    kind = report.get("kind", "validation")
    if "summary" in report:
        summary = report["summary"]
        return (
            f"{kind}: passed={report.get('passed')} "
            f"fatal={summary.get('fatal', 0)} error={summary.get('error', 0)} "
            f"warning={summary.get('warning', 0)} info={summary.get('info', 0)}"
        )
    if kind == "preview":
        overview = report.get("overview", {})
        return (
            f"preview: title={overview.get('title')} "
            f"roles={len(report.get('roles', []))} scenes={len(report.get('scenes', []))} "
            f"passed={report.get('issues', {}).get('passed')}"
        )
    if kind == "simulation":
        return (
            f"simulation: passed={report.get('passed')} runtime={report.get('runtime_type')} "
            f"scenes={report.get('scene_count')} actions={report.get('action_scene_count')}"
        )
    if kind == "package":
        return f"package: passed={report.get('passed')} output={report.get('output')}"
    if kind == "author":
        return (
            f"author: game_type={report.get('game_type')} runtime={report.get('runtime_type')} "
            f"output={report.get('output_path')} validation={report.get('validation', {}).get('passed')}"
        )
    if kind == "author_checklist":
        return (
            f"author_checklist: game_type={report.get('game_type')} runtime={report.get('runtime_type')} "
            f"questions={len(report.get('required_questions', []))}"
        )
    return _to_json(report)


def _to_json(data: Any) -> str:
    """Serialize JSON with stable formatting."""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _now_iso() -> str:
    """Return current timestamp for generated reports."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def main() -> None:
    """Console entry point."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

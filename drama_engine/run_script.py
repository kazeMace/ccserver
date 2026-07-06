"""Local single-script runner for Drama Engine.

这个入口用于在本地演示或运行一个 YAML script。默认模式会启动现有 Web
service dashboard，并预先创建一局只绑定当前 script 的本地 session；如果需要
也可以显式传入 --headless，在终端里直接跑完流程。

The runner intentionally reuses SessionRegistry + fixed-flow runners, so YAML
compilation, role assignment, Director execution, event storage, dry-run, and
real-LLM behavior stay aligned with the web server runtime.

Usage examples:
  conda run --no-capture-output -n ccserver python drama_engine/run_script.py drama_engine/scripts/fixed_flow/deduction/avalon.yaml
  conda run --no-capture-output -n ccserver python drama_engine/run_script.py drama_engine/scripts/fixed_flow/deduction/avalon.yaml --no-open
  conda run -n ccserver python drama_engine/run_script.py drama_engine/scripts/fixed_flow/deduction/avalon.yaml --headless --dry-run
  conda run --no-capture-output -n ccserver python drama_engine/run_script.py --preset drama_engine/scripts/presets/deduction/werewolf/werewolf_v1_12p_guard.preset.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from dotenv import load_dotenv

# 允许用户从任意 cwd 直接执行本文件。
# Allow running this file directly from any cwd.
_DRAMA_ROOT = Path(__file__).resolve().parent
_PROJECT_ROOT = _DRAMA_ROOT.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler
from drama_engine.core.session.registry import SessionRegistry
from drama_engine.application.script_library import SCRIPT_LIBRARY_ROOT
from drama_engine.service.server.app import create_app

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalScriptConfig:
    """本地单脚本运行配置。

    Attributes:
      script_path: 已解析为绝对路径的 YAML script。
      params: 传给 compiler/runtime 的参数。
      seat_ids: 当前脚本推导出的玩家 seat 列表。
      human_seat_ids: 需要真人控制的 seat 集合。
      dry_run: True 表示使用 MockActor，不调用真实 LLM。
    """

    script_path: Path
    params: dict[str, Any]
    seat_ids: list[str]
    human_seat_ids: set[str]
    dry_run: bool


class LocalRunError(RuntimeError):
    """本地运行入口的可读错误。"""


def load_preset(preset_path: str) -> dict[str, Any]:
    """加载 .preset.yaml 文件。

    参数：
      preset_path: 预设文件路径。

    返回：
      预设 dict，至少包含 script；params 缺省为空 dict。

    异常：
      AssertionError: 文件内容不是 dict 或缺少 script。
    """
    path = Path(preset_path).expanduser().resolve()
    with path.open(encoding="utf-8") as file_obj:
        preset = yaml.safe_load(file_obj) or {}
    assert isinstance(preset, dict), f"预设文件必须是 dict: {path}"
    assert preset.get("script"), f"预设文件缺少 script 字段: {path}"
    preset.setdefault("params", {})
    preset["__preset_path"] = str(path)
    return preset


def parse_cli_params(param_items: list[str]) -> dict[str, Any]:
    """把 --param KEY=VALUE 列表解析为 dict。

    支持 int/float/bool/list 的简单类型推断。
    """
    result: dict[str, Any] = {}
    for item in param_items:
        assert "=" in item, f"--param 必须是 KEY=VALUE，收到: {item!r}"
        key, _, raw_value = item.partition("=")
        key = key.strip()
        assert key, f"--param key 不能为空: {item!r}"
        result[key] = _coerce_cli_value(raw_value.strip())
    return result


def _coerce_cli_value(raw_value: str) -> Any:
    """把 CLI 字符串值转换为简单 Python 值。"""
    lowered = raw_value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"none", "null"}:
        return None
    if "," in raw_value:
        return [_coerce_cli_value(part.strip()) for part in raw_value.split(",") if part.strip()]
    try:
        return int(raw_value)
    except ValueError:
        pass
    try:
        return float(raw_value)
    except ValueError:
        pass
    return raw_value


def merge_params(preset_params: dict[str, Any], cli_params: dict[str, Any]) -> dict[str, Any]:
    """合并运行参数，CLI 参数优先。"""
    merged = dict(preset_params or {})
    merged.update(cli_params or {})
    return merged


def resolve_script_path(script_ref: str, preset_path: str | None = None) -> Path:
    """解析 script 路径。

    解析顺序：
      1. 绝对路径直接使用。
      2. 相对当前 cwd。
      3. 相对 preset 所在目录。
      4. 相对 preset 上一级目录。
      5. 相对 drama_engine/scripts。
      6. 相对新版 drama_engine 目录。
      7. 相对项目根目录。
    """
    assert script_ref, "script 路径不能为空"
    raw_path = Path(script_ref).expanduser()
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(Path.cwd() / raw_path)
        if preset_path:
            preset_dir = Path(preset_path).resolve().parent
            candidates.append(preset_dir / raw_path)
            candidates.append(preset_dir.parent / raw_path)
        candidates.append(SCRIPT_LIBRARY_ROOT / raw_path)
        candidates.append(_DRAMA_ROOT / raw_path)
        candidates.append(_PROJECT_ROOT / raw_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    checked = "\n  - ".join(str(path) for path in candidates)
    raise LocalRunError(f"剧本文件不存在: {script_ref}\n已检查:\n  - {checked}")


def _resolve_interactive_player_names(script: Any) -> list[str]:
    """从编译后的 InteractiveScript 解析 seat 名称。

    解析顺序：
      1. players.ids 显式列表。
      2. players.count 生成 Player_1..Player_N。
      3. 从各 scene 的静态 participants 汇总。
      4. 兜底一个 Player_1。
    与 InteractiveSessionRunner._resolve_player_names 保持同一套规则。
    """
    players = getattr(script, "players", None) or {}
    ids = players.get("ids") if isinstance(players, dict) else None
    if isinstance(ids, list) and ids:
        return [str(item) for item in ids]
    count = int(players.get("count") or 0) if isinstance(players, dict) else 0
    if count > 0:
        return [f"Player_{index}" for index in range(1, count + 1)]
    names: set[str] = set()
    for scene in getattr(script, "scenes", {}).values():
        spec = scene.participants.spec
        if isinstance(spec, dict) and isinstance(spec.get("static"), list):
            names.update(str(item) for item in spec["static"])
        elif isinstance(spec, list):
            names.update(str(item) for item in spec)
    return sorted(names) or ["Player_1"]


def resolve_human_players(params: dict[str, Any]) -> set[str]:
    """从参数解析真人 seat 集合。

    本地 dashboard 模式支持真人通过 player link 提交；headless 模式通常不传
    human_players，因为终端不会提供网页输入入口。
    """
    raw_value = params.get("human_players", None)
    if raw_value in (None, ""):
        return set()
    if isinstance(raw_value, str):
        return {item.strip() for item in raw_value.split(",") if item.strip()}
    if isinstance(raw_value, (list, tuple, set)):
        return {str(item).strip() for item in raw_value if str(item).strip()}
    raise LocalRunError(f"human_players 必须是 str/list/tuple/set，收到: {type(raw_value)}")


def build_parser() -> argparse.ArgumentParser:
    """构建本地单脚本运行命令行。"""
    parser = argparse.ArgumentParser(description="Drama Engine 本地单脚本演示器")
    parser.add_argument("script", nargs="?", default=None, help="YAML script 路径，与 --preset 二选一")
    parser.add_argument("--preset", default=None, help=".preset.yaml 路径；script 未提供时使用 preset.script")
    parser.add_argument("--param", action="append", default=[], dest="params", metavar="KEY=VALUE", help="覆盖脚本参数，可重复")
    parser.add_argument("--dry-run", action="store_true", help="使用 MockActor，不调用 LLM；适合快速验证流程")
    parser.add_argument("--real-llm", action="store_true", help="使用真实 LLM actor；会读取 ANTHROPIC_BASE_URL / API key 等环境变量")
    parser.add_argument("--human-players", default=None, help="真人 seat 列表，如 Player_1,Player_2；默认全 AI/mock")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="日志级别")

    dashboard_group = parser.add_argument_group("dashboard 演示模式（默认）")
    dashboard_group.add_argument("--headless", action="store_true", help="不启动 dashboard，直接在终端跑完单个 script")
    dashboard_group.add_argument("--host", default="127.0.0.1", help="dashboard 监听 host，默认 127.0.0.1")
    dashboard_group.add_argument("--port", type=int, default=8766, help="dashboard 监听端口，默认 8766")
    dashboard_group.add_argument("--no-open", action="store_true", help="启动 dashboard 但不自动打开浏览器")
    dashboard_group.add_argument("--auto-assign", action="store_true", help="dashboard 启动前先随机分配角色，打开后可直接点开始游戏")

    headless_group = parser.add_argument_group("headless 终端运行模式")
    headless_group.add_argument("--step-mode", action="store_true", help="以单步模式启动；配合 --auto-step 自动放行")
    headless_group.add_argument("--auto-step", action="store_true", help="step-mode 下自动循环放行，便于 CLI 直接跑完")
    headless_group.add_argument("--step-delay", type=float, default=0.05, help="auto-step 每次放行后的等待秒数")
    headless_group.add_argument("--max-auto-steps", type=int, default=2000, help="auto-step 最大放行次数，防止无限循环")
    headless_group.add_argument("--print-events", choices=["none", "public", "host", "all"], default="host", help="headless 结束后打印哪些事件回放")
    headless_group.add_argument("--event-limit", type=int, default=80, help="headless 事件打印数量上限，<=0 表示不限")
    return parser


def build_local_script_config(parsed: argparse.Namespace, parser: argparse.ArgumentParser) -> LocalScriptConfig:
    """从 CLI 参数构造本地单脚本配置。

    该函数只负责解析、校验和编译预检查，不创建 session，也不启动服务。
    """
    script_ref = parsed.script
    preset_params: dict[str, Any] = {}
    preset_path: str | None = None
    if parsed.preset:
        preset = load_preset(parsed.preset)
        preset_path = preset["__preset_path"]
        preset_params = dict(preset.get("params") or {})
        if script_ref is None:
            script_ref = str(preset["script"])
    if script_ref is None:
        parser.error("需要提供 script 或 --preset")

    cli_params = parse_cli_params(parsed.params)
    if parsed.human_players is not None:
        cli_params["human_players"] = parsed.human_players
    params = merge_params(preset_params, cli_params)
    params["dry_run"] = bool(parsed.dry_run or not parsed.real_llm)
    params["use_runner"] = True

    script_path = resolve_script_path(script_ref, preset_path=preset_path)
    compiler = InteractiveSessionCompiler()
    errors = compiler.validate_file(str(script_path), params)
    if errors:
        message_lines = ["YAML 校验失败:"]
        for error in errors:
            message_lines.append(f"  - {error}")
        raise LocalRunError("\n".join(message_lines))

    script = compiler.compile(str(script_path), params=params)
    seat_ids = _resolve_interactive_player_names(script)
    human_seat_ids = resolve_human_players(params)
    _assert_human_seats_exist(human_seat_ids, seat_ids)
    return LocalScriptConfig(
        script_path=script_path,
        params=params,
        seat_ids=seat_ids,
        human_seat_ids=human_seat_ids,
        dry_run=bool(params["dry_run"]),
    )


async def create_local_runtime(registry: SessionRegistry, config: LocalScriptConfig) -> Any:
    """在指定 registry 中创建一局本地单脚本 session。

    返回：
      GameRuntime 风格对象，供 dashboard 或 headless 后续控制。
    """
    assert config.seat_ids, "seat_ids 不能为空"
    runtime = await registry.create_session(
        game_id=f"local:{config.script_path.stem}",
        script_path=str(config.script_path),
        seat_ids=config.seat_ids,
        human_seat_ids=config.human_seat_ids,
        params=config.params,
        metadata={"local_single_script": True, "script_path": str(config.script_path)},
    )
    return runtime


async def run_local_script(args: list[str] | None = None) -> int:
    """解析参数并运行本地单脚本入口。

    默认进入 dashboard 演示模式；传入 --headless 时才直接在终端跑完整局。
    """
    parser = build_parser()
    parsed = parser.parse_args(args)
    _configure_logging(parsed.log_level)
    # 与 service/server/__main__.py 保持一致：shell env 优先，.env 只兜底。
    # Keep parity with the web service entrypoint: shell env wins over .env.
    load_dotenv(override=False)

    try:
        config = build_local_script_config(parsed, parser)
    except LocalRunError as exc:
        print(f"[drama_engine] {exc}")
        return 1

    _print_config(config)
    if parsed.headless:
        return await run_headless(config, parsed)
    return await run_dashboard(config, parsed)


async def run_headless(config: LocalScriptConfig, parsed: argparse.Namespace) -> int:
    """不启动 Web dashboard，直接在终端运行一局。"""
    registry = SessionRegistry(store=None, load_existing=False)
    runtime = await create_local_runtime(registry, config)

    if parsed.step_mode:
        await registry.set_step_mode(runtime.session.session_id, True)

    await registry.assign_session(runtime.session.session_id)
    _print_assignment(runtime)
    await registry.start_session(runtime.session.session_id)

    if parsed.step_mode and parsed.auto_step:
        await _auto_step_until_done(
            registry=registry,
            session_id=runtime.session.session_id,
            task=runtime.director_task,
            max_steps=parsed.max_auto_steps,
            delay=parsed.step_delay,
        )

    if runtime.director_task is not None:
        await runtime.director_task

    _print_result(runtime, event_mode=parsed.print_events, event_limit=parsed.event_limit)
    return 0 if runtime.session.status == "ended" else 2


async def run_dashboard(config: LocalScriptConfig, parsed: argparse.Namespace) -> int:
    """启动本地 dashboard，并让它只演示当前一个 script。"""
    registry = SessionRegistry(store=None, load_existing=False)
    runtime = await create_local_runtime(registry, config)
    if parsed.auto_assign:
        await registry.assign_session(runtime.session.session_id)
        _print_assignment(runtime)

    session_id = runtime.session.session_id
    base_url = f"http://{parsed.host}:{parsed.port}"
    host_url = f"{base_url}/host/sessions/{session_id}"
    viewer_url = f"{base_url}/viewer/sessions/{session_id}"

    app = create_app(registry=registry)
    server_config = uvicorn.Config(
        app,
        host=parsed.host,
        port=parsed.port,
        log_level=parsed.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    server = uvicorn.Server(server_config)

    print("[drama_engine] dashboard mode: open host dashboard to demonstrate this script", flush=True)
    print(f"[drama_engine] host dashboard: {host_url}", flush=True)
    print(f"[drama_engine] public viewer:   {viewer_url}", flush=True)
    print("[drama_engine] controls: 在 dashboard 点击“随机分配角色”→“开始游戏”；Ctrl+C 停止服务", flush=True)
    if parsed.auto_assign:
        print("[drama_engine] auto-assign: 已预先分配角色，打开后可直接点击“开始游戏”", flush=True)

    if not parsed.no_open:
        asyncio.create_task(_open_browser_after_server_start(host_url))

    await server.serve()
    return 0


async def _open_browser_after_server_start(url: str) -> None:
    """等待 dashboard 监听启动后再打开浏览器。

    参数：
      url: 需要打开的 host dashboard URL。

    说明：
      uvicorn.Server.serve() 会阻塞当前 coroutine，因此这里用后台 task 延迟打开，
      避免浏览器在 socket bind 前访问导致首屏失败。
    """
    assert url.startswith("http://") or url.startswith("https://"), "dashboard URL 必须是 HTTP(S)"
    await asyncio.sleep(0.8)
    opened = webbrowser.open(url)
    if not opened:
        logger.warning("浏览器未自动打开，请手动访问: %s", url)


def _configure_logging(level: str) -> None:
    """配置本地 CLI 日志输出。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="[%(levelname)s] %(name)s: %(message)s",
    )


def _print_config(config: LocalScriptConfig) -> None:
    """打印本次运行的基础配置。"""
    print(f"[drama_engine] script: {config.script_path}")
    print(f"[drama_engine] params: {json.dumps(config.params, ensure_ascii=False, sort_keys=True)}")
    print(f"[drama_engine] seats: {', '.join(config.seat_ids)}")
    print(f"[drama_engine] mode: {'dry-run' if config.dry_run else 'real-llm'}")


def _assert_human_seats_exist(human_seat_ids: set[str], seat_ids: list[str]) -> None:
    """确认 human_players 引用的 seat 存在。"""
    missing = sorted(human_seat_ids.difference(seat_ids))
    if missing:
        raise LocalRunError(f"human_players 中存在未知 seat: {missing}; 可用 seat: {seat_ids}")


def _print_assignment(runtime: Any) -> None:
    """打印发牌后的 seat/role 摘要。"""
    print("[drama_engine] assigned roles:")
    for seat in runtime.seat_summary():
        print(
            "  - {seat_id}: role={role} alive={alive} controller={controller}".format(
                seat_id=seat["seat_id"],
                role=seat.get("role_snapshot") or "?",
                alive=seat.get("alive_snapshot"),
                controller=seat.get("controller_type"),
            )
        )


async def _auto_step_until_done(
    registry: SessionRegistry,
    session_id: str,
    task: asyncio.Task[Any] | None,
    max_steps: int,
    delay: float,
) -> None:
    """单步模式下自动放行，直到任务结束或达到上限。"""
    assert max_steps > 0, "max_steps 必须大于 0"
    for step_index in range(max_steps):
        assert step_index >= 0, "step_index 不应为负数"
        if task is not None and task.done():
            return
        await registry.step_session(session_id, count=1)
        await asyncio.sleep(max(0.0, delay))
    raise LocalRunError(f"auto-step 达到上限 {max_steps}，疑似流程未结束")


def _print_result(runtime: Any, event_mode: str, event_limit: int) -> None:
    """打印运行结果和可选事件回放。"""
    print(f"[drama_engine] final status: {runtime.session.status}")
    ended_events = [
        event for event in runtime.event_store.host_backlog()
        if event.get("kind") == "session_ended"
    ]
    if ended_events:
        print(f"[drama_engine] result: {ended_events[-1].get('result', '')}")
    if event_mode == "none":
        return
    events = _select_events(runtime, event_mode)
    if event_limit > 0:
        events = events[-event_limit:]
    print(f"[drama_engine] events ({event_mode}, count={len(events)}):")
    for event in events:
        print(_format_event(event))


def _select_events(runtime: Any, event_mode: str) -> list[dict[str, Any]]:
    """根据模式选择事件回放。"""
    if event_mode == "public":
        return runtime.event_store.public_backlog()
    if event_mode == "host":
        return runtime.event_store.host_backlog()
    if event_mode == "all":
        events = list(runtime.event_store.host_backlog())
        for seat_id in runtime.session.seats.keys():
            events.extend(runtime.event_store.private_backlog(seat_id))
        return sorted(events, key=lambda event: int(event.get("seq") or 0))
    return []


def _format_event(event: dict[str, Any]) -> str:
    """把事件压缩成 CLI 友好的单行文本。"""
    seq = event.get("seq", "?")
    kind = event.get("kind") or event.get("type") or "event"
    actor = event.get("actor") or event.get("sender") or event.get("seat_id") or ""
    scope = event.get("scope") or event.get("audience") or ""
    text = event.get("text") or event.get("result") or event.get("message") or ""
    if not text and kind in {"roles_snapshot", "__view__"}:
        text = json.dumps({k: v for k, v in event.items() if k not in {"session_id"}}, ensure_ascii=False)
    prefix = f"#{seq} {kind}"
    if actor:
        prefix += f" actor={actor}"
    if scope:
        prefix += f" scope={scope}"
    return f"  {prefix}: {text}"


def main() -> None:
    """同步入口，供 `python drama_engine/run_script.py` 调用。"""
    try:
        raise_code = asyncio.run(run_local_script())
    except KeyboardInterrupt:
        print("\n[drama_engine] interrupted")
        raise_code = 130
    except Exception as exc:
        print(f"[drama_engine] error: {exc}", file=sys.stderr)
        raise_code = 1
    raise SystemExit(raise_code)


if __name__ == "__main__":
    main()

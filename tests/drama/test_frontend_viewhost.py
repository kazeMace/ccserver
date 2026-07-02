"""Host 端 ViewHost 与 DSL 视图注册表的契约测试。"""

import ast
import re
from pathlib import Path

from drama_engine.core.dsl.registry import build_default_dsl_registry


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "drama_engine" / "service" / "frontend"
VIEWER_JS = FRONTEND_DIR / "live_viewer.js"
VIEWER_CSS = FRONTEND_DIR / "live_viewer.css"
PLAYER_JS = FRONTEND_DIR / "player.js"
SIMPLE_CSS = FRONTEND_DIR / "simple.css"


def _frontend_view_kinds() -> set[str]:
    """从 JS 插件声明中读取 kinds，避免前端漏注册 DSL 已声明的视图类型。"""
    source = VIEWER_JS.read_text(encoding="utf-8")
    matches = re.findall(r"kinds:\s*(\[[^\]]*\])", source)
    kinds: set[str] = set()
    for raw in matches:
        for value in ast.literal_eval(raw):
            kinds.add(value)
    return kinds


def test_frontend_viewhost_covers_registered_core_view_kinds():
    """DSL registry 注册的核心 view kind 必须有浏览器端插件渲染器。"""
    registry = build_default_dsl_registry()
    frontend_kinds = _frontend_view_kinds()

    missing = sorted(set(registry.view_kind_names()) - frontend_kinds)

    assert missing == []


def test_frontend_viewhost_has_styles_for_rich_view_plugins():
    """复杂视图插件必须有稳定 CSS，避免渲染后挤压 Host 布局。"""
    css = VIEWER_CSS.read_text(encoding="utf-8")

    for selector in [
        ".text-view",
        ".markdown-view",
        ".data-table",
        ".list-view",
        ".board-view",
        ".board-cell",
        ".cards-view",
        ".mini-card",
        ".vote-grid",
    ]:
        assert selector in css


def test_player_page_supports_structured_action_ux():
    """Player page should render schema fields, deadline, and inline errors."""
    source = PLAYER_JS.read_text(encoding="utf-8")
    css = SIMPLE_CSS.read_text(encoding="utf-8")

    for marker in [
        "buildActionMeta",
        "deadlineText",
        "buildSchemaFields",
        "data-schema-field",
        "coerceSchemaValue",
        "showActionError",
    ]:
        assert marker in source
    for selector in [
        ".action-meta",
        ".action-error",
        ".schema-grid",
        ".field-help",
    ]:
        assert selector in css

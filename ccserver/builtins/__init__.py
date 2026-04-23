"""
ccserver.builtins — 内置资源仓库。

包含随项目一起发布的内置 tool、skill、agent、hook 定义。
这些内容是系统默认应该知道的"必读内容"，
可通过 ProjectSettings / feature flags 进行启用/禁用控制。
"""

from pathlib import Path


def _builtins_dir() -> Path:
    return Path(__file__).parent


def list_builtin_tools() -> list[str]:
    """返回 builtins/tools/ 下注册的工具类名列表。"""
    from .tools import __all__ as _tools_all
    return [name for name in _tools_all if name.startswith("BT")]


def list_builtin_agents() -> list[Path]:
    """返回 builtins/agents/ 下的 *.md 定义文件列表。"""
    agents_dir = _builtins_dir() / "agents"
    if not agents_dir.exists():
        return []
    return sorted(agents_dir.glob("*.md"))


def list_builtin_skills() -> list[Path]:
    """返回 builtins/skills/ 下的 SKILL.md 文件列表。"""
    skills_dir = _builtins_dir() / "skills"
    if not skills_dir.exists():
        return []
    return sorted(skills_dir.rglob("SKILL.md"))


def list_builtin_hooks() -> list[Path]:
    """返回 builtins/hooks/ 下的 HOOK.md 文件列表。"""
    hooks_dir = _builtins_dir() / "hooks"
    if not hooks_dir.exists():
        return []
    return sorted(hooks_dir.rglob("HOOK.md"))

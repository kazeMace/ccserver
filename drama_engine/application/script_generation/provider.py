"""Natural-language script generation boundary.

当前不绑定具体 Skill 实现，只提供管理后端可调用的稳定抽象和一个可用的草稿生成器。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ScriptGenerationRequest:
    """Request for natural-language script draft creation."""

    prompt: str
    materials: list[str]
    base_script_id: str = ""
    options: dict[str, Any] | None = None


@dataclass(slots=True)
class ScriptGenerationResult:
    """Result of natural-language generation."""

    name: str
    content: str
    notes: list[str]


class ScriptGenerationProvider:
    """Provider interface for future Skill-backed generation."""

    def generate_script(self, request: ScriptGenerationRequest) -> ScriptGenerationResult:
        """Generate a YAML draft. Subclasses should override this method."""
        raise NotImplementedError


class TemplateScriptGenerationProvider(ScriptGenerationProvider):
    """Safe first provider that creates an editable draft template from prompt.

    这不是最终 Skill 能力，但它让管理后端入口可用：用户输入自然语言后会得到
    一个带注释的 draft YAML，后续必须经过 validate/inspect/playtest。
    """

    def generate_script(self, request: ScriptGenerationRequest) -> ScriptGenerationResult:
        assert request.prompt.strip(), "prompt 不能为空"
        title = request.prompt.strip().splitlines()[0][:40]
        content = f'''# 根据自然语言需求自动创建的草稿。
# 生成来源：管理端自然语言入口。该草稿必须继续检查、查看、试玩。
meta:
  title: "{_yaml_quote(title)}"
  description: "请根据需求补全 DSL。原始需求: {_yaml_quote(request.prompt[:160])}"
  min_players: 0
  max_players: 0

roles: []

players:
  count: 0
  initial_attrs:
    alive: true
  casting:
    type: shuffle
    distribution: {{}}

scopes:
- name: public
  display_name: "全场"
  members: all
  delivery: immediate

initial_state:
  GAME: {{}}

flow:
  loop: false
  scenes: []

referee:
  victory:
    rules: []
'''
        return ScriptGenerationResult(
            name=f"自然语言草稿 - {title}",
            content=content,
            notes=[
                "已根据自然语言创建 DSL 草稿模板。",
                "当前未绑定具体 Skill 生成器，因此不会伪造完整规则。",
                "请继续编辑 YAML，并执行检查、查看、试玩后再发布。",
            ],
        )


def _yaml_quote(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")

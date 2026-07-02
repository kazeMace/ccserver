"""
doc_gen — 从配置 schema 自动生成配置参考文档。

设计（见 spec §11）：
  遍历 CcServerConfig 各段 dataclass 的字段，读取 metadata（desc/env）+ 默认值，
  渲染成分段 Markdown 表格。文档由 schema 派生，结构上不可能漂移。

用法：
  - 程序内：render_reference() 返回 markdown 字符串。
  - 命令行：python -m ccserver.configuration.doc_gen  → 写入 docs/config-reference.md

同步保证：tests/test_config_doc_sync.py 比对 render_reference() 与磁盘文件，
不一致即测试失败，强制"改配置必更新文档"。
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path

from .schema import (
    ModelConfig,
    VlmConfig,
    AgentBehaviorConfig,
    PermissionConfig,
    ToolConfig,
    CompactionConfig,
    InfraConfig,
)


# 段顺序 + 中文标题（与 CcServerConfig 字段一一对应）
_SECTIONS = [
    ("model", ModelConfig, "model — 主 LLM 连接"),
    ("vlm", VlmConfig, "vlm — 视觉模型"),
    ("agent", AgentBehaviorConfig, "agent — agent 行为"),
    ("permissions", PermissionConfig, "permissions — 工具/命令权限"),
    ("tools", ToolConfig, "tools — 工具开关"),
    ("compaction", CompactionConfig, "compaction — 上下文压缩"),
    ("infra", InfraConfig, "infra — 基础设施/部署（进程级）"),
]


def _default_value(section_cls, field_name):
    """获取某段某字段的默认值（用默认实例求值，路径转 str）。"""
    inst = section_cls()
    val = getattr(inst, field_name)
    if isinstance(val, Path):
        return str(val)
    return val


def _render_section(section_key: str, section_cls, title: str) -> str:
    """渲染单个配置段为 Markdown 表格。"""
    lines = []
    lines.append(f"### {title}")
    lines.append("")
    lines.append("| 字段 | 说明 | 默认值 | 环境变量 |")
    lines.append("| --- | --- | --- | --- |")
    for f in fields(section_cls):
        # 内部字段（下划线前缀，如 InfraConfig._PATH_FIELDS）不计入
        if f.name.startswith("_"):
            continue
        desc = f.metadata.get("desc", "")
        env = f.metadata.get("env", "") or ""
        default = _default_value(section_cls, f.name)
        default_str = "（无）" if default in (None, "") else f"`{default}`"
        env_str = f"`{env}`" if env else "—"
        lines.append(f"| `{section_key}.{f.name}` | {desc} | {default_str} | {env_str} |")
    lines.append("")
    return "\n".join(lines)


def render_reference() -> str:
    """渲染完整配置参考文档（Markdown 字符串）。"""
    out = []
    out.append("# ccserver 配置参考 (Config Reference)")
    out.append("")
    out.append("> 本文件由 `ccserver/configuration/doc_gen.py` 从配置 schema 自动生成。")
    out.append("> 请勿手工编辑；改动配置字段后运行 `python -m ccserver.configuration.doc_gen` 重新生成。")
    out.append("")
    out.append("## 作用域与加载优先级")
    out.append("")
    out.append("配置分三作用域，自上而下逐层覆盖（后者优先）：")
    out.append("")
    out.append("1. **PROCESS**：代码默认值 → `~/.ccserver/settings.json` → 环境变量")
    out.append("2. **SESSION**：PROCESS → `<project>/.ccserver/settings.local.json` → 会话/请求传参")
    out.append("3. **AGENT**：SESSION → AgentDef 覆盖 → spawn/create_root 传参")
    out.append("")
    out.append("配置文件为 JSON，结构与下列分段一致（嵌套对象）。例如：")
    out.append("")
    out.append("```json")
    out.append("{")
    out.append('  "model": { "model_id": "claude-sonnet-4-6", "api_type": "anthropic-messages" },')
    out.append('  "agent": { "main_round_limit": 100 },')
    out.append('  "permissions": { "deny": ["Bash"], "ask": ["Write"] }')
    out.append("}")
    out.append("```")
    out.append("")
    out.append("## 配置段")
    out.append("")
    for section_key, section_cls, title in _SECTIONS:
        assert is_dataclass(section_cls), f"{section_cls} 不是 dataclass"
        out.append(_render_section(section_key, section_cls, title))
    return "\n".join(out).rstrip() + "\n"


# 文档目标路径（仓库根 docs/config-reference.md）
DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "config-reference.md"


def write_reference(path: Path = DOC_PATH) -> Path:
    """渲染并写入配置参考文档，返回写入路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_reference(), encoding="utf-8")
    return path


if __name__ == "__main__":
    written = write_reference()
    print(f"配置参考文档已生成: {written}")

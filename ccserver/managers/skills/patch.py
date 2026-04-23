from dataclasses import dataclass, field
from pathlib import Path


# ─── SkillPatch ───────────────────────────────────────────────────────────────


@dataclass
class SkillPatch:
    """
    一个 Skill 目录解析后的结构化表示，对应 agentskills.io 三层渐进式设计：

      Tier 1 — Catalog（发现）
          name / description / location
          会话启动时注入系统提示，每条约 50-100 token，始终在上下文中。

      Tier 2 — Activation（激活）
          body：完整的 SKILL.md 正文（frontmatter 之后的 Markdown）
          按需加载，激活时注入对话上下文。

      Tier 3 — Resources（执行）
          scripts / references / assets：支撑文件路径列表
          激活时列出，模型按需通过文件读取工具加载，不主动注入。

    目录结构：
        my-skill/
        ├── SKILL.md        必需：前置元数据 + Markdown 指令
        ├── scripts/        可选：可执行脚本
        ├── references/     可选：参考文档
        └── assets/         可选：模板、资源文件
    """

    # ── Tier 1：Catalog（轻量元数据，始终加载）────────────────────────────────
    name: str                               # skill 唯一标识符（来自 frontmatter 或目录名）
    description: str                        # 一句话描述，说明何时使用该 skill
    location: Path                          # SKILL.md 的绝对路径，用于文件读取激活方式
    tags: list[str] = field(default_factory=list)  # 可选标签，用于分类筛选

    # ── Tier 2：Activation（完整指令，延迟加载）───────────────────────────────
    # 不直接存储 body，通过 load_body() 按需从磁盘读取，
    # 避免会话启动时一次性加载所有 skill 的全量内容。

    # ── Tier 3：Resources（资源路径，激活时列出）──────────────────────────────
    scripts: list[Path] = field(default_factory=list)    # scripts/ 下的文件列表
    references: list[Path] = field(default_factory=list) # references/ 下的文件列表
    assets: list[Path] = field(default_factory=list)     # assets/ 下的文件列表

    # ── 额外元数据（frontmatter 中未被上述字段覆盖的键值）────────────────────
    extra: dict = field(default_factory=dict)

    @property
    def root_dir(self) -> Path:
        """skill 目录根路径（SKILL.md 的父目录）。"""
        return self.location.parent

    def load_body(self) -> str:
        """从磁盘读取 SKILL.md 正文（frontmatter 之后的内容）。"""
        text = self.location.read_text(encoding="utf-8")
        # 跳过 frontmatter 块
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text.strip()

    def to_activation_block(self, body: str) -> str:
        """
        Tier 2：生成激活时注入对话上下文的结构化内容块。
        包含完整指令、skill 目录路径、资源文件列表。
        """
        resources_xml = self._build_resources_xml()
        return (
            f'<skill_content name="{self.name}">\n'
            f"{body}\n\n"
            f"Skill directory: {self.root_dir}\n"
            f"Relative paths in this skill are relative to the skill directory.\n"
            f"{resources_xml}"
            f"</skill_content>"
        )

    def _build_resources_xml(self) -> str:
        all_files = self.scripts + self.references + self.assets
        if not all_files:
            return ""
        lines = ["<skill_resources>"]
        for f in all_files:
            lines.append(f"  <file>{f.relative_to(self.root_dir)}</file>")
        lines.append("</skill_resources>\n")
        return "\n".join(lines) + "\n"

"""
CommandLoader — 扫描 .ccserver/commands/ 目录，加载 command 定义。

command 文件格式（Markdown + frontmatter）：
    ---
    name: persona
    description: 切换或管理对话人设
    builtin: false   # true 时 agent 层有前置逻辑，false 时纯粹交给 LLM
    ---
    （command 的说明正文，注入到消息的最后一个 content block）

发现路径（按优先级从高到低）：
    {project_root}/.ccserver/commands/
    ~/.ccserver/commands/
"""

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ...utils import parse_frontmatter


@dataclass
class CommandDef:
    name: str           # command 名称（不含 /）
    description: str    # 一句话描述
    location: Path      # .md 文件绝对路径
    builtin: bool       # True 时 agent 层需要特殊前置处理

    def load_body(self) -> str:
        """从磁盘读取 command 说明正文（frontmatter 之后的内容）。"""
        text = self.location.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        if meta is not None:
            return body
        return text.strip()


class CommandLoader:
    """
    扫描多个目录发现 command 定义文件，解析为 CommandDef 对象。

    同名冲突时高优先级（先传入的目录）优先，低优先级版本被跳过。
    """

    def __init__(self, *commands_dirs: Path):
        self.commands: dict[str, CommandDef] = {}
        for commands_dir in commands_dirs:
            self._scan(commands_dir)

    @classmethod
    def from_project_root(cls, project_root: Path, global_config_dir: Path | None = None) -> "CommandLoader":
        global_dir = global_config_dir or Path.home() / ".ccserver"
        return cls(
            project_root / ".ccserver" / "commands",
            global_dir / "commands",
        )

    def _scan(self, commands_dir: Path):
        if not commands_dir.exists():
            return
        for md_file in sorted(commands_dir.glob("*.md")):
            cmd = self._parse(md_file)
            if cmd is None:
                continue
            if cmd.name in self.commands:
                logger.warning(
                    "Command name collision | name={} shadowed by existing entry, skipping {}",
                    cmd.name, md_file,
                )
                continue
            self.commands[cmd.name] = cmd
            logger.debug("Command loaded | name={} builtin={}", cmd.name, cmd.builtin)

    def _parse(self, md_file: Path) -> CommandDef | None:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("Failed to read command file | path={} error={}", md_file, e)
            return None

        meta, _ = parse_frontmatter(text)
        if meta is None:
            meta = {}

        # 没有 frontmatter 时用文件名作为 name
        name = meta.get("name", md_file.stem).strip()
        description = meta.get("description", "").strip()
        builtin_val = meta.get("builtin", False)
        builtin = (
            builtin_val
            if isinstance(builtin_val, bool)
            else str(builtin_val).strip().lower() == "true"
        )

        return CommandDef(
            name=name,
            description=description,
            location=md_file.resolve(),
            builtin=builtin,
        )

    def get(self, name: str) -> CommandDef | None:
        return self.commands.get(name)

    def list_commands(self) -> list:
        """返回所有已加载 command 的元数据列表，格式与 SkillLoader.list_skills() 一致。"""
        return [
            {"name": f"/{c.name}", "description": c.description, "location": str(c.location)}
            for c in self.commands.values()
        ]

"""
CommandLoader — 扫描 .ccserver/commands/ 目录，加载 command 定义。

command 文件格式（Markdown + frontmatter）：
    ---
    name: persona
    description: 切换或管理对话人设
    builtin: false   # true 时 agent 层有前置逻辑，false 时纯粹交给 LLM
    aliases: [p]    # 可选：命令别名列表
    args:           # 可选：参数声明（供客户端补全提示）
      - name: style
        description: 对话风格
        required: false
        choices: [正式, 轻松, 简洁]
    ---
    （command 的说明正文，注入到消息的最后一个 content block）

发现路径（按优先级从高到低）：
    {project_root}/.ccserver/commands/
    ~/.ccserver/commands/
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from ...utils import parse_frontmatter


@dataclass
class CommandArg:
    """
    命令参数声明（供客户端补全提示使用）。

    Attributes:
        name:        参数名
        description: 参数说明
        required:    是否必填（默认 False）
        choices:     可选值列表（None 表示自由输入）
    """
    name: str
    description: str = ""
    required: bool = False
    choices: Optional[list[str]] = None


@dataclass
class CommandDef:
    """
    单个 command 的完整定义。

    Attributes:
        name:        command 名称（不含 /）
        description: 一句话描述
        location:    .md 文件绝对路径
        builtin:     True 时 agent 层在 command_registry 中有对应处理器
        aliases:     命令别名列表（不含 /），如 ["c"] 表示 /c 也可触发
        args:        参数声明列表（供客户端 UI 补全提示）
    """
    name: str
    description: str
    location: Path
    builtin: bool = False
    aliases: list[str] = field(default_factory=list)
    args: list[CommandArg] = field(default_factory=list)

    def load_body(self) -> str:
        """从磁盘读取 command 说明正文（frontmatter 之后的内容）。"""
        text = self.location.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        return body if meta is not None else text.strip()


class CommandLoader:
    """
    扫描多个目录发现 command 定义文件，解析为 CommandDef 对象。

    同名冲突时高优先级（先传入的目录）优先，低优先级版本被跳过。
    别名也会注册到查找索引，支持 /alias 触发对应命令。
    """

    def __init__(self, *commands_dirs: Path):
        # 主索引：name → CommandDef
        self.commands: dict[str, CommandDef] = {}
        # 别名索引：alias → canonical name
        self._aliases: dict[str, str] = {}
        for commands_dir in commands_dirs:
            self._scan(commands_dir)

    @classmethod
    def from_project_root(cls, project_root: Path, global_config_dir: Path | None = None) -> "CommandLoader":
        global_dir = global_config_dir or Path.home() / ".ccserver"
        return cls(
            project_root / ".ccserver" / "commands",
            global_dir / "commands",
        )

    def _scan(self, commands_dir: Path) -> None:
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
            # 注册别名
            for alias in cmd.aliases:
                if alias in self._aliases or alias in self.commands:
                    logger.warning(
                        "Command alias collision | alias={} cmd={}",
                        alias, cmd.name,
                    )
                    continue
                self._aliases[alias] = cmd.name
            logger.debug(
                "Command loaded | name={} builtin={} aliases={}",
                cmd.name, cmd.builtin, cmd.aliases,
            )

    def _parse(self, md_file: Path) -> CommandDef | None:
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("Failed to read command file | path={} error={}", md_file, e)
            return None

        meta, _ = parse_frontmatter(text)
        if meta is None:
            meta = {}

        name = meta.get("name", md_file.stem).strip()
        description = meta.get("description", "").strip()

        builtin_val = meta.get("builtin", False)
        builtin = (
            builtin_val
            if isinstance(builtin_val, bool)
            else str(builtin_val).strip().lower() == "true"
        )

        # 解析别名列表
        raw_aliases = meta.get("aliases", [])
        if isinstance(raw_aliases, list):
            aliases = [str(a).strip() for a in raw_aliases if a]
        else:
            aliases = []

        # 解析参数声明列表
        raw_args = meta.get("args", [])
        args: list[CommandArg] = []
        if isinstance(raw_args, list):
            for raw_arg in raw_args:
                if not isinstance(raw_arg, dict):
                    continue
                arg_name = raw_arg.get("name", "").strip()
                if not arg_name:
                    continue
                choices = raw_arg.get("choices")
                args.append(CommandArg(
                    name=arg_name,
                    description=str(raw_arg.get("description", "")).strip(),
                    required=bool(raw_arg.get("required", False)),
                    choices=list(choices) if isinstance(choices, list) else None,
                ))

        return CommandDef(
            name=name,
            description=description,
            location=md_file.resolve(),
            builtin=builtin,
            aliases=aliases,
            args=args,
        )

    def get(self, name: str) -> CommandDef | None:
        """按名称或别名查找 CommandDef。"""
        cmd = self.commands.get(name)
        if cmd is not None:
            return cmd
        # 尝试别名
        canonical = self._aliases.get(name)
        return self.commands.get(canonical) if canonical else None

    def list_commands(self) -> list[dict]:
        """
        返回所有已加载 command 的元数据列表。

        格式与 SkillLoader.list_skills() 一致，额外包含 args/aliases。
        """
        result = []
        for c in self.commands.values():
            entry = {
                "name": f"/{c.name}",
                "description": c.description,
                "location": str(c.location),
                "builtin": c.builtin,
                "aliases": [f"/{a}" for a in c.aliases],
            }
            if c.args:
                entry["args"] = [
                    {
                        "name": a.name,
                        "description": a.description,
                        "required": a.required,
                        "choices": a.choices,
                    }
                    for a in c.args
                ]
            result.append(entry)
        return result

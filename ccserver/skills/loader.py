import re
from pathlib import Path

from loguru import logger

from .skill_patch import SkillPatch
from ..utils import parse_frontmatter


# ─── SkillLoader ──────────────────────────────────────────────────────────────

# 扫描时跳过的目录名
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox"}


class SkillLoader:
    """
    扫描多个目录发现 Skill，解析为 SkillPatch 对象，支持按需激活。

    加载顺序与优先级：
        1. project-level（相对工作目录）优先级高于 user-level
        2. 同优先级内，先发现的先注册；名称冲突时记录警告并保留高优先级版本
        3. 每个 scope 同时扫描客户端私有目录与 .agents/skills/ 互操作路径

    发现路径（按优先级从高到低）：
        {workdir}/.agents/skills/
        {workdir}/.ccserver/skills/
        ~/.agents/skills/
        ~/.ccserver/skills/
    """

    def __init__(self, *skills_dirs: Path):
        """
        按优先级顺序传入多个扫描目录，前面的目录优先级更高。
        通常：project-level 目录在前，user-level 目录在后。
        """
        self.skills: dict[str, SkillPatch] = {}
        self._activated: set[str] = set()  # 已激活 skill 名称，用于去重

        for skills_dir in skills_dirs:
            self._scan(skills_dir)

    @classmethod
    def from_workdir(cls, workdir: Path, global_config_dir: Path | None = None) -> "SkillLoader":
        """
        根据工作目录自动构建标准扫描路径（project-level + user-level）。
        global_config_dir 默认为 ~/.ccserver，可通过 config.GLOBAL_CONFIG_DIR 覆盖。
        """
        global_dir = global_config_dir or Path.home() / ".ccserver"
        dirs = [
            workdir / ".agents" / "skills",
            workdir / ".ccserver" / "skills",
            global_dir / "skills",
        ]
        return cls(*dirs)

    # ── 扫描与解析 ────────────────────────────────────────────────────────────

    def _scan(self, skills_dir: Path):
        """扫描一个目录，将发现的 skill 注册到 self.skills（低优先级不覆盖已存在）。"""
        if not skills_dir.exists():
            return
        for skill_md in self._find_skill_files(skills_dir):
            patch = self._parse(skill_md)
            if patch is None:
                continue
            if patch.name in self.skills:
                logger.warning(
                    "Skill name collision | name={} shadowed by existing entry, skipping {}",
                    patch.name, skill_md,
                )
                continue
            self.skills[patch.name] = patch

    def _find_skill_files(self, base: Path) -> list[Path]:
        """在 base 目录下递归查找 SKILL.md，跳过无关目录，限制深度为 6 层。"""
        found = []
        self._walk(base, base, depth=0, max_depth=6, found=found)
        return sorted(found)

    def _walk(self, path: Path, base: Path, depth: int, max_depth: int, found: list):
        if depth > max_depth:
            return
        for child in path.iterdir():
            if child.is_dir() and child.name not in _SKIP_DIRS:
                skill_md = child / "SKILL.md"
                if skill_md.exists():
                    found.append(skill_md)
                self._walk(child, base, depth + 1, max_depth, found)

    def _parse(self, skill_md: Path) -> SkillPatch | None:
        """解析 SKILL.md，返回 SkillPatch；解析失败时记录日志并返回 None。"""
        root_dir = skill_md.parent
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("Failed to read SKILL.md | path={} error={}", skill_md, e)
            return None

        meta, _ = parse_frontmatter(text)
        if meta is None:
            logger.error("Unparseable frontmatter, skipping | path={}", skill_md)
            return None

        name = meta.pop("name", root_dir.name)
        description = meta.pop("description", "").strip()
        if not description:
            logger.warning("Missing description, skipping | path={}", skill_md)
            return None

        if len(name) > 64:
            logger.warning("Skill name exceeds 64 chars | name={} path={}", name, skill_md)
        if name != root_dir.name:
            logger.warning("Skill name mismatch with directory | name={} dir={}", name, root_dir.name)

        tags_raw = meta.pop("tags", "")
        tags = [t.strip() for t in re.split(r"[,\[\]]", tags_raw) if t.strip()]

        return SkillPatch(
            name=name,
            description=description,
            location=skill_md.resolve(),
            tags=tags,
            scripts=self._list_dir(root_dir / "scripts"),
            references=self._list_dir(root_dir / "references"),
            assets=self._list_dir(root_dir / "assets"),
            extra=meta,
        )

    def _list_dir(self, path: Path) -> list[Path]:
        if not path.exists():
            return []
        return sorted(f for f in path.iterdir() if f.is_file())

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> SkillPatch | None:
        """按名称获取 SkillPatch（仅元数据，不加载 body）。"""
        return self.skills.get(name)

    def activate(self, name: str) -> str:
        """
        Tier 2 激活：按需从磁盘读取 body，返回结构化激活内容块。
        已激活过的 skill 记录去重，但仍返回内容（由调用方决定是否跳过注入）。
        """
        patch = self.skills.get(name)
        if not patch:
            available = ", ".join(self.skills)
            return f"Error: Unknown skill '{name}'. Available: {available}"

        already_activated = name in self._activated
        self._activated.add(name)

        if already_activated:
            logger.debug("Skill already activated | name={}", name)

        body = patch.load_body()
        return patch.to_activation_block(body)

    def list_skills(self) -> list:
        """
        返回所有已加载 skill 的元数据列表，供 lib 层格式化为 catalog。
        每项包含 name、description、location 字段。
        """
        return [
            {"name": p.name, "description": p.description, "location": str(p.location)}
            for p in self.skills.values()
        ]

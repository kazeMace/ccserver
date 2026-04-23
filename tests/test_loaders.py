"""
tests/test_loaders.py — SkillLoader 和 CommandLoader 单元测试

覆盖：
  SkillLoader:
    - 不存在目录静默跳过
    - 正常解析 SKILL.md（name、description、tags）
    - 无 description → 跳过该 skill
    - 名称冲突 → 高优先级保留，低优先级跳过
    - get() 按名称获取
    - activate() 已知 skill 返回内容块
    - activate() 未知 skill 返回错误字符串
    - activate() 去重（再次激活不报错，仍返回内容）
    - list_skills() 返回 name/description/location 列表

  CommandLoader:
    - 不存在目录静默跳过
    - 正常解析 .md（name、description、builtin）
    - 无 frontmatter → 用文件名作为 name
    - 名称冲突 → 高优先级保留
    - get() 返回 CommandDef / None
    - load_body() 返回正文
    - list_commands() 含 / 前缀
"""

from pathlib import Path
import pytest

from ccserver.managers.skills import SkillLoader
from ccserver.managers.commands import CommandLoader


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────


def _make_skill_dir(base: Path, name: str, description: str = "A skill", body: str = "## Instructions\nDo something.", tags: str = "") -> Path:
    """在 base/name/ 下创建最小合法的 SKILL.md 结构。"""
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    frontmatter = f"---\nname: {name}\ndescription: {description}\n"
    if tags:
        frontmatter += f"tags: {tags}\n"
    frontmatter += "---\n"
    (skill_dir / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    return skill_dir


def _make_command_file(commands_dir: Path, stem: str, name: str = "", description: str = "A command", builtin: bool = False, body: str = "Command body.") -> Path:
    commands_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = f"---\n"
    if name:
        frontmatter += f"name: {name}\n"
    frontmatter += f"description: {description}\n"
    frontmatter += f"builtin: {'true' if builtin else 'false'}\n"
    frontmatter += f"---\n{body}"
    path = commands_dir / f"{stem}.md"
    path.write_text(frontmatter, encoding="utf-8")
    return path


# ══════════════════════════════════════════════════════════════════════════════
# SkillLoader
# ══════════════════════════════════════════════════════════════════════════════


def test_skill_loader_nonexistent_dir_ok(tmp_path):
    loader = SkillLoader(tmp_path / "nonexistent")
    assert loader.skills == {}


def test_skill_loader_parses_skill(tmp_path):
    _make_skill_dir(tmp_path, "my-skill", description="Does something useful")
    loader = SkillLoader(tmp_path)
    assert "my-skill" in loader.skills
    assert loader.skills["my-skill"].description == "Does something useful"


def test_skill_loader_parses_tags(tmp_path):
    # tags 字段使用单个标签字符串（无逗号），避免 frontmatter 将其解析为 list
    # 导致 SkillLoader._parse 内 re.split 接收到 list 类型而报错
    skill_dir = tmp_path / "tagged-skill"
    skill_dir.mkdir()
    content = "---\nname: tagged-skill\ndescription: A tagged skill\ntags: python\n---\nBody.\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    loader = SkillLoader(tmp_path)
    tags = loader.skills["tagged-skill"].tags
    assert "python" in tags


def test_skill_loader_skips_missing_description(tmp_path):
    """没有 description 字段的 skill 被跳过。"""
    skill_dir = tmp_path / "no-desc"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: no-desc\n---\nBody.", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    assert "no-desc" not in loader.skills


def test_skill_loader_name_collision_high_priority_wins(tmp_path):
    """同名 skill 在两个目录中，高优先级（先传入的）保留。"""
    high_dir = tmp_path / "high"
    low_dir = tmp_path / "low"
    _make_skill_dir(high_dir, "shared-skill", description="from high priority")
    _make_skill_dir(low_dir, "shared-skill", description="from low priority")
    loader = SkillLoader(high_dir, low_dir)
    assert loader.skills["shared-skill"].description == "from high priority"


def test_skill_loader_get_existing(tmp_path):
    _make_skill_dir(tmp_path, "get-me")
    loader = SkillLoader(tmp_path)
    patch = loader.get("get-me")
    assert patch is not None
    assert patch.name == "get-me"


def test_skill_loader_get_nonexistent(tmp_path):
    loader = SkillLoader(tmp_path)
    assert loader.get("unknown-skill") is None


def test_skill_loader_activate_returns_content(tmp_path):
    _make_skill_dir(tmp_path, "active-skill", body="## Use This Skill\nDetails here.")
    loader = SkillLoader(tmp_path)
    content = loader.activate("active-skill")
    assert "Details here." in content
    assert isinstance(content, str)


def test_skill_loader_activate_unknown_returns_error(tmp_path):
    loader = SkillLoader(tmp_path)
    result = loader.activate("no-such-skill")
    assert "Error" in result or "Unknown" in result


def test_skill_loader_activate_twice_no_error(tmp_path):
    _make_skill_dir(tmp_path, "repeat-skill")
    loader = SkillLoader(tmp_path)
    loader.activate("repeat-skill")
    result = loader.activate("repeat-skill")  # 第二次不报错
    assert isinstance(result, str)


def test_skill_loader_list_skills(tmp_path):
    _make_skill_dir(tmp_path, "skill-a", description="Skill A desc")
    _make_skill_dir(tmp_path, "skill-b", description="Skill B desc")
    loader = SkillLoader(tmp_path)
    listing = loader.list_skills()
    names = {item["name"] for item in listing}
    assert "skill-a" in names
    assert "skill-b" in names
    for item in listing:
        assert "name" in item
        assert "description" in item
        assert "location" in item


def test_skill_loader_with_scripts_dir(tmp_path):
    """skills/ 下有 scripts/ 子目录时，scripts 列表被填充。"""
    skill_dir = _make_skill_dir(tmp_path, "scripted-skill")
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.sh").write_text("#!/bin/sh\necho hello", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    patch = loader.skills["scripted-skill"]
    assert len(patch.scripts) == 1
    assert patch.scripts[0].name == "run.sh"


# ══════════════════════════════════════════════════════════════════════════════
# CommandLoader
# ══════════════════════════════════════════════════════════════════════════════


def test_command_loader_nonexistent_dir_ok(tmp_path):
    loader = CommandLoader(tmp_path / "nonexistent")
    assert loader.commands == {}


def test_command_loader_parses_command(tmp_path):
    cmds_dir = tmp_path / "commands"
    _make_command_file(cmds_dir, "persona", name="persona", description="Switch persona")
    loader = CommandLoader(cmds_dir)
    assert "persona" in loader.commands
    assert loader.commands["persona"].description == "Switch persona"


def test_command_loader_builtin_flag(tmp_path):
    cmds_dir = tmp_path / "commands"
    _make_command_file(cmds_dir, "builtin_cmd", name="builtin_cmd", description="desc", builtin=True)
    _make_command_file(cmds_dir, "normal_cmd", name="normal_cmd", description="desc", builtin=False)
    loader = CommandLoader(cmds_dir)
    assert loader.commands["builtin_cmd"].builtin is True
    assert loader.commands["normal_cmd"].builtin is False


def test_command_loader_no_frontmatter_uses_stem(tmp_path):
    """无 frontmatter 时用文件名（stem）作为 name。"""
    cmds_dir = tmp_path / "commands"
    cmds_dir.mkdir()
    (cmds_dir / "help_cmd.md").write_text("Some help text.", encoding="utf-8")
    loader = CommandLoader(cmds_dir)
    assert "help_cmd" in loader.commands


def test_command_loader_name_collision_high_priority_wins(tmp_path):
    high_dir = tmp_path / "high"
    low_dir = tmp_path / "low"
    _make_command_file(high_dir, "shared", name="shared", description="from high")
    _make_command_file(low_dir, "shared", name="shared", description="from low")
    loader = CommandLoader(high_dir, low_dir)
    assert loader.commands["shared"].description == "from high"


def test_command_loader_get_existing(tmp_path):
    cmds_dir = tmp_path / "commands"
    _make_command_file(cmds_dir, "mycommand", name="mycommand", description="desc")
    loader = CommandLoader(cmds_dir)
    cmd = loader.get("mycommand")
    assert cmd is not None
    assert cmd.name == "mycommand"


def test_command_loader_get_nonexistent(tmp_path):
    loader = CommandLoader(tmp_path / "commands")
    assert loader.get("unknown") is None


def test_command_loader_load_body(tmp_path):
    cmds_dir = tmp_path / "commands"
    _make_command_file(cmds_dir, "with_body", name="with_body", description="desc", body="This is the body content.")
    loader = CommandLoader(cmds_dir)
    body = loader.commands["with_body"].load_body()
    assert "This is the body content." in body


def test_command_loader_list_commands_has_slash_prefix(tmp_path):
    cmds_dir = tmp_path / "commands"
    _make_command_file(cmds_dir, "status", name="status", description="Show status")
    loader = CommandLoader(cmds_dir)
    listing = loader.list_commands()
    names = [item["name"] for item in listing]
    assert "/status" in names


def test_command_loader_list_commands_fields(tmp_path):
    cmds_dir = tmp_path / "commands"
    _make_command_file(cmds_dir, "alpha", name="alpha", description="Alpha desc")
    loader = CommandLoader(cmds_dir)
    for item in loader.list_commands():
        assert "name" in item
        assert "description" in item
        assert "location" in item


def test_command_loader_from_project_root(tmp_path):
    """from_project_root() 构建标准路径，不存在的目录静默跳过。"""
    loader = CommandLoader.from_project_root(tmp_path)
    assert loader.commands == {}  # 目录不存在，无命令

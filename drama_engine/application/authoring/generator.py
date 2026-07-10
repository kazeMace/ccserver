"""Natural-language authoring for Party Game DSL.

The authoring layer intentionally builds on the verified script matrix instead
of inventing a parallel DSL writer. It classifies a natural-language idea,
selects a known-good template, applies small metadata overrides, and then runs
the normal validate/preview/simulate/package chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "interactive_session"


@dataclass(frozen=True, slots=True)
class AuthoringTemplate:
    """One natural-language authoring template."""

    game_type: str
    script_name: str
    runtime_type: str
    extensions: tuple[str, ...]
    game_pack: str
    keywords: tuple[str, ...]
    required_questions: tuple[str, ...]
    optional_questions: tuple[str, ...] = field(default_factory=tuple)
    defaults: dict[str, Any] = field(default_factory=dict)
    risk_warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def script_path(self) -> Path:
        """Return the canonical YAML script path."""
        return SCRIPT_DIR / self.script_name


@dataclass(slots=True)
class AuthoringResult:
    """Result returned by PartyGameAuthor."""

    game_type: str
    runtime_type: str
    template_script: str
    output_path: str
    package_path: str | None
    validation: dict[str, Any]
    simulation: dict[str, Any]
    preview: dict[str, Any]
    package: dict[str, Any] | None
    required_questions: list[str]
    optional_questions: list[str]
    defaults: dict[str, Any]
    risk_warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly result."""
        return {
            "game_type": self.game_type,
            "runtime_type": self.runtime_type,
            "template_script": self.template_script,
            "output_path": self.output_path,
            "package_path": self.package_path,
            "validation": self.validation,
            "simulation": self.simulation,
            "preview": self.preview,
            "package": self.package,
            "required_questions": list(self.required_questions),
            "optional_questions": list(self.optional_questions),
            "defaults": dict(self.defaults),
            "risk_warnings": list(self.risk_warnings),
        }


class PartyGameAuthor:
    """Create validated Party Game DSL scripts from natural-language ideas."""

    def __init__(self, templates: list[AuthoringTemplate] | None = None) -> None:
        """Initialize with built-in templates unless explicitly provided."""
        self.templates = templates or build_default_authoring_templates()

    def classify(self, idea: str) -> AuthoringTemplate:
        """Classify an idea and return the best matching template."""
        assert isinstance(idea, str) and idea.strip(), "idea 不能为空"
        normalized = idea.lower()
        best_template = self.templates[0]
        best_score = -1
        for template in self.templates:
            score = 0
            for keyword in template.keywords:
                if keyword.lower() in normalized:
                    score += 1
            if score > best_score:
                best_template = template
                best_score = score
        return best_template

    def create(
        self,
        idea: str,
        output_path: str | Path,
        answers: dict[str, Any] | None = None,
        package_path: str | Path | None = None,
    ) -> AuthoringResult:
        """Create a YAML script and run validate/preview/simulate/package."""
        from drama_engine.cli import package_script, preview_script, simulate_script, validate_script

        template = self.classify(idea)
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        doc = self._load_template_doc(template)
        self._apply_authoring_metadata(doc, idea, answers or {})
        output.write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        validation = validate_script(output).to_dict()
        simulation = simulate_script(output)
        preview = preview_script(output)
        package_report = None
        package_output = None
        if package_path is not None:
            package_output = str(Path(package_path).expanduser().resolve())
            package_report = package_script(output, package_output)

        return AuthoringResult(
            game_type=template.game_type,
            runtime_type=template.runtime_type,
            template_script=template.script_name,
            output_path=str(output),
            package_path=package_output,
            validation=validation,
            simulation=simulation,
            preview=preview,
            package=package_report,
            required_questions=list(template.required_questions),
            optional_questions=list(template.optional_questions),
            defaults=dict(template.defaults),
            risk_warnings=list(template.risk_warnings),
        )

    def checklist(self, idea: str) -> dict[str, Any]:
        """Return questions/defaults/warnings for an idea without writing files."""
        template = self.classify(idea)
        return {
            "game_type": template.game_type,
            "runtime_type": template.runtime_type,
            "template_script": template.script_name,
            "required_questions": list(template.required_questions),
            "optional_questions": list(template.optional_questions),
            "defaults": dict(template.defaults),
            "risk_warnings": list(template.risk_warnings),
            "extensions": list(template.extensions),
            "game_pack": template.game_pack,
        }

    def _load_template_doc(self, template: AuthoringTemplate) -> dict[str, Any]:
        """Load a verified YAML template."""
        assert template.script_path.exists(), f"authoring template 不存在: {template.script_path}"
        doc = yaml.safe_load(template.script_path.read_text(encoding="utf-8")) or {}
        assert isinstance(doc, dict), f"authoring template 必须是 YAML 对象: {template.script_path}"
        return doc

    def _apply_authoring_metadata(
        self,
        doc: dict[str, Any],
        idea: str,
        answers: dict[str, Any],
    ) -> None:
        """Apply minimal metadata overrides to the generated document."""
        meta = doc.setdefault("meta", {})
        assert isinstance(meta, dict), "meta 必须是对象"
        if answers.get("title"):
            meta["title"] = str(answers["title"])
        else:
            meta["title"] = f"UGC: {meta.get('title') or 'Party Game'}"
        description = str(meta.get("description") or "")
        meta["description"] = f"{description}\n\nUGC idea: {idea}".strip()
        meta.setdefault("tags", [])
        if isinstance(meta["tags"], list) and "ugc" not in meta["tags"]:
            meta["tags"].append("ugc")


def build_default_authoring_templates() -> list[AuthoringTemplate]:
    """Build the built-in authoring templates."""
    # 所有模板均指向迁移后的 interactive_session 代表脚本；机制通过 game_pack 引入。
    return [
        AuthoringTemplate(
            game_type="social_deduction",
            script_name="deduction/werewolf.yaml",
            runtime_type="interactive_session",
            extensions=(),
            game_pack="builtin.social",
            keywords=("狼人", "身份", "推理", "social", "deduction", "夜晚", "投票"),
            required_questions=("几人游戏？", "有哪些隐藏身份？", "每轮有哪些白天/夜晚阶段？", "胜利条件是什么？"),
            optional_questions=("是否需要警长？", "是否允许遗言？", "是否有人类玩家参与？"),
            defaults={"players": 9, "runtime": "interactive_session"},
        ),
        AuthoringTemplate(
            game_type="word_guess",
            script_name="deduction/who_is_undercover.yaml",
            runtime_type="interactive_session",
            extensions=(),
            game_pack="builtin.social",
            keywords=("谁是卧底", "词语", "猜词", "卧底", "word", "guess"),
            required_questions=("平民词和卧底词如何生成？", "每轮发言几次？", "卧底是否有猜词机会？"),
            defaults={"players": 6, "runtime": "interactive_session"},
        ),
        AuthoringTemplate(
            game_type="card_game",
            script_name="cards/uno.yaml",
            runtime_type="interactive_session",
            extensions=("cards",),
            game_pack="builtin.cards",
            keywords=("卡牌", "UNO", "uno", "出牌", "摸牌", "deck", "card"),
            required_questions=("有哪些牌？", "初始手牌几张？", "摸牌和出牌规则是什么？", "胜利条件是什么？"),
            defaults={"players": 4, "runtime": "interactive_session"},
        ),
        AuthoringTemplate(
            game_type="board_game",
            script_name="board/gomoku.yaml",
            runtime_type="interactive_session",
            extensions=("board",),
            game_pack="builtin.board",
            keywords=("棋盘", "五子棋", "象棋", "围棋", "跳棋", "落子", "board"),
            required_questions=("棋盘尺寸是多少？", "玩家如何移动或落子？", "合法动作和胜利条件是什么？"),
            defaults={"players": 2, "runtime": "interactive_session"},
        ),
        AuthoringTemplate(
            game_type="map_economy",
            script_name="economy/monopoly.yaml",
            runtime_type="interactive_session",
            extensions=("board", "dice", "economy"),
            game_pack="builtin.economy",
            keywords=("大富翁", "地图", "经济", "资产", "交易", "骰子", "map", "economy"),
            required_questions=("地图有哪些格子？", "货币和资产规则是什么？", "交易/破产/胜利条件是什么？"),
            defaults={"players": 2, "runtime": "interactive_session"},
        ),
        AuthoringTemplate(
            game_type="story",
            script_name="story/text_adventure_interactive.yaml",
            runtime_type="interactive_session",
            extensions=(),
            game_pack="",
            keywords=("剧情", "冒险", "文字冒险", "galgame", "分支", "story", "adventure", "DND", "dnd", "跑团"),
            required_questions=("世界设定是什么？", "有哪些关键选择分支？", "有哪些结局？"),
            defaults={"players": 1, "runtime": "interactive_session"},
        ),
        AuthoringTemplate(
            game_type="group_chat",
            script_name="deduction/dynamic_schedule_discussion.yaml",
            runtime_type="interactive_session",
            extensions=(),
            game_pack="",
            keywords=("群聊", "多 Agent", "多agent", "圆桌", "讨论", "group_chat", "chat"),
            required_questions=("群聊主题是什么？", "有哪些参与者角色？", "最多讨论几轮？"),
            defaults={"players": 4, "runtime": "interactive_session"},
        ),
    ]

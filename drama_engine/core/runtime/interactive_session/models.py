"""Canonical models for the interactive_session runtime.

这些 dataclass 是 runtime 内部 IR，runner/executor 只读取这些对象。
Normalizer 负责把 legacy YAML 转成这些 canonical model，避免执行层散落旧语法判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from drama_engine.core.moderation.models import GuardRailSpec


@dataclass(slots=True)
class ScopeSpec:
    """消息域定义 / Message-domain declaration."""

    id: str = "public"
    visibility: str = "public"
    members: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        assert self.id, "scope.id 不能为空"
        assert self.visibility in {"public", "private"}, "scope.visibility 必须是 public 或 private"


@dataclass(slots=True)
class ParticipantsSpec:
    """参与者选择规格 / Participant selector spec."""

    spec: Any = field(default_factory=lambda: {"static": []})


@dataclass(slots=True)
class DynamicScheduleSpec:
    """动态子调度规格 / Dynamic child schedule spec."""

    enabled: bool = False
    check_on: str = "after_message"
    detector: dict[str, Any] = field(default_factory=dict)
    allowed: dict[str, Any] = field(default_factory=dict)
    patch: dict[str, Any] = field(default_factory=dict)
    merge_back: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate dynamic schedule lifecycle hook names."""
        assert self.check_on in {"after_message", "after_round"}, (
            "dynamic.check_on 必须是 after_message 或 after_round"
        )


@dataclass(slots=True)
class ScheduleSpec:
    """调度规格 / Schedule spec."""

    mode: str = "none"
    actor: Any = None
    order: dict[str, Any] = field(default_factory=dict)
    planner: dict[str, Any] = field(default_factory=dict)
    opening: Any = ""
    max_turns: int = 1
    max_rounds: int = 1
    timeout_ms: int | None = None
    stop_when: dict[str, Any] | None = None
    dynamic: DynamicScheduleSpec = field(default_factory=DynamicScheduleSpec)

    def __post_init__(self) -> None:
        valid_modes = {
            "none",
            "single",
            "sequential",
            "simultaneous",
            "random_order",
            "openchat",
            "loop_until",
        }
        assert self.mode in valid_modes, f"未知 schedule.mode: {self.mode}"
        assert self.max_turns > 0, "schedule.max_turns 必须是正整数"
        assert self.max_rounds > 0, "schedule.max_rounds 必须是正整数"


@dataclass(slots=True)
class ParticipantActionSpec:
    """参与者动作规格 / Participant action spec."""

    kind: str = "none"
    target: str = "none"
    candidates: dict[str, Any] | None = None
    response: dict[str, Any] = field(default_factory=dict)
    cue: Any = ""

    def __post_init__(self) -> None:
        valid_kinds = {"speak", "choose", "vote", "action", "form", "narration", "none"}
        assert self.kind in valid_kinds, f"未知 participant_action.kind: {self.kind}"
        assert self.target in {"none", "optional", "required"}, (
            "participant_action.target 必须是 none、optional 或 required"
        )


@dataclass(slots=True)
class ControllerActionSpec:
    """剧情控制动作规格 / Story controller action spec."""

    enabled: bool = False
    controller: dict[str, Any] = field(default_factory=lambda: {"type": "none"})
    kind: str = "none"
    choices: list[dict[str, Any]] = field(default_factory=list)
    free_input: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        valid_kinds = {"choice", "free_text", "narration", "cinematic", "none"}
        valid_controllers = {"human", "agent", "system", "plugin", "none"}
        assert self.kind in valid_kinds, f"未知 controller_action.kind: {self.kind}"
        controller_type = str(self.controller.get("type") or "none")
        assert controller_type in valid_controllers, f"未知 controller.type: {controller_type}"


@dataclass(slots=True)
class RefereeSpec:
    """裁判规格 / Referee spec."""

    enabled: bool = False
    check_on: list[str] = field(default_factory=lambda: ["after_scene"])
    include: Any = None
    exclude: Any = None
    rules: list[dict[str, Any]] = field(default_factory=list)
    executor: dict[str, Any] | None = None
    result: Any = None

    def __post_init__(self) -> None:
        """Validate referee lifecycle hook names."""
        allowed = {"after_scene", "after_message", "after_round", "after_generated_beat"}
        invalid = [item for item in self.check_on if item not in allowed]
        assert not invalid, f"referee.check_on 包含未知值: {invalid}"


@dataclass(slots=True)
class VisibilityPolicy:
    """实体属性级可见性策略 / Entity attribute-level visibility policy.

    回答的问题是「某实体的某个属性值对谁可见」，与 scope（消息发给谁）、
    candidate（能对谁行动）、participants（谁在场）是四个正交的维度。

    - secret_attrs：这些属性名对「他人」隐藏，只有属性所有者自己（actor view 的 self）
      与授权受众（host / referee）能看到。例如狼人杀里 role、faction。
    - self_visible：预留字段。为空表示「所有者自己可见其全部属性」（默认行为）；
      非空时可进一步收窄所有者自己能看到的属性范围（当前投影暂不强制裁剪，留待扩展）。

    未声明（secret_attrs 为空）时表示「无秘密、全部公开」，声明成为唯一事实来源。
    """

    secret_attrs: list[str] = field(default_factory=list)
    self_visible: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 断言：属性名必须是字符串，避免 YAML 写错类型导致后续遮蔽逻辑静默失效。
        assert all(isinstance(name, str) for name in self.secret_attrs), (
            "visibility.secret_attrs 必须全部是字符串属性名"
        )
        assert all(isinstance(name, str) for name in self.self_visible), (
            "visibility.self_visible 必须全部是字符串属性名"
        )


@dataclass(slots=True)
class SceneSpec:
    """Scene canonical IR."""

    id: str
    type: str = "scene"
    scope: ScopeSpec = field(default_factory=ScopeSpec)
    when: dict[str, Any] | None = None
    participants: ParticipantsSpec = field(default_factory=ParticipantsSpec)
    schedule: ScheduleSpec = field(default_factory=ScheduleSpec)
    participant_action: ParticipantActionSpec = field(default_factory=ParticipantActionSpec)
    controller_action: ControllerActionSpec = field(default_factory=ControllerActionSpec)
    resolution: dict[str, Any] = field(default_factory=dict)
    publication: dict[str, Any] = field(default_factory=dict)
    referee: RefereeSpec = field(default_factory=RefereeSpec)
    # scene 级 OOC 内容守卫；覆盖全局 guardrail。默认未启用。
    guardrail: GuardRailSpec = field(default_factory=GuardRailSpec)
    hooks: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.id, "scene.id 不能为空"


@dataclass(slots=True)
class FlowTransitionSpec:
    """状态机转移规格 / State-machine transition spec."""

    to: str
    when: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        assert self.to, "transition.to 不能为空"


@dataclass(slots=True)
class FlowStateSpec:
    """状态机节点规格 / Flow state spec."""

    id: str
    scenes: list[str] = field(default_factory=list)
    transitions: list[FlowTransitionSpec] = field(default_factory=list)
    entry_effects: list[dict[str, Any]] = field(default_factory=list)
    exit_effects: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False


@dataclass(slots=True)
class FlowSpec:
    """流程规格 / Flow spec."""

    type: str = "sequence"
    initial: str = "main"
    states: dict[str, FlowStateSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.type in {"sequence", "state_machine"}, "flow.type 必须是 sequence 或 state_machine"
        assert self.initial in self.states, f"flow.initial '{self.initial}' 不在 flow.states 中"


@dataclass(slots=True)
class InteractiveScript:
    """Compiled interactive_session script."""

    meta: dict[str, Any]
    runtime: Any
    flow: FlowSpec
    scenes: dict[str, SceneSpec]
    players: dict[str, Any] = field(default_factory=dict)
    # 角色定义列表，由顶层 roles: 块编译而来。每项含 name/display_name/description 等。
    roles: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    scopes: dict[str, ScopeSpec] = field(default_factory=dict)
    referee: RefereeSpec = field(default_factory=RefereeSpec)
    # 实体属性级可见性策略（哪些属性对他人隐藏），由顶层 visibility: 块编译而来。
    visibility: VisibilityPolicy = field(default_factory=VisibilityPolicy)
    # 全局 OOC 内容守卫，由顶层 guardrail: 块编译而来。scene 级可覆盖。
    guardrail: GuardRailSpec = field(default_factory=GuardRailSpec)
    plugins: list[dict[str, Any]] = field(default_factory=list)
    # 机制集合引用：{"plugin": "builtin.board", "config": {...}}，或其列表（引入多个集合）；
    # 不含规则本体。
    game_pack: dict[str, Any] | list[dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.scenes, "interactive_session 至少需要一个 scene"

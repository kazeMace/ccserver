"""Canonical models for the interactive_session runtime.

这些 dataclass 是 runtime 内部 IR，runner/executor 只读取这些对象。
Normalizer 负责把 legacy YAML 转成这些 canonical model，避免执行层散落旧语法判断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
        assert self.max_turns >= 0, "schedule.max_turns 必须是非负整数"
        assert self.max_rounds >= 0, "schedule.max_rounds 必须是非负整数"


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
        valid_kinds = {"choice", "free_text", "narration", "none"}
        assert self.kind in valid_kinds, f"未知 controller_action.kind: {self.kind}"


@dataclass(slots=True)
class RefereeSpec:
    """裁判规格 / Referee spec."""

    enabled: bool = False
    check_on: list[str] = field(default_factory=lambda: ["after_scene"])
    include: Any = None
    exclude: Any = None
    rules: list[dict[str, Any]] = field(default_factory=list)
    evaluator: dict[str, Any] | None = None
    result: Any = None

    def __post_init__(self) -> None:
        """Validate referee lifecycle hook names."""
        allowed = {"after_scene", "after_message", "after_round", "after_generated_beat"}
        invalid = [item for item in self.check_on if item not in allowed]
        assert not invalid, f"referee.check_on 包含未知值: {invalid}"


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
    hooks: dict[str, Any] = field(default_factory=dict)
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
    state: dict[str, Any] = field(default_factory=dict)
    scopes: dict[str, ScopeSpec] = field(default_factory=dict)
    referee: RefereeSpec = field(default_factory=RefereeSpec)
    plugins: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.scenes, "interactive_session 至少需要一个 scene"

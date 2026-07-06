"""Engine shared data / state / actor layer.

旧的固定流程编排层（Director / Sequence / dialogue policies / Stage / Narrator）已随
`interactive_session` 新架构上线而删除。本包现在只保留被新 runtime 与 DSL 组件复用的
共享层：世界状态 State、剧本数据模型 models、Actor 协议与 Cast。
The legacy fixed-flow orchestration has been removed; this package now only keeps the
shared data/state/actor layer reused by the interactive_session runtime and DSL components.
"""

from .actors import (
    ActorController,
    ActorPort,
    AgentActor,
    AgentActorController,
    ObservationBuffer,
    PerceptionFormatter,
    SeatActor,
    create_agent_actor,
)
from .cast import Cast
from .constants import MAX_COLLECT_RETRIES, MAX_LOOP_TURNS
from .human import (
    ActionRequest,
    ActionSubmission,
    HumanActorController,
    HumanInputPort,
    ServiceHumanInputPort,
    create_human_actor_from_port,
)
from .models import ActorProfile, PlayerConfig, Role, Scene, Scope, Script, Vocabulary
from .state import Link, SetAttr, State, StateWriter, Unlink, kill, revive

__all__ = [
    "ActionRequest",
    "ActionSubmission",
    "ActorController",
    "ActorPort",
    "ActorProfile",
    "AgentActor",
    "AgentActorController",
    "Cast",
    "HumanActorController",
    "HumanInputPort",
    "Link",
    "MAX_COLLECT_RETRIES",
    "MAX_LOOP_TURNS",
    "ObservationBuffer",
    "PerceptionFormatter",
    "PlayerConfig",
    "Role",
    "Scope",
    "Scene",
    "Script",
    "SeatActor",
    "SetAttr",
    "State",
    "StateWriter",
    "Unlink",
    "Vocabulary",
    "create_agent_actor",
    "create_human_actor_from_port",
    "ServiceHumanInputPort",
    "kill",
    "revive",
]

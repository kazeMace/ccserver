"""Script and domain data models for drama engine.

These classes are pure data declarations. They do not drive runtime behavior.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Vocabulary:
    """
    词汇表 — 声明本剧目使用的所有合法词汇。

    frozen=True 表示这个对象创建后就不能修改（不可变）。

    用途：
      1. 校验：写入 State 时检查 role/faction/scope/prop 是否在词汇表内，
         拼错了「werewololf」会立刻报错，而不是静默返回空集。
      2. 未来自然语言编译的靶子：编译器只需要在这个有限值域内理解玩家的话。

    参数：
      roles    — 角色名集合，如 {"werewolf", "seer", "villager"}
      factions — 阵营名集合，如 {"wolf", "good"}
      scopes   — 可见域名集合，如 {"public", "town", "wolf-den"}
      abilities — 能力概念名集合，如 {"vote", "heal", "shoot"}
      items    — 道具概念名集合，如 {"heal_potion", "poison_potion"}
      statuses — 状态概念名集合，如 {"alive", "dead"}
    """
    roles: frozenset      # 使用 frozenset 而不是 set，配合 frozen=True
    factions: frozenset
    scopes: frozenset
    abilities: frozenset
    items: frozenset = frozenset()
    statuses: frozenset = frozenset()


@dataclass(frozen=True)
class Role:
    """
    角色定义（戏服）— 描述一个角色「是谁、能做什么、能听什么」。

    只有数据，没有行为。Actor（表演者）和 Role（角色）是解耦的：
    同一个 Actor 可以扮演任意 Role，换剧本不动 Actor。

    参数：
      name     — 角色名，如 "werewolf"
      brief    — 私密身份说明，开场时注入给 Actor 的 system 内容
      scopes   — 穿上这件戏服后自动订阅的可见域列表，如 ["public", "wolf-den"]
      abilities — 角色拥有的能力概念，如 ["vote", "heal"]
      faction  — 所属阵营，如 "wolf" 或 "good"，空字符串表示无阵营
    """
    name: str
    brief: str
    scopes: list
    abilities: list
    faction: str = ""
    display_name: str = ""
    inventory: list = None


@dataclass(frozen=True)
class ActorProfile:
    """
    Actor 身份档案 — 描述“这个参与者在本局是谁”。

    ActorProfile 是稳定上下文，不是场上事件。AI Actor 每次行动时都会看到它；
    真人玩家则通过 UI 看到它。普通 observation 只表示“刚刚听到/看到的消息”。

    参数：
      actor_name        — 玩家/Actor 的公开 ID，如 "Player_12"
      display_name      — 展示名，默认为 actor_name
      nickname          — 可选昵称
      role_name         — 角色 ID，如 "guard"
      role_display_name — 角色展示名，如 "守卫"
      faction           — 阵营 ID，可为空
      brief             — 角色私密说明
      role_context      — 由 concepts 生成的角色/阵营/能力解释
    """
    actor_name: str
    display_name: str
    nickname: str
    role_name: str
    role_display_name: str
    faction: str
    brief: str
    role_context: str = ""

    def render_for_prompt(self) -> str:
        """
        渲染给 AI Actor 的稳定身份上下文。

        返回：
          可直接放在模型请求顶部的文本。
        """
        assert self.actor_name, "ActorProfile.actor_name 不能为空"
        role_label = self.role_display_name or self.role_name or "未分配角色"
        lines = [
            "【身份档案】",
            f"你是 {self.actor_name}。",
            f"你的角色是【{role_label}】。",
            f"当你说“我”“自己”“本玩家”时，指的是 {self.actor_name}。",
        ]
        if self.display_name and self.display_name != self.actor_name:
            lines.append(f"你的展示名是 {self.display_name}。")
        if self.nickname:
            lines.append(f"你的昵称是 {self.nickname}。")
        if self.faction:
            lines.append(f"你的阵营是 {self.faction}。")
        if self.brief:
            lines.append("")
            lines.append("【角色说明】")
            lines.append(self.brief)
        if self.role_context:
            lines.append("")
            lines.append(self.role_context)
        return "\n".join(lines)


@dataclass(frozen=True)
class PlayerConfig:
    """
    玩家席位配置 — 描述本剧目有哪些玩家席位，以及这些席位的默认资料。

    Player 是「席位/参与者资料」，Actor 是运行时执行体，Role 是剧本身份。
    这三者分开后，Director 就不需要把狼人杀的 alive=True 写死在引擎层。
    """
    count: int
    ids: list
    display_names: dict
    nicknames: dict
    initial_attrs: dict


@dataclass
class Scope:
    """
    可见域 — 定义「谁能听到这个信道的消息」。

    这是本框架的核心差异化设计：
    - WolfAgent 用 [X ONLY] 文本前缀靠模型自觉
    - 我们用 Scope 的路由强制隔离，私密消息物理上进不了别人的上下文

    参数：
      name     — 可见域名称，如 "wolf-den"
      members  — 一个函数：def fn(state) -> set[str]
                 给定当前世界状态，返回这个域当前成员的 Actor 名集合。
                 设计成函数（而非固定集合）是为了「拉取式」更新：
                 每次需要成员名单时重新求值，死者自动消失，无需手动同步。
      delivery — "immediate"：每条消息立刻投递
                 "deferred"：先收集完本幕所有发言，再统一公布（防止跟票）
    """
    name: str
    members: Any          # 类型是 Callable[[State], set[str]]，这里用 Any 方便新人阅读
    delivery: str = "immediate"   # "immediate" 或 "deferred"


@dataclass
class Scene:
    """
    一幕 — 声明「在哪个信道、谁上场、旁白说什么、怎么轮流、收什么动作、改什么状态」。

    参数：
      name       — 幕名，如 "wolf-vote"，用于日志和调试
      scope      — 在哪个 Scope 演（字符串，对应 Script.scopes 中的某个名称）
      participants — 函数：def fn(state) -> set[str]，返回本幕参与者名字集合。
                     返回空集时，Director 会跳过这幕（空场跳幕机制）。
      cue        — 任务提示：可以是字符串，也可以是函数 def fn(state) -> str。
                   函数形式允许提示词随状态变化（如平安夜公告内容不同）。
      announce_response_cue — 是否把 cue 作为主持人喊话投递到 scene.scope。
                              True 表示公开/域内报幕；False 表示 cue 只进入行动者私密任务。
      dialogue_policy — DialoguePolicy/TurnPolicy 实例，决定参与者如何轮流发言。
      response_model  — Pydantic Model class 或 None。
                        非 None 时要求 Actor 必须产出符合该 Model 的结构化 JSON。
      response_prompt — 结构化输出的额外提示，只发送给本幕行动者。
                        Extra prompt for structured output, sent only to actors.
      on_result  — 函数：def fn(responses, state, writer) -> None
                   本幕所有人发言结束后调用，用来改世界状态。
                   例如统计投票结果、记录死亡等。
      when       — 函数：def fn(state) -> bool 或 None
                   非 None 时，在空场检查前决定本幕是否触发。
      until      — 函数：def fn(state) -> bool 或 None
                   非 None 时，每轮发言后检查是否应提前结束本幕。
    """
    name: str
    scope: str
    participants: Any       # Callable[[State], set[str]]
    cue: Any                # str 或 Callable[[State], str]
    dialogue_policy: Any    # DialoguePolicy/TurnPolicy 实例
    response_model: Any = None   # type[BaseModel] 或 None
    response_prompt: str = "" # response.prompt 额外输出要求 / extra output requirement
    candidates: Any = None # Callable[[State], list[str]] 或 None
    candidate_constraints: dict | None = None # 多目标候选约束，如 count/distinct
    on_result: Any = None # Callable[[list, State, StateWriter], None] 或 None
    when: Any = None      # Callable[[State], bool] 或 None
    until: Any = None     # Callable[[State], bool] 或 None
    display_name: str = "" # 给用户和 Agent 看的幕名；为空时使用 name
    announce_response_cue: bool = True # 是否把 cue 作为主持人喊话投递到 scene.scope
    response_messages: Any = None  # Actor 响应如何渲染并投递到 self/scope/observer/debug
    publication: dict | None = None # 主持人公告、前端视图、披露声明


@dataclass
class Script:
    """
    剧本 — 描述一整出戏的完整数据。

    这是框架「数据驱动」设计的核心：
    换游戏 = 换一份 Script 数据，Director（导演）代码一行不改。

    参数：
      vocab    — 词汇表，供校验和未来自然语言编译用
      roles    — 角色列表 list[Role]
      casting  — 选角策略实例（ShuffleDeal / FixedDeal 等）
      scopes   — 可见域列表 list[Scope]
      flow     — 流程实例（Sequence 等），决定幕场顺序
      referee  — 裁判函数：def fn(state) -> str | None
                 返回非 None 时表示胜负已分（返回值是公告文本）
      concepts — 概念解释字典，供 Actor 上下文、UI 和未来自然语言编译使用
      triggers — 脚本级事件触发器，基于 mutation log 执行通用 effects
    """
    vocab: Vocabulary
    roles: list
    casting: Any
    scopes: list
    flow: Any
    referee: Any          # Callable[[State], str | None]
    player_config: Any = None
    concepts: Any = None
    triggers: Any = None
    plugins: Any = None
    extensions: Any = None
    plugin_registry: Any = None
    runtime: Any = None  # RuntimeSpec；默认由 compiler 填 game_session
    game_pack: Any = None
    rule_set: Any = None
    publish: Any = None

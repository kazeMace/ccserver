"""World state, mutations, and the single write gateway."""

from dataclasses import dataclass
from typing import Any

from .models import Vocabulary


@dataclass(frozen=True)
class SetAttr:
    """
    变更指令：给某个实体的某个属性赋值。

    例子：
      SetAttr("Player1", "alive", False)   # 表示 Player1 死亡
      SetAttr("Player1", "role", "seer")   # 表示 Player1 的角色是预言家

    为什么用数据而不是方法：
      - 可以记录到日志，事后回放整局游戏
      - 新增游戏动词（如「禁言」）只需要新增 SetAttr 调用，不用改 StateWriter
    """
    entity: str   # 实体名（Actor 名），如 "Player1"
    key: str      # 属性名，如 "alive"
    value: Any    # 新值，如 False


@dataclass(frozen=True)
class Link:
    """
    变更指令：建立两个实体之间的关系。

    例子：
      Link("teammate", "Player1", "Player2")   # Player1 和 Player2 是队友
      Link("lover", "Player3", "Player4")      # 恋人关系（剧本杀/galgame 等）

    v0.1 预留接口，狼人杀用属性替代，需要时再启用。
    """
    relation: str   # 关系名，如 "teammate"
    a: str          # 关系的起点实体
    b: str          # 关系的终点实体


@dataclass(frozen=True)
class Unlink:
    """
    变更指令：删除两个实体之间的关系。

    relation/source/target 任一维度可为 None，表示通配：
      Unlink("lover", "P1", None)  # 删除 P1 发出的所有 lover 边
      Unlink("lover", None, None)  # 清空全部 lover 边
    """
    relation: str
    a: str | None = None
    b: str | None = None


class State:
    """
    世界状态 — 存储剧目当前的全部事实。

    设计为「开放属性存储」：不写死字段，用字典存任意属性。
    这样换游戏（谁是卧底的 identity+word、海龟汤的 solved）
    只需要加属性，不改 State 类本身。

    与 WolfAgent 的 Players 对象相比：
    - Players：有 werewolves/villagers/seer 等固定角色桶，换游戏必须改类
    - State：开放属性字典 + 查询函数，换游戏只加属性

    使用方式：
      state.having(alive=True, role="werewolf")  # 找所有活着的狼人
      state.get_attr("Player1", "alive", True)   # 读 Player1 的存活状态
    """

    def __init__(self, vocab: Vocabulary):
        """
        初始化世界状态。

        参数：
          vocab — 词汇表，用于写入时校验属性值合法性
        """
        # 实体属性存储：实体名 -> 属性字典
        # 例如：{"Player1": {"alive": True, "role": "werewolf"}, ...}
        self._attrs: dict = {}

        # 关系存储：(关系名, 起点, 终点) 的集合
        # 例如：{("teammate", "Player1", "Player2")}
        self._relations: set = set()

        # 变更日志：所有 Mutation 按时间顺序追加
        # 用于事件溯源、回放、综艺型「基于历史派生下一幕」
        self._log: list = []

        # 词汇表，供写入时校验
        self._vocab: Vocabulary = vocab

    def register_entity(self, name: str, initial_attrs: dict = None):
        """
        注册一个新实体（通常是开场时注册所有 Actor）。

        参数：
          name          — 实体名，如 "Player1"
          initial_attrs — 初始属性字典，如 {"alive": True, "role": "villager"}
        """
        assert name not in self._attrs, f"实体 '{name}' 已经存在，不能重复注册"
        self._attrs[name] = initial_attrs.copy() if initial_attrs else {}
        print(f"[State] 注册实体：{name}，初始属性：{self._attrs[name]}")

    def having(self, **conditions) -> set:
        """
        查询：返回满足所有条件的实体名集合。

        参数：
          **conditions — 属性名=值 的键值对，如 alive=True, role="werewolf"

        返回：
          满足全部条件的实体名集合，如 {"Player1", "Player3"}

        例子：
          state.having(alive=True)                    # 所有活人
          state.having(alive=True, role="werewolf")   # 所有活着的狼人
        """
        result = set()
        for name, attrs in self._attrs.items():
            # 检查是否所有条件都满足
            all_match = True
            for key, value in conditions.items():
                if attrs.get(key) != value:
                    all_match = False
                    break
            if all_match:
                result.add(name)
        return result

    def where(self, pred) -> set:
        """
        查询：用自定义谓词函数过滤实体。

        参数：
          pred — 函数：def fn(attrs: dict) -> bool
                 接收单个实体的属性字典，返回是否满足条件

        返回：
          满足谓词的实体名集合

        例子：
          def died_this_round(attrs):
              return (not attrs.get("alive", True)) and attrs.get("death_round") == 1
          state.where(died_this_round)
        """
        result = set()
        for name, attrs in self._attrs.items():
            if pred(attrs):
                result.add(name)
        return result

    def related(self, relation: str, who: str) -> set:
        """
        查询：返回与指定实体有某种关系的实体集合。

        参数：
          relation — 关系名，如 "teammate"
          who      — 起点实体名

        返回：
          与 who 有 relation 关系的实体名集合
        """
        result = set()
        for (rel, a, b) in self._relations:
            if rel == relation and a == who:
                result.add(b)
        return result

    def has_entity(self, name: str) -> bool:
        """
        检查实体是否已注册。

        参数：
          name — 实体名
        返回：
          bool — 实体是否存在
        """
        return name in self._attrs

    def mutation_log(self) -> list:
        """
        返回变更日志副本。

        返回：
          list — 已应用 Mutation 的时间顺序列表
        """
        return list(self._log)

    def all_entities(self) -> set:
        """
        返回所有已注册的实体名集合（含死者）。

        注意：函数名用 all_entities 而不是 all，因为 all 是 Python 内置函数名。

        返回：
          所有实体名集合，如 {"Player1", "Player2", ..., "Player9"}
        """
        return set(self._attrs.keys())

    def get_attr(self, name: str, key: str, default: Any = None) -> Any:
        """
        读取某个实体的某个属性值。

        参数：
          name    — 实体名，如 "Player1"
          key     — 属性名，如 "alive"
          default — 属性不存在时的默认值

        返回：
          属性值，如 True
        """
        entity_attrs = self._attrs.get(name, {})
        return entity_attrs.get(key, default)

    def _apply(self, mutation) -> None:
        """
        内部写入方法 — 只应由 StateWriter 调用，外部代码不要直接调用。

        参数：
          mutation — SetAttr 或 Link 实例
        """
        if isinstance(mutation, SetAttr):
            # 确保实体已注册
            assert mutation.entity in self._attrs, (
                f"实体 '{mutation.entity}' 未注册，无法设置属性"
            )
            self._attrs[mutation.entity][mutation.key] = mutation.value

        elif isinstance(mutation, Link):
            # 添加关系边
            assert mutation.a in self._attrs, f"关系起点实体 '{mutation.a}' 未注册"
            assert mutation.b in self._attrs, f"关系终点实体 '{mutation.b}' 未注册"
            self._relations.add((mutation.relation, mutation.a, mutation.b))

        elif isinstance(mutation, Unlink):
            # 删除匹配的关系边。None 表示该维度不限制。
            kept = set()
            for (rel, a, b) in self._relations:
                same_relation = rel == mutation.relation
                same_source = mutation.a is None or a == mutation.a
                same_target = mutation.b is None or b == mutation.b
                if same_relation and same_source and same_target:
                    continue
                kept.add((rel, a, b))
            self._relations = kept

        else:
            raise ValueError(f"未知的 Mutation 类型：{type(mutation)}")

        # 追加到事件日志
        self._log.append(mutation)

    def snapshot(self) -> dict:
        """
        返回当前状态的快照（用于调试打印）。

        返回：
          状态字典的副本（只含实体属性，不含关系）
        """
        return {name: attrs.copy() for name, attrs in self._attrs.items()}

    def full_snapshot(self) -> dict:
        """
        返回可完整恢复的深快照，供 checkpoint / 回滚使用。

        与 snapshot() 不同，本方法同时包含实体属性和关系，restore() 可据此
        把状态完整还原到快照时刻。

        返回：
          {"attrs": {实体: {属性: 值}}, "relations": [[关系名, 起点, 终点], ...]}
        """
        import copy

        return {
            "attrs": {name: copy.deepcopy(attrs) for name, attrs in self._attrs.items()},
            "relations": [list(rel) for rel in self._relations],
        }

    def restore(self, snapshot: dict) -> None:
        """
        从 full_snapshot() 的深快照恢复状态。

        会整体替换实体属性、关系和变更日志（日志清空，因为回滚后从 checkpoint
        重新累积）。词汇表不变。

        参数：
          snapshot — full_snapshot() 返回的字典
        """
        import copy

        assert isinstance(snapshot, dict), "snapshot 必须是 dict"
        attrs = snapshot.get("attrs") or {}
        relations = snapshot.get("relations") or []
        assert isinstance(attrs, dict), "snapshot.attrs 必须是 dict"
        self._attrs = {name: copy.deepcopy(value) for name, value in attrs.items()}
        self._relations = {tuple(rel) for rel in relations}
        self._log = []


class StateWriter:
    """
    状态写入口 — 改写 State 的唯一合法途径。

    所有改状态的操作都必须经过这个类的 apply() 方法：
    - 做合法性断言（实体存在、属性名在词汇表内等）
    - 写入 State
    - 记录到事件日志（供回放）
    - 打印日志（供调试）
    - 未来：发 EventBus 事件（v0.1 用 print 代替）

    使用方式：
      writer.apply(SetAttr("Player1", "alive", False))
      # 或者用便利函数：
      kill(writer, "Player1")
    """

    def __init__(self, state: State):
        """
        初始化状态写入口。

        参数：
          state — 要写入的 State 对象
        """
        self._state = state

    def apply(self, mutation) -> None:
        """
        应用一个变更。

        这是改写 State 的唯一合法入口。

        参数：
          mutation — SetAttr 或 Link 实例，描述要做什么变更
        """
        # 打印变更日志，方便调试
        print(f"[StateWriter] 应用变更：{mutation}")

        # 委托给 State 内部方法执行真正的写入
        self._state._apply(mutation)

        # TODO v0.1+：向 EventBus 发送变更事件，供 monitor/recorder 消费
        # self._bus.emit(mutation)


# ── 便利函数（不是 StateWriter 的方法，是独立的函数）──────────────────────
# 把常见的游戏动词封装成具名函数，方便 on_result 里调用。
# 新增游戏动词只需新增一个函数，不改 StateWriter（开闭原则）。

def kill(writer: StateWriter, name: str) -> None:
    """
    便利函数：标记某人死亡。

    参数：
      writer — StateWriter 实例
      name   — 要标记死亡的实体名，如 "Player1"
    """
    writer.apply(SetAttr(name, "alive", False))


def revive(writer: StateWriter, name: str) -> None:
    """
    便利函数：复活某人（女巫救人等场景）。

    参数：
      writer — StateWriter 实例
      name   — 要复活的实体名
    """
    writer.apply(SetAttr(name, "alive", True))

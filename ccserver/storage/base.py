"""
storage.base — StorageAdapter 聚合接口 + 通用工具。

StorageAdapter 是"全能聚合接口"，继承 storage.interfaces 中按领域拆分的全部
细接口（SessionStore / ConversationStore / TaskStore / TeamStore / MailboxStore
/ CronStore）。所有方法定义都在各细接口中，本类不再重复声明，仅做组合。

为什么保留 StorageAdapter：
  现有大量代码依赖 `StorageAdapter` 作为继承基类和类型注解
  （FileStorageAdapter / SQLiteStorageAdapter / MongoStorageAdapter /
   CachedStorageAdapter / SessionManager 等）。保留聚合接口可零成本向后兼容。

新代码建议：
  只关心单一领域的代码（如缓存装饰器、只读会话服务）应针对最小细接口编程，
  例如 `def __init__(self, store: SessionStore)`，而非依赖整个 StorageAdapter，
  这正是接口隔离原则（ISP）的目的。
"""

from datetime import datetime
from pathlib import Path

from loguru import logger

# 细接口与纯数据结构的唯一定义点在 interfaces.py，这里重新导出以兼容旧 import 路径。
from .interfaces import (
    SessionRecord,
    SessionStore,
    ConversationStore,
    TaskStore,
    TeamStore,
    MailboxStore,
    CronStore,
)


def _json_default(obj):
    """
    JSON 序列化的 fallback：对不可 JSON 原生类型的对象保留结构化信息。

    处理优先级：
      1. datetime → ISO 格式字符串
      2. Path → str
      3. 其他 → 记录 warning，返回 "<unserializable:类型名>" 标记

    为什么不用 default=str：
      str() 会静默吞掉原始类型信息，反序列化后调用方无法区分
      "这是一个字符串" 和 "一个被强制转字符串的 datetime 对象"。
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    type_name = f"{type(obj).__module__}.{type(obj).__name__}"
    logger.warning("JSON serialization fallback | type={}", type_name)
    return f"<unserializable:{type_name}>"


class StorageAdapter(
    SessionStore,
    ConversationStore,
    TaskStore,
    TeamStore,
    MailboxStore,
    CronStore,
):
    """
    全能聚合存储接口：组合全部领域细接口。

    继承本类等价于"承诺支持所有存储领域"，但除 SessionStore 外的方法
    都有 NotImplementedError 默认实现，后端按需覆盖即可——行为与拆分前一致。

    若某后端只需支持部分领域，更推荐直接继承所需的细接口
    （如 `class XxxStore(SessionStore)`），以遵循接口隔离原则。
    """


__all__ = [
    "SessionRecord",
    "StorageAdapter",
    "SessionStore",
    "ConversationStore",
    "TaskStore",
    "TeamStore",
    "MailboxStore",
    "CronStore",
    "_json_default",
]

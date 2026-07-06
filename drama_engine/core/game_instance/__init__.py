"""GameInstance 聚合根与会话控制层。

本包是 Drama Engine 新架构的核心：
- `SessionState` / `SeatState`：会话过程状态（座位、生命周期、进度、timeline cursor、checkpoint）。
- `ProgressState`：当前 flow/scene/round/turn/phase 进度。
后续阶段会在此包补充 GameInstance 门面、SessionControl、SnapshotManager、RollbackManager。

导出保持惰性，避免在加载底层原语时就拉起完整门面。
"""

_EXPORT_MODULES = {
    "SessionState": "drama_engine.core.game_instance.state",
    "SeatState": "drama_engine.core.game_instance.state",
    "ProgressState": "drama_engine.core.game_instance.state",
    "all_session_statuses": "drama_engine.core.game_instance.state",
    "SESSION_LOBBY": "drama_engine.core.game_instance.state",
    "SESSION_ASSIGNED": "drama_engine.core.game_instance.state",
    "SESSION_RUNNING": "drama_engine.core.game_instance.state",
    "SESSION_PAUSED": "drama_engine.core.game_instance.state",
    "SESSION_ENDED": "drama_engine.core.game_instance.state",
    "SESSION_FAILED": "drama_engine.core.game_instance.state",
    "SESSION_TERMINATED": "drama_engine.core.game_instance.state",
    "CONTROLLER_AI": "drama_engine.core.game_instance.state",
    "CONTROLLER_HUMAN": "drama_engine.core.game_instance.state",
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str):
    """按需加载 game_instance 导出符号。"""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    from importlib import import_module

    module = import_module(module_name)
    return getattr(module, name)

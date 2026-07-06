"""Session layer public API.

Exports are lazy to avoid importing the session registry while lower-level
runtime primitives are still loading.
"""

_EXPORT_MODULES = {
    "GameRuntime": "drama_engine.core.session.runtime",
    "SessionState": "drama_engine.core.game_instance.state",
    "JsonSessionStore": "drama_engine.core.session.persistence",
    "PlayerClaim": "drama_engine.core.session.tokens",
    "PlayerTokenService": "drama_engine.core.session.tokens",
    "RuntimeLifecycleHooks": "drama_engine.core.session.lifecycle",
    "RuntimeState": "drama_engine.core.session.lifecycle",
    "SeatState": "drama_engine.core.game_instance.state",
    "ServicePorts": "drama_engine.core.session.ports",
    "ServiceSessionControls": "drama_engine.core.session.controls",
    "SessionEventStore": "drama_engine.core.session.events",
    "SessionRegistry": "drama_engine.core.session.registry",
    "SummaryProvider": "drama_engine.core.session.summary",
    "WebStepGate": "drama_engine.core.session.step_gate",
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str):
    """Load session exports on demand."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    from importlib import import_module

    module = import_module(module_name)
    return getattr(module, name)

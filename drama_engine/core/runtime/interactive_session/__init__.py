"""Interactive session runtime package.

这个 package 实现 `runtime.type: interactive_session`。
It keeps the new runtime independent from `game_session` while reusing shared
DSL components such as conditions, candidates, effects, actors, and session
ports.
"""

from drama_engine.core.runtime.interactive_session.runner import (
    InteractiveSessionExecutionModel,
    InteractiveSessionRunner,
)

__all__ = ["InteractiveSessionExecutionModel", "InteractiveSessionRunner"]

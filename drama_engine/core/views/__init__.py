"""视图投影层（ViewProjector，架构文档 §17）。

对外视图（host/player/public/audience）统一走 ViewProjector；runner.summary() 仅保留
内部调试用途。当前 ViewProjector 复用已有 view_projection 快照实现，作为统一入口，
后续可在此扩展 HTML 视图与领域视图。
"""

from drama_engine.core.views.projector import ViewProjector

__all__ = ["ViewProjector"]

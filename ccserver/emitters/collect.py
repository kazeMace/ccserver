from .base import BaseEmitter


class CollectEmitter(BaseEmitter):
    """
    将所有事件收集到内存中。
    用于普通 HTTP（非流式）响应。
    """

    def __init__(self):
        self.events: list[dict] = []

    async def emit(self, event: dict) -> None:
        self.events.append(event)

    def get_final_text(self) -> str:
        for e in reversed(self.events):
            if e["type"] == "done":
                return e.get("content", "")
        return ""

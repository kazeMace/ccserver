// 消息流容器：渲染消息列表 + 自动滚到底。供 Player/Host/Viewer 复用。

import { useEffect, useRef } from "react";
import type { InteractionMessage } from "../types/interaction";
import { Message } from "./Message";

export function MessageFeed({ messages, selfSeat }: { messages: InteractionMessage[]; selfSeat?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);
  return (
    <div className="feed" ref={ref}>
      <div className="feed-inner">
        {messages.length === 0 ? <div className="empty-hint">暂无消息</div> : null}
        {messages.map((m) => (
          <Message key={m.seq} msg={m} selfSeat={selfSeat} />
        ))}
      </div>
    </div>
  );
}

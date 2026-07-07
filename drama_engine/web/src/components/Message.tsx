// 消息渲染（§9 展示轴）。降级链：card.variant→card.kind→role→body.text。
// 先尝试富卡片注册表；无匹配卡片则按 role 布局；role 布局本身以 body.text 为终极兜底。

import type { InteractionMessage } from "../types/interaction";
import { resolveCardRenderer } from "../registry/cards";

export function Message({ msg, selfSeat }: { msg: InteractionMessage; selfSeat?: string }) {
  // 1. 富卡片优先（variant→kind）。
  const cards = msg.body.cards ?? [];
  for (const card of cards) {
    const Renderer = resolveCardRenderer(card);
    if (Renderer) {
      // 把主文本合流进 card.data，供 portrait_line 等使用。
      return <div className="msg"><Renderer card={{ ...card, data: { text: msg.body.text, ...card.data } }} /></div>;
    }
  }

  // 2. 按 role 布局兜底。
  return <div className="msg">{renderByRole(msg, selfSeat)}</div>;
}

function renderByRole(msg: InteractionMessage, selfSeat?: string) {
  const text = msg.body.text;
  switch (msg.role) {
    case "system":
      return <div className={`m-announce${msg.body.style === "announcement" ? " strong" : ""}`}>{text}</div>;
    case "referee":
      return <div className="m-announce strong">{text}</div>;
    case "narrator":
      return <div className="m-narrate">{text}</div>;
    case "secret":
      return (
        <div className="m-secret">
          <div className="secret-head">🔒 私密信息</div>
          <div className="secret-body">{text}</div>
        </div>
      );
    case "system_meta":
      return <div className="m-announce">⚙️ {text}</div>;
    case "dialogue":
    default:
      return <ChatBubble msg={msg} selfSeat={selfSeat} />;
  }
}

function ChatBubble({ msg, selfSeat }: { msg: InteractionMessage; selfSeat?: string }) {
  const s = msg.sender;
  const isSelf = s?.kind === "human" && (s.id === "me" || (selfSeat != null && s.id === selfSeat));
  const whisper = msg.scope.startsWith("group:") || msg.body.style === "whisper";
  return (
    <div className={`m-chat${isSelf ? " self" : ""}`}>
      <div className={`avatar${s?.dead ? " dead" : ""}`}>{s?.emoji ?? "🙂"}</div>
      <div className="m-body">
        {!isSelf && (
          <div className="m-name">
            {s?.name ?? "?"}
            {s?.role ? <span className="m-role-tag">{s.role}</span> : null}
          </div>
        )}
        <div className={`bubble${whisper ? " whisper" : ""}`}>{msg.body.text}</div>
      </div>
    </div>
  );
}

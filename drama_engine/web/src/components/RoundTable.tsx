// 圆桌视图（移植旧 host 圆桌）：左右两列座位 + 当前发言气泡。
// 数据来自 StateView.players + 最近一条 dialogue 消息。

import type { InteractionMessage, PlayerCard } from "../types/interaction";

export function RoundTable({ players, lastSpeech }: { players: PlayerCard[]; lastSpeech?: InteractionMessage | null }) {
  const half = Math.ceil(players.length / 2);
  const left = players.slice(0, half);
  const right = players.slice(half);
  const speakerId = lastSpeech?.sender?.id;
  return (
    <div className="round-table" style={{ display: "flex", justifyContent: "space-between", gap: 16, padding: 16 }}>
      <div className="rt-col" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {left.map((p) => <SeatChip key={p.seat_id} p={p} speaking={p.seat_id === speakerId} bubble={p.seat_id === speakerId ? lastSpeech?.body.text : undefined} side="left" />)}
      </div>
      <div className="rt-center" style={{ alignSelf: "center", color: "var(--text-lo)", fontSize: 12 }}>🎲 圆桌</div>
      <div className="rt-col" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {right.map((p) => <SeatChip key={p.seat_id} p={p} speaking={p.seat_id === speakerId} bubble={p.seat_id === speakerId ? lastSpeech?.body.text : undefined} side="right" />)}
      </div>
    </div>
  );
}

function SeatChip({ p, speaking, bubble, side }: { p: PlayerCard; speaking?: boolean; bubble?: string; side: "left" | "right" }) {
  return (
    <div style={{ display: "flex", flexDirection: side === "right" ? "row-reverse" : "row", alignItems: "center", gap: 8, opacity: p.alive === false ? 0.45 : 1 }}>
      <div className={`avatar${p.alive === false ? " dead" : ""}`} style={{ boxShadow: speaking ? "0 0 0 2px var(--accent)" : undefined }}>
        {p.emoji ?? "🙂"}
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 500 }}>{p.name ?? p.seat_id}</div>
        {bubble ? (
          <div className="bubble" style={{ maxWidth: 220, fontSize: 12.5, marginTop: 2 }}>
            {bubble.length > 60 ? bubble.slice(0, 60) + "…" : bubble}
          </div>
        ) : p.tag_text ? (
          <div style={{ fontSize: 11, color: "var(--text-lo)" }}>{p.tag_text}</div>
        ) : null}
      </div>
    </div>
  );
}

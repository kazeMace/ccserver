// 富卡片渲染注册表（§9 展示轴降级链）。
// 查表顺序：card.variant → card.kind → （交回 Message 用 role 兜底）→ body.text。
// 新增游戏卡片皮肤 = 在 CARD_REGISTRY 加一条，miss 自动降级，不改 Message。

import type { RichCard } from "../types/interaction";

export interface CardRendererProps {
  card: RichCard;
}

type CardRenderer = (props: CardRendererProps) => JSX.Element;

// —— 内置卡片渲染器（对应原型的 vn/clue/secret/affinity + 狼人杀皮肤）——

function PortraitLineCard({ card }: CardRendererProps) {
  const d = card.data as { name?: string; emoji?: string };
  return (
    <div className="m-vn">
      <div className="portrait">{d.emoji ?? "🙂"}</div>
      <div className="vn-body">
        <div className="vn-name">{d.name ?? ""}</div>
        <div className="vn-text">{(card.data.text as string) ?? ""}</div>
      </div>
    </div>
  );
}

function ClueCard({ card }: CardRendererProps) {
  const d = card.data as { title?: string; desc?: string; foot?: string };
  return (
    <div className="m-clue">
      <div className="clue-head">🔍 线索</div>
      <div className="clue-title">{d.title}</div>
      <div className="clue-desc">{d.desc}</div>
      {d.foot ? <div className="clue-foot"><span>{d.foot}</span></div> : null}
    </div>
  );
}

function SecretCard({ card }: CardRendererProps) {
  const d = card.data as { head?: string; body?: string };
  return (
    <div className="m-secret">
      <div className="secret-head">🔒 {d.head ?? "私密信息"}</div>
      <div className="secret-body">{d.body}</div>
    </div>
  );
}

function AffinityCard({ card }: CardRendererProps) {
  const d = card.data as { text?: string; dir?: "up" | "down" };
  return <div className={`m-affinity${d.dir === "down" ? " down" : ""}`}>{d.text}</div>;
}

function MediaCard({ card }: CardRendererProps) {
  const d = card.data as {
    kind?: "video" | "audio" | "image";
    title?: string;
    url?: string;
    poster?: string;
    subtitleUrl?: string;
    subtitle_url?: string;
    autoplay?: boolean;
  };
  if (!d.url) return <div className="m-announce">媒体资源缺少 url</div>;
  const subtitleUrl = d.subtitleUrl ?? d.subtitle_url;
  // 媒体类型：优先从 card.data.kind 读，兜底用 card.kind（后端 video/audio/image 直接作为 card.kind）
  const mediaKind = d.kind ?? card.kind;
  return (
    <div className="m-media">
      {d.title ? <div className="media-title">{d.title}</div> : null}
      {mediaKind === "image" ? (
        <img src={d.url} alt={d.title ?? "media"} />
      ) : mediaKind === "audio" ? (
        <audio src={d.url} controls preload="metadata" />
      ) : (
        <video src={d.url} poster={d.poster} controls preload="metadata" playsInline autoPlay={Boolean(d.autoplay)}>
          {subtitleUrl ? <track kind="subtitles" src={subtitleUrl} srcLang="zh" label="字幕" /> : null}
        </video>
      )}
    </div>
  );
}

// 狼人杀皮肤示例：验人结果 / 死亡公告（§9.5）。
function SeerResultCard({ card }: CardRendererProps) {
  const d = card.data as { target?: string; verdict?: string };
  const good = d.verdict === "good";
  return (
    <div className="m-secret">
      <div className="secret-head">🔮 查验结果</div>
      <div className="secret-body">
        {d.target} 的身份是：<strong style={{ color: good ? "var(--success)" : "var(--danger)" }}>{good ? "好人（金水）" : "狼人"}</strong>
      </div>
    </div>
  );
}

function DeathNoticeCard({ card }: CardRendererProps) {
  const d = card.data as { dead_seats?: string[]; text?: string };
  return <div className="m-announce strong">☠️ {d.text ?? `${(d.dead_seats ?? []).join("、")} 出局`}</div>;
}

function LocationCard({ card }: CardRendererProps) {
  const d = card.data as { locations?: { name: string }[] };
  const locations = d.locations ?? [];
  return (
    <div className="m-announce">
      📍 {locations.length ? locations.map((l) => l.name).join(" → ") : ""}
    </div>
  );
}

// 注册表：key 优先匹配 variant，其次 kind。
export const CARD_REGISTRY: Record<string, CardRenderer> = {
  // by kind
  portrait_line: PortraitLineCard,
  clue: ClueCard,
  secret: SecretCard,
  affinity_delta: AffinityCard,
  media: MediaCard,
  video: MediaCard,
  audio: MediaCard,
  image: MediaCard,
  location: LocationCard,
  seer_result: SeerResultCard,
  death_notice: DeathNoticeCard,
};

// 按降级链解析卡片渲染器：variant → kind。未命中返回 null（交回 role 兜底）。
export function resolveCardRenderer(card: RichCard): CardRenderer | null {
  if (card.variant && CARD_REGISTRY[card.variant]) return CARD_REGISTRY[card.variant];
  if (card.kind && CARD_REGISTRY[card.kind]) return CARD_REGISTRY[card.kind];
  return null;
}

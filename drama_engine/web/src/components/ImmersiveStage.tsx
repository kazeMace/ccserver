// 沉浸式叙事舞台（文字冒险 / galgame 类）：全屏场景 + 逐句点击揭示 + 底部选项/输入 dock。
// 取代聊天气泡流：每条 interaction 消息是一个「节拍(beat)」，点击一次推进一拍（对话或旁白）。
// 视频节拍全屏播放（字幕 + 进度条 + 跳过）；有待回复且已读到最新时，底部浮出选项/自由输入。
// 复用 inputs.tsx 的 ReplyInput/PromptRow，保证输入原语与聊天式 Composer 单一实现。

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { InteractionMessage, ReplyRequest, RoleDefinition, SessionStatus } from "../types/interaction";
import type { SubmitPartial } from "./inputs";
import { PromptRow, ReplyInput } from "./inputs";

// —— 节拍分类：决定在场景/底部面板里如何呈现 ——
type BeatKind = "media" | "speech" | "narration" | "system" | "secret" | "clue" | "affinity";

interface Beat {
  kind: BeatKind;
  text: string;
  speakerName?: string; // 说话者显示名（对话/立绘）
  emoji?: string; // 说话者 emoji 兜底头像
  portraitUrl?: string; // 说话者立绘图（来自 view.roles）
  isSelf?: boolean; // 是否玩家自己
  affinityDown?: boolean; // 好感度下降（红色）
  clueTitle?: string; // 线索标题
  media?: { kind?: string; url?: string; title?: string; poster?: string; subtitleUrl?: string; autoplay?: boolean };
}

// 从 view.roles 建立「名字/角色id → 角色定义」索引，用于把说话者解析到立绘图。
function buildRoleIndex(roles?: Record<string, RoleDefinition>): Map<string, RoleDefinition> {
  const idx = new Map<string, RoleDefinition>();
  if (!roles) return idx;
  for (const key of Object.keys(roles)) {
    const def = roles[key];
    idx.set(key, def);
    if (def.name) idx.set(def.name, def);
  }
  return idx;
}

// 把一条 interaction 消息解析成一个节拍。降级链：富卡片 → role → body.text。
function toBeat(msg: InteractionMessage, roleIdx: Map<string, RoleDefinition>, selfSeat?: string): Beat {
  const text = msg.body.text ?? "";
  const cards = msg.body.cards ?? [];

  // 1. 富卡片优先。
  for (const card of cards) {
    const key = card.variant ?? card.kind;
    const d = card.data as Record<string, unknown>;
    if (key === "media" || key === "video" || key === "audio" || key === "image") {
      const m = (d as Beat["media"]) ?? {};
      // media 卡的 kind 可能在 data.kind，也可能就是 card.kind。
      return { kind: "media", text, media: { ...m, kind: (m?.kind as string) ?? card.kind } };
    }
    if (key === "portrait_line") {
      const name = (d.name as string) ?? msg.sender?.name;
      return { kind: "speech", text, speakerName: name, emoji: (d.emoji as string) ?? msg.sender?.emoji, portraitUrl: resolvePortrait(name, roleIdx) };
    }
    if (key === "clue") {
      return { kind: "clue", text: (d.desc as string) ?? text, clueTitle: (d.title as string) ?? "线索" };
    }
    if (key === "secret") {
      return { kind: "secret", text: (d.body as string) ?? text };
    }
    if (key === "affinity_delta") {
      return { kind: "affinity", text: (d.text as string) ?? text, affinityDown: d.dir === "down" };
    }
  }

  // 2. 按 role 兜底。
  switch (msg.role) {
    case "narrator":
      return { kind: "narration", text };
    case "secret":
      return { kind: "secret", text };
    case "system":
    case "referee":
    case "system_meta":
      return { kind: "system", text };
    case "dialogue":
    default: {
      const s = msg.sender;
      const isSelf = s?.kind === "human" && (s.id === "me" || (selfSeat != null && s.id === selfSeat));
      return { kind: "speech", text, speakerName: s?.name, emoji: s?.emoji, portraitUrl: resolvePortrait(s?.name, roleIdx), isSelf };
    }
  }
}

// 用说话者名字解析立绘图 URL。
function resolvePortrait(name: string | undefined, roleIdx: Map<string, RoleDefinition>): string | undefined {
  if (!name) return undefined;
  return roleIdx.get(name)?.portrait_url;
}

export interface ImmersiveStageProps {
  messages: InteractionMessage[];
  pending: ReplyRequest | null;
  status: SessionStatus | "connecting";
  submitting?: boolean;
  onSubmit: (partial: SubmitPartial) => void;
  roles?: Record<string, RoleDefinition>;
  selfSeat?: string;
  phase?: string | null;
  title?: string;
  topRight?: ReactNode; // 顶部悬浮控件（视角切换 / 连接状态 / 侧栏按钮）
  submitError?: string;
}

export function ImmersiveStage(props: ImmersiveStageProps) {
  const { messages, pending, status, submitting, onSubmit, roles, selfSeat, phase, title, topRight, submitError } = props;
  const roleIdx = buildRoleIndex(roles);

  // 已揭示到第几条消息（含）。玩家点击「继续」推进；新消息到达默认停在上一次进度，等待点击。
  const [revealed, setRevealed] = useState(0);
  // 记录已看到的消息总数，新消息到达时不自动跳到末尾（保持逐句节奏）。
  const prevLenRef = useRef(0);

  useEffect(() => {
    // 首批消息到达时，先揭示第一条，让场景不至于全空。
    if (prevLenRef.current === 0 && messages.length > 0) {
      setRevealed(1);
    }
    prevLenRef.current = messages.length;
  }, [messages.length]);

  // 会话/座位切换（messages 被清空）时重置揭示进度。
  useEffect(() => {
    if (messages.length === 0) {
      setRevealed(0);
      prevLenRef.current = 0;
    }
  }, [messages.length]);

  const hasMore = revealed < messages.length; // 还有未揭示的历史节拍
  const current = revealed > 0 ? messages[revealed - 1] : null;
  const beat = current ? toBeat(current, roleIdx, selfSeat) : null;

  // 当前视频是否「已播完/已跳过」。用于「视频播完才显示选项」：
  // 视频节拍处于主视角时，先隐藏选项，直到 onEnded 触发或玩家点击跳过。
  const [mediaDone, setMediaDone] = useState(false);
  // 切到新节拍时重置播放完成标记（用当前消息 seq 作为身份）。
  const curSeq = current?.seq ?? -1;
  useEffect(() => {
    setMediaDone(false);
  }, [curSeq]);

  // 背景层：从已揭示范围里向前找最近的一条 media 节拍作为「持久背景」。
  // 视频/图片一旦出现，就固定在最底层，后续对话/旁白/选项都浮在它之上，直到出现新的 media。
  let bgMedia: Beat | null = null;
  for (let i = revealed - 1; i >= 0; i--) {
    const b = toBeat(messages[i], roleIdx, selfSeat);
    if (b.kind === "media") { bgMedia = b; break; }
  }
  // 当前节拍就是这条 media（还没往后翻）→ 视频处于「主视角」。
  const mediaIsCurrent = beat?.kind === "media";
  // 视频主视角且尚未播完：此时不出选项、不出对话框，只等待视频结束或点击跳过。
  const mediaPlaying = mediaIsCurrent && !mediaDone;

  // 待回复且已读到最新：浮出输入 dock。但视频还在播时先压住，等播完/跳过。
  const showReply = Boolean(pending) && !hasMore && !mediaPlaying;
  // 前进一拍（仍有未揭示节拍时）。非视频节拍：点击一次弹一句。
  const advance = () => {
    if (hasMore) setRevealed((n) => Math.min(messages.length, n + 1));
  };
  // 视频播完 / 被点击跳过：等价于「把视频快进到结尾」——一次性揭示到当前批次末尾，
  // 直接露出视频之后的对话与选项。绝不越过选项，保证玩家一定能做选择。
  const finishMedia = () => {
    setMediaDone(true);
    setRevealed(messages.length);
  };
  // 整屏点击：视频在播 → 跳过视频（快进到出选项）；否则仍有节拍 → 前进一拍。
  const onStageClick = () => {
    if (mediaPlaying) { finishMedia(); return; }
    advance();
  };
  const canAdvance = hasMore || mediaPlaying;

  // 立绘：当前是他人对话时展示（浮在背景之上）。
  const figurePortrait = beat?.kind === "speech" && !beat.isSelf ? beat.portraitUrl : undefined;
  const showFigure = beat?.kind === "speech" && !beat.isSelf;
  // 对话框：当前节拍是文本类（非 media）时展示。
  const showDialogue = Boolean(beat) && beat!.kind !== "media";
  const waiting = !hasMore && !pending && status !== "ended" && status !== "connecting";

  return (
    <div className={`imm-stage${canAdvance ? " clickable" : ""}`} onClick={canAdvance ? onStageClick : undefined}>
      {/* 背景层：持久 media（视频/图片/音频）或 立绘模糊底 / 主题渐变底 */}
      <div className="imm-bg-layer">
        {bgMedia ? (
          // onEnded 只在「视频是当前主视角节拍」时生效：播完自动推进/放出选项。
          // 已翻过去（视频退居背景）后不再响应结束事件。
          <MediaLayer beat={bgMedia} onEnded={mediaIsCurrent ? finishMedia : undefined} />
        ) : (
          <SceneBg portrait={figurePortrait} />
        )}
        <div className="imm-vignette" />
      </div>

      {/* 立绘层：浮在背景之上 */}
      {showFigure ? (
        <div className="imm-figure">
          {figurePortrait ? <img src={figurePortrait} alt={beat?.speakerName ?? ""} /> : <div className="imm-figure-emoji">{beat?.emoji ?? "🙂"}</div>}
        </div>
      ) : null}

      {/* 空态提示（尚无任何节拍） */}
      {!beat ? (
        <div className="imm-scene-empty">
          {status === "connecting" ? "正在连接……" : status === "ended" ? "故事已完结" : "等待剧情推进……"}
        </div>
      ) : null}

      {/* 顶部悬浮：章节/阶段 + 页面注入的控件（点击不触发推进） */}
      <div className="imm-top" onClick={(e) => e.stopPropagation()}>
        <div className="imm-top-left">
          {title ? <span className="imm-title">{title}</span> : null}
          {phase ? <span className="imm-phase">{phase}</span> : null}
        </div>
        <div className="imm-top-right">{topRight}</div>
      </div>

      {/* 前景浮层：对话框 + 选项/输入 dock，始终浮在视频/图片之上 */}
      <div className="imm-overlay">
        {submitError ? <div className="imm-error">{submitError}</div> : null}
        {/* 视频播放中无对话框/选项：给一个「点击屏幕跳过」引导条 */}
        {mediaPlaying ? <div className="imm-media-hint">点击屏幕跳过 ▸</div> : null}
        {showDialogue ? <DialogueBox beat={beat!} hint={hasMore ? "点击屏幕继续" : waiting ? "等待剧情推进……" : ""} /> : null}
        {showReply && pending ? (
          // dock 内部交互不冒泡到整屏推进。
          <div onClick={(e) => e.stopPropagation()}>
            <ReplyDock reply={pending} submitting={submitting} onSubmit={onSubmit} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

// —— 背景层：立绘模糊底（对话时）或主题渐变底 ——
function SceneBg({ portrait }: { portrait?: string }) {
  if (portrait) return <div className="imm-bg" style={{ backgroundImage: `url(${portrait})` }} />;
  return <div className="imm-bg imm-bg-flat" />;
}

// —— media 背景层：视频/图片铺满，作为持久背景；不吃点击（点击穿透给整屏推进）——
// onEnded 仅在「视频是当前主视角」时传入：播完触发（放出后续对话或选项）；退居背景后为 undefined。
function MediaLayer({ beat, onEnded }: { beat: Beat; onEnded?: () => void }) {
  const m = beat.media ?? {};
  const kind = m.kind ?? "video";
  if (kind === "image") {
    return <img className="imm-media-el" src={m.url} alt={m.title ?? "scene"} />;
  }
  if (kind === "audio") {
    return (
      <div className="imm-bg imm-bg-flat imm-audio-wrap">
        {m.title ? <div className="imm-media-title">{m.title}</div> : null}
        {/* 音频保留原生控件（点击不穿透，避免误推进） */}
        <audio className="imm-audio" src={m.url} controls autoPlay onEnded={onEnded} onClick={(e) => e.stopPropagation()} />
      </div>
    );
  }
  return (
    <video
      className="imm-media-el"
      src={m.url}
      poster={m.poster || undefined}
      playsInline
      autoPlay
      onEnded={onEnded}
    >
      {m.subtitleUrl ? <track kind="subtitles" src={m.subtitleUrl} srcLang="zh" label="字幕" default /> : null}
    </video>
  );
}

// —— 逐句对话框：说话者 chip + 正文 + 底部提示（点击屏幕继续 / 等待）——
function DialogueBox({ beat, hint }: { beat: Beat; hint: string }) {
  const speaker = beat.kind === "speech" ? (beat.isSelf ? "你" : beat.speakerName) : beat.kind === "narration" ? "旁白" : beat.kind === "clue" ? beat.clueTitle : undefined;
  const boxClass =
    "imm-dialogue" +
    (beat.kind === "narration" ? " narration" : "") +
    (beat.kind === "system" ? " system" : "") +
    (beat.kind === "secret" ? " secret" : "") +
    (beat.kind === "clue" ? " clue" : "") +
    (beat.kind === "affinity" ? " affinity" : "") +
    (beat.affinityDown ? " down" : "") +
    (beat.isSelf ? " self" : "");

  return (
    <div className={boxClass}>
      {speaker ? (
        <div className="imm-speaker">
          {beat.kind === "speech" && !beat.isSelf && beat.emoji ? <span className="imm-speaker-avatar">{beat.emoji}</span> : null}
          <span className="imm-speaker-name">{speaker}</span>
        </div>
      ) : null}
      <div className="imm-line">{beat.text}</div>
      {hint ? <div className="imm-hint">{hint}</div> : null}
    </div>
  );
}

// —— 底部输入 dock：待回复时浮出，复用共享输入原语 ——
function ReplyDock({ reply, submitting, onSubmit }: { reply: ReplyRequest; submitting?: boolean; onSubmit: (p: SubmitPartial) => void }) {
  return (
    <div className="imm-dock">
      <PromptRow reply={reply} />
      <ReplyInput reply={reply} submitting={submitting} onSubmit={onSubmit} />
    </div>
  );
}

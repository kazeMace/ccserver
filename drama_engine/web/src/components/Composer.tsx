// 回复输入区（§3 ReplyRequest → 8 原语）。降级链：widget → primitive。
// 覆盖全部 8 原语：observe(无输入) / text / choice / multi_choice / choice_or_text / vote / structured / form。
// 提交 payload 用协议 PlayerReply 命名（choice_id/choice_ids/text/data + request_id）。

import { useEffect, useState } from "react";
import type { PlayerReply, ReplyOption, ReplyRequest } from "../types/interaction";
import { resolveWidget } from "../registry/widgets";

export type SubmitPartial = Omit<PlayerReply, "session_id" | "seat_id">;

export interface ComposerProps {
  reply: ReplyRequest | null;
  status?: string;
  submitting?: boolean;
  onSubmit: (partial: SubmitPartial) => void;
}

export function Composer({ reply, status, submitting, onSubmit }: ComposerProps) {
  // 无待回复：显示等待/结束态。
  if (!reply) {
    if (status === "ended" || status === "failed") {
      return <div className="composer"><div className="composer-inner"><div className="waiting-tag">对局已结束</div></div></div>;
    }
    return (
      <div className="composer">
        <div className="composer-inner">
          <div className="waiting-tag"><span className="spin" />等待其他玩家 / 系统推进……</div>
        </div>
      </div>
    );
  }

  // 皮肤优先（widget）；未命中用 primitive 兜底。
  const Skin = resolveWidget(reply);
  return (
    <div className="composer">
      <div className="composer-inner">
        <div className="composer-title">
          <span>行动输入</span>
          <span>{reply.primitive === "choice_or_text" ? "选项 / 自由对话" : reply.primitive}</span>
        </div>
        <PromptRow reply={reply} />
        {Skin ? <Skin reply={reply} onSubmit={onSubmit} /> : <PrimitiveInput reply={reply} submitting={submitting} onSubmit={onSubmit} />}
      </div>
    </div>
  );
}

function PromptRow({ reply }: { reply: ReplyRequest }) {
  const [left, setLeft] = useState<number | null>(reply.timeout_ms ? Math.round(reply.timeout_ms / 1000) : null);
  useEffect(() => {
    if (reply.timeout_ms == null) return;
    setLeft(Math.round(reply.timeout_ms / 1000));
    const t = setInterval(() => setLeft((v) => (v == null ? v : Math.max(0, v - 1))), 1000);
    return () => clearInterval(t);
  }, [reply.request_id, reply.timeout_ms]);
  return (
    <div className="reply-prompt">
      <span>{reply.prompt}</span>
      {left != null ? <span className="reply-timer">⏱ {left}s</span> : null}
    </div>
  );
}

// —— primitive 兜底渲染（封闭 8 种）——
function PrimitiveInput({ reply, submitting, onSubmit }: { reply: ReplyRequest; submitting?: boolean; onSubmit: (p: SubmitPartial) => void }) {
  const rid = reply.request_id;
  // confirm 退化（choice + presentation:confirm）
  if (reply.presentation === "confirm") {
    const opt = reply.options?.[0];
    return (
      <button className="confirm-btn" disabled={submitting} onClick={() => onSubmit({ request_id: rid, choice_id: opt?.id ?? "confirm" })}>
        {opt?.text ?? "继续 ▸"}
      </button>
    );
  }

  switch (reply.primitive) {
    case "text":
      return <TextInput reply={reply} submitting={submitting} onSubmit={onSubmit} />;
    case "choice":
      return <ChoiceInput reply={reply} onSubmit={onSubmit} />;
    case "multi_choice":
      return <MultiChoiceInput reply={reply} onSubmit={onSubmit} />;
    case "choice_or_text":
      return <ChoiceOrTextInput reply={reply} submitting={submitting} onSubmit={onSubmit} />;
    case "vote":
      return <VoteInput reply={reply} onSubmit={onSubmit} />;
    case "structured":
    case "form":
      return <FormInput reply={reply} submitting={submitting} onSubmit={onSubmit} />;
    case "observe":
    default:
      return <div className="waiting-tag">（仅观看）</div>;
  }
}

function TextInput({ reply, submitting, onSubmit }: { reply: ReplyRequest; submitting?: boolean; onSubmit: (p: SubmitPartial) => void }) {
  const [text, setText] = useState("");
  const send = () => {
    if (!text.trim()) return;
    onSubmit({ request_id: reply.request_id, text: text.trim() });
    setText("");
  };
  return (
    <div className="text-row">
      <textarea
        className="text-input"
        rows={1}
        placeholder={reply.free_input?.placeholder ?? "输入……"}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
          }
        }}
      />
      <button className="send-btn" disabled={submitting || !text.trim()} onClick={send}>➤</button>
    </div>
  );
}

function ChoiceInput({ reply, onSubmit }: { reply: ReplyRequest; onSubmit: (p: SubmitPartial) => void }) {
  return (
    <div className="choices">
      {(reply.options ?? []).map((o) => (
        <ChoiceButton key={o.id} option={o} onClick={() => onSubmit({ request_id: reply.request_id, choice_id: o.id, text: o.text })} />
      ))}
    </div>
  );
}

function ChoiceButton({ option, selected, onClick }: { option: ReplyOption; selected?: boolean; onClick: () => void }) {
  return (
    <button className={`choice-btn${selected ? " sel" : ""}`} disabled={option.disabled} onClick={onClick}>
      <span className="ck">{selected ? "✓" : ""}</span>
      <span className="c-main">
        {option.text}
        {option.desc ? <div className="c-desc">{option.desc}</div> : null}
        {option.disabled_reason ? <div className="c-cond">{option.disabled_reason}</div> : null}
      </span>
    </button>
  );
}

function MultiChoiceInput({ reply, onSubmit }: { reply: ReplyRequest; onSubmit: (p: SubmitPartial) => void }) {
  const [sel, setSel] = useState<string[]>([]);
  const min = reply.min_select ?? 1;
  const max = reply.max_select ?? (reply.options?.length ?? 1);
  const toggle = (id: string) => setSel((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length < max ? [...cur, id] : cur));
  return (
    <>
      <div className="choices">
        {(reply.options ?? []).map((o) => (
          <ChoiceButton key={o.id} option={o} selected={sel.includes(o.id)} onClick={() => toggle(o.id)} />
        ))}
      </div>
      <button className="confirm-btn" style={{ marginTop: 10 }} disabled={sel.length < min} onClick={() => onSubmit({ request_id: reply.request_id, choice_ids: sel })}>
        确认（已选 {sel.length}，需 {min}–{max}）
      </button>
    </>
  );
}

function ChoiceOrTextInput({ reply, submitting, onSubmit }: { reply: ReplyRequest; submitting?: boolean; onSubmit: (p: SubmitPartial) => void }) {
  return (
    <>
      <div className="choices">
        {(reply.options ?? []).map((o) => (
          <ChoiceButton key={o.id} option={o} onClick={() => onSubmit({ request_id: reply.request_id, choice_id: o.id, text: o.text })} />
        ))}
      </div>
      <div className="hybrid-hint">或自由输入</div>
      <TextInput reply={reply} submitting={submitting} onSubmit={onSubmit} />
    </>
  );
}

function VoteInput({ reply, onSubmit }: { reply: ReplyRequest; onSubmit: (p: SubmitPartial) => void }) {
  const [sel, setSel] = useState<string | null>(null);
  const showCount = Boolean(reply.props?.show_vote_count);
  return (
    <>
      <div className="vote-grid">
        {(reply.options ?? []).map((o) => {
          const meta = (o.meta ?? {}) as { emoji?: string; count?: number };
          return (
            <button key={o.id} className={`vote-cell${sel === o.id ? " sel" : ""}`} disabled={o.disabled} onClick={() => setSel(o.id)}>
              <span className="va">{meta.emoji ?? "🙂"}</span>
              <span className="vn">{o.text}</span>
              {showCount && meta.count ? <span className="vcount">{meta.count} 票</span> : null}
            </button>
          );
        })}
      </div>
      <button className="confirm-btn" style={{ marginTop: 10 }} disabled={!sel} onClick={() => sel && onSubmit({ request_id: reply.request_id, choice_id: sel })}>
        确认投票
      </button>
    </>
  );
}

interface SchemaField {
  name: string;
  label?: string;
  type: string;
  min?: number;
  max?: number;
  value?: number;
  placeholder?: string;
}

function FormInput({ reply, submitting, onSubmit }: { reply: ReplyRequest; submitting?: boolean; onSubmit: (p: SubmitPartial) => void }) {
  const fields = ((reply.schema as { fields?: SchemaField[] })?.fields ?? []) as SchemaField[];
  const [data, setData] = useState<Record<string, unknown>>(() => {
    const init: Record<string, unknown> = {};
    for (const f of fields) init[f.name] = f.value ?? (f.type === "number" || f.type === "range" ? f.min ?? 0 : "");
    return init;
  });
  const set = (name: string, v: unknown) => setData((d) => ({ ...d, [name]: v }));
  return (
    <>
      <div className="form-fields">
        {fields.map((f) => (
          <div className="field-row" key={f.name}>
            <span className="field-label">{f.label ?? f.name}</span>
            {f.type === "range" ? (
              <span className="range-track">
                <input type="range" min={f.min ?? 1} max={f.max ?? 10} value={Number(data[f.name] ?? f.min ?? 1)} onChange={(e) => set(f.name, Number(e.target.value))} />
                <span className="range-val">{String(data[f.name] ?? "")}</span>
              </span>
            ) : (
              <input
                className="field-input"
                type={f.type === "number" ? "number" : "text"}
                placeholder={f.placeholder}
                value={String(data[f.name] ?? "")}
                onChange={(e) => set(f.name, f.type === "number" ? Number(e.target.value) : e.target.value)}
              />
            )}
          </div>
        ))}
      </div>
      <button className="confirm-btn" style={{ marginTop: 10 }} disabled={submitting} onClick={() => onSubmit({ request_id: reply.request_id, data })}>
        提交
      </button>
    </>
  );
}

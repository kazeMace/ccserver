// inbox 轮询 hook（§5 InboxResponse + §6 回滚对齐）。
// 维护 after 游标增量拉取；reset_from 非空时截断本地消息并回退游标（回滚对齐）。

import { useCallback, useEffect, useRef, useState } from "react";
import { getClient } from "../api/client";
import type { InteractionMessage, ReplyRequest, SessionStatus, PlayerReply } from "../types/interaction";

export interface InboxState {
  messages: InteractionMessage[];
  pending: ReplyRequest | null;
  phase: string | null;
  status: SessionStatus | "connecting";
  submitting: boolean;
  refresh: () => Promise<void>;
  submit: (partial: Omit<PlayerReply, "session_id" | "seat_id">) => Promise<void>;
}

export function useInbox(sessionId: string, seat: string, intervalMs = 1500): InboxState {
  const [messages, setMessages] = useState<InteractionMessage[]>([]);
  const [pending, setPending] = useState<ReplyRequest | null>(null);
  const [phase, setPhase] = useState<string | null>(null);
  const [status, setStatus] = useState<SessionStatus | "connecting">("connecting");
  const [submitting, setSubmitting] = useState(false);
  const cursorRef = useRef(0);
  const seenRef = useRef<Set<number>>(new Set());

  // 切换 session 或 seat 时必须清空本地投影。
  // 否则从狼人杀进入 Galgame/剧本杀时，旧消息和 cursor 会继续污染新对局。
  useEffect(() => {
    setMessages([]);
    setPending(null);
    setPhase(null);
    setStatus(sessionId ? "connecting" : "ended");
    cursorRef.current = 0;
    seenRef.current = new Set();
  }, [sessionId, seat]);

  const poll = useCallback(async () => {
    if (!sessionId) return;
    try {
      const resp = await getClient().getInbox(sessionId, seat, cursorRef.current);
      // 回滚对齐：丢弃 seq > reset_from 的本地消息，游标回退。
      if (resp.reset_from != null) {
        const rf = resp.reset_from;
        setMessages((cur) => cur.filter((m) => m.seq <= rf));
        seenRef.current = new Set([...seenRef.current].filter((s) => s <= rf));
        cursorRef.current = rf;
      }
      if (resp.messages.length) {
        const fresh = resp.messages.filter((m) => !seenRef.current.has(m.seq));
        fresh.forEach((m) => seenRef.current.add(m.seq));
        if (fresh.length) setMessages((cur) => [...cur, ...fresh]);
      }
      cursorRef.current = Math.max(cursorRef.current, resp.cursor);
      setPending(resp.pending ?? null);
      setPhase(resp.phase ?? null);
      setStatus(resp.status);
    } catch {
      setStatus("failed");
    }
  }, [sessionId, seat]);

  useEffect(() => {
    poll();
    const t = setInterval(poll, intervalMs);
    return () => clearInterval(t);
  }, [poll, intervalMs]);

  const submit = useCallback(
    async (partial: Omit<PlayerReply, "session_id" | "seat_id">) => {
      setSubmitting(true);
      try {
        const ack = await getClient().postReply(sessionId, { session_id: sessionId, seat_id: seat, ...partial });
        if (!ack.accepted) {
          // 校验失败：pending 保持，交由 UI 展示 error（这里简单 alert，页面可覆盖）。
          throw new Error(ack.error ?? "提交被拒绝");
        }
        setPending(null);
        await poll();
      } finally {
        setSubmitting(false);
      }
    },
    [sessionId, seat, poll],
  );

  return { messages, pending, phase, status, submitting, refresh: poll, submit };
}

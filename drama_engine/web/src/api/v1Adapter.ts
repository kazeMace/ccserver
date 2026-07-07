// v1Adapter：直连 interaction.v1 REST 端点（协议就绪后启用）。
// 端点契约见 docs/interaction_protocol_design.md §1。生命周期沿用现有 /api/sessions/* 端点。

import type {
  DramaClient,
  CreateSessionInput,
  GameDef,
  RollbackPoint,
  SessionSummary,
} from "./client";
import type { InboxResponse, PlayerReply, ReplyAck, StateView } from "../types/interaction";

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ ${url}`);
  return (await res.json()) as T;
}

export class V1Adapter implements DramaClient {
  readonly mode = "v1" as const;

  async listGames(): Promise<GameDef[]> {
    return jsonFetch<GameDef[]>("/api/games");
  }

  async createSession(input: CreateSessionInput): Promise<SessionSummary> {
    return jsonFetch<SessionSummary>("/api/sessions", { method: "POST", body: JSON.stringify(input) });
  }

  async getSession(sessionId: string): Promise<SessionSummary> {
    return jsonFetch<SessionSummary>(`/api/sessions/${sessionId}`);
  }

  private post(path: string): Promise<void> {
    return jsonFetch<void>(path, { method: "POST" });
  }

  assign(s: string) { return this.post(`/api/sessions/${s}/assign`); }
  start(s: string) { return this.post(`/api/sessions/${s}/start`); }
  pause(s: string) { return this.post(`/api/sessions/${s}/pause`); }
  resume(s: string) { return this.post(`/api/sessions/${s}/resume`); }
  restart(s: string) { return this.post(`/api/sessions/${s}/restart`); }
  step(s: string, count = 1) { return this.post(`/api/sessions/${s}/step?count=${count}`); }
  setStepMode(s: string, enabled: boolean) { return this.post(`/api/sessions/${s}/step-mode?enabled=${enabled}`); }
  terminate(s: string) { return this.post(`/api/sessions/${s}/terminate`); }

  // —— interaction.v1 三面（端点由另一会话实现）——
  async getInbox(sessionId: string, seat: string, after: number): Promise<InboxResponse> {
    return jsonFetch<InboxResponse>(`/api/sessions/${sessionId}/inbox?seat=${encodeURIComponent(seat)}&after=${after}&limit=50`);
  }

  async postReply(sessionId: string, reply: PlayerReply): Promise<ReplyAck> {
    return jsonFetch<ReplyAck>(`/api/sessions/${sessionId}/reply`, { method: "POST", body: JSON.stringify(reply) });
  }

  async getView(sessionId: string, seat: string): Promise<StateView> {
    return jsonFetch<StateView>(`/api/sessions/${sessionId}/view?seat=${encodeURIComponent(seat)}`);
  }

  async checkpoint(sessionId: string, reason: string): Promise<RollbackPoint> {
    const res = await jsonFetch<{ checkpoint: RollbackPoint }>(`/api/sessions/${sessionId}/checkpoint?reason=${encodeURIComponent(reason)}`, { method: "POST" });
    return res.checkpoint;
  }
  async rollbackPoints(sessionId: string): Promise<RollbackPoint[]> {
    return jsonFetch<RollbackPoint[]>(`/api/sessions/${sessionId}/rollback-points`);
  }
  async rollbackTo(sessionId: string, checkpointId: string): Promise<void> {
    return this.post(`/api/sessions/${sessionId}/rollback?checkpoint_id=${encodeURIComponent(checkpointId)}`);
  }

  async moderatorSetController(s: string, seat: string, controller: string) {
    return this.post(`/api/sessions/${s}/moderator/set-controller?seat=${seat}&controller=${controller}`);
  }
  async moderatorSubmit(s: string, seat: string, data: unknown, text: string) {
    return jsonFetch<void>(`/api/sessions/${s}/moderator/submit?seat=${seat}`, { method: "POST", body: JSON.stringify({ data, text }) });
  }
  async setHumanCount(s: string, count: number) {
    return this.post(`/api/sessions/${s}/moderator/set-human-count?count=${count}`);
  }
}

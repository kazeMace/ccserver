// 统一 API client 接口：前端所有页面只依赖这个接口，不直接 fetch。
// 两种实现：mockAdapter（原型静态数据，端点未就绪期）、v1Adapter（直连 interaction.v1）。
// 运行时通过 VITE_API_MODE 或默认 mock 选择实现。

import type {
  InboxResponse,
  PlayerReply,
  ReplyAck,
  StateView,
} from "../types/interaction";

// —— 生命周期 / 大厅相关的轻量类型（非 interaction.v1 核心，但页面需要）——
export interface GameDef {
  game_id: string;
  script_path: string;
  title: string;
}

export interface CreateSessionInput {
  game_id: string;
  script_path?: string;
  seat_ids: string[];
  human_seat_ids: string[];
  params: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface SessionSummary {
  session_id: string;
  game_id?: string;
  status?: string;
  player_links?: Record<string, string>;
  host_url?: string;
  viewer_url?: string;
  [key: string]: unknown;
}

export interface RollbackPoint {
  checkpoint_id: string;
  reason: string;
  created_at?: string;
  [key: string]: unknown;
}

// 统一客户端接口。所有方法都以 interaction.v1 契约为准。
export interface DramaClient {
  // 目录 / 创建
  listGames(): Promise<GameDef[]>;
  createSession(input: CreateSessionInput): Promise<SessionSummary>;
  getSession(sessionId: string): Promise<SessionSummary>;

  // 生命周期
  assign(sessionId: string): Promise<void>;
  start(sessionId: string): Promise<void>;
  pause(sessionId: string): Promise<void>;
  resume(sessionId: string): Promise<void>;
  restart(sessionId: string): Promise<void>;
  step(sessionId: string, count?: number): Promise<void>;
  setStepMode(sessionId: string, enabled: boolean): Promise<void>;
  terminate(sessionId: string): Promise<void>;

  // interaction.v1 三面
  getInbox(sessionId: string, seat: string, after: number): Promise<InboxResponse>;
  postReply(sessionId: string, reply: PlayerReply): Promise<ReplyAck>;
  getView(sessionId: string, seat: string): Promise<StateView>;

  // 回滚
  checkpoint(sessionId: string, reason: string): Promise<RollbackPoint>;
  rollbackPoints(sessionId: string): Promise<RollbackPoint[]>;
  rollbackTo(sessionId: string, checkpointId: string): Promise<void>;

  // 主持人操作（可选，v1 端点就绪后接入）
  moderatorSetController?(sessionId: string, seat: string, controller: string): Promise<void>;
  moderatorSubmit?(sessionId: string, seat: string, data: unknown, text: string): Promise<void>;
  setHumanCount?(sessionId: string, count: number): Promise<void>;

  // 元信息
  readonly mode: "mock" | "v1";
}

// —— 运行时单例 ——
// 具体适配器由 main.tsx 按 VITE_API_MODE 注入（避免 client ↔ adapter 循环依赖）。
let _client: DramaClient | null = null;

export function setClient(client: DramaClient): void {
  _client = client;
}

export function getClient(): DramaClient {
  if (!_client) throw new Error("client 未初始化：请在 main.tsx 调用 setClient()");
  return _client;
}

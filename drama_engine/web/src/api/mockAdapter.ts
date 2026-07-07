// mockAdapter：把原型 step 脚本转成 interaction.v1 契约，供端点未就绪期开发/演示。
// 核心：维护每个 session 的「已推进到第几 step + 已产出消息（带 seq）+ 当前 pending」，
// 玩家提交 reply 后推进到下一 step，追加其消息并挂新 pending——模拟真实 inbox 增量拉取。

import type {
  DramaClient,
  CreateSessionInput,
  GameDef,
  RollbackPoint,
  SessionSummary,
} from "./client";
import type {
  InboxResponse,
  InteractionMessage,
  MessageRole,
  PlayerReply,
  ReplyAck,
  ReplyRequest,
  ReplyPrimitive,
  RichCard,
  StateView,
} from "../types/interaction";
import { GAMES, SCRIPTS, type MockGame, type MockMsg, type MockReply } from "./mockData";

// mock kind → interaction.v1 role + 可选 RichCard。
function mockMsgToInteraction(msg: MockMsg, seq: number, sessionId: string, phase: string, channel: string): InteractionMessage {
  let role: MessageRole = "system";
  let cards: RichCard[] | undefined;
  const senderKind = msg.self ? "human" : msg.sender ? "agent" : "system";

  switch (msg.kind) {
    case "announce":
      role = msg.strong ? "referee" : "system";
      break;
    case "narrate":
      role = "narrator";
      break;
    case "chat":
      role = "dialogue";
      break;
    case "vn":
      role = "dialogue";
      cards = [{ kind: "portrait_line", data: { name: msg.name, emoji: msg.emoji } }];
      break;
    case "clue":
      role = "system";
      cards = [{ kind: "clue", data: { title: msg.title, desc: msg.desc, foot: msg.foot } }];
      break;
    case "secret":
      role = "secret";
      cards = [{ kind: "secret", data: { head: msg.head, body: msg.body } }];
      break;
    case "affinity":
      role = "system";
      cards = [{ kind: "affinity_delta", data: { text: msg.text, dir: msg.dir } }];
      break;
  }

  return {
    seq,
    session_id: sessionId,
    ts: seq, // mock 用 seq 当时间戳（Date.now 会破坏可重放，这里不需要真实时间）
    role,
    sender: msg.sender
      ? { kind: senderKind, id: msg.sender.id, name: msg.sender.name, emoji: msg.sender.emoji, role: msg.sender.role }
      : msg.self
        ? { kind: "human", id: "me", name: "你", emoji: "😎" }
        : msg.name
          ? { kind: "npc", name: msg.name, emoji: msg.emoji }
          : null,
    body: {
      text: msg.text ?? msg.body ?? msg.title ?? "",
      style: msg.strong ? "announcement" : msg.kind === "narrate" ? "dramatic" : "normal",
      cards: cards ?? null,
    },
    scope: channel === "public" || channel === "villa" || channel === "table" || channel === "story" || channel === "board" ? "public" : `group:${channel}`,
    phase,
    scene_id: null,
    reply_request: null,
  };
}

// mock reply.type → interaction.v1 primitive。
function mockTypeToPrimitive(type: MockReply["type"]): { primitive: ReplyPrimitive; presentation?: "confirm" } {
  switch (type) {
    case "confirm":
      return { primitive: "choice", presentation: "confirm" };
    case "text":
      return { primitive: "text" };
    case "choice":
      return { primitive: "choice" };
    case "choice_or_text":
      return { primitive: "choice_or_text" };
    case "vote":
      return { primitive: "vote" };
    case "structured":
      return { primitive: "structured" };
    case "form":
      return { primitive: "form" };
  }
}

function mockReplyToRequest(reply: MockReply, requestId: string): ReplyRequest {
  const { primitive, presentation } = mockTypeToPrimitive(reply.type);
  // vote 的 candidates 归一成 options（协议里 vote 也用 options）。
  const options =
    reply.options?.map((o) => ({ id: o.id, text: o.text, desc: o.desc ?? null, disabled: o.disabled ?? false, disabled_reason: o.cond ?? null })) ??
    reply.candidates?.map((c) => ({ id: c.id, text: c.name, desc: null, meta: { emoji: c.emoji, count: c.count } })) ??
    null;
  // structured/form 的 fields → 简单 schema。
  const schema = reply.fields
    ? { fields: reply.fields.map((f) => ({ name: f.id, label: f.label, type: f.type, min: f.min, max: f.max, value: f.value, placeholder: f.placeholder })) }
    : null;
  return {
    request_id: requestId,
    primitive,
    widget: reply.widget ?? null,
    props: reply.props ?? null,
    prompt: reply.prompt,
    presentation: presentation ?? "default",
    options,
    free_input:
      reply.type === "text" || reply.type === "choice_or_text"
        ? { placeholder: reply.placeholder ?? "", multiline: true, hint: null }
        : null,
    schema,
    timeout_ms: reply.timer ? reply.timer * 1000 : null,
    min_select: 1,
    max_select: 1,
    skippable: false,
    // confirm 退化：label 放进 options
    ...(presentation === "confirm" ? { options: [{ id: "confirm", text: reply.label ?? "继续", desc: null }] } : {}),
  };
}

// 每个 mock session 的推进状态。
interface MockPlay {
  gameId: string;
  game: MockGame;
  stepIdx: number; // 已产出到第几 step（含）
  messages: InteractionMessage[]; // 累积消息（带 seq）
  seq: number;
  pending: ReplyRequest | null;
  status: InboxResponse["status"];
}

export class MockAdapter implements DramaClient {
  readonly mode = "mock" as const;
  private plays = new Map<string, MockPlay>();
  private counter = 0;

  async listGames(): Promise<GameDef[]> {
    return GAMES.map((g) => ({ game_id: g.id, script_path: `mock/${g.id}`, title: g.name }));
  }

  async createSession(input: CreateSessionInput): Promise<SessionSummary> {
    this.counter += 1;
    const sessionId = `mock-${input.game_id}-${this.counter}`;
    const gameId = SCRIPTS[input.game_id] ? input.game_id : "werewolf";
    this.plays.set(sessionId, {
      gameId,
      game: SCRIPTS[gameId],
      stepIdx: -1,
      messages: [],
      seq: 0,
      pending: null,
      status: "running",
    });
    return { session_id: sessionId, game_id: gameId, status: "lobby", player_links: { Player_1: `/player?token=${sessionId}` } };
  }

  async getSession(sessionId: string): Promise<SessionSummary> {
    const play = this.plays.get(sessionId);
    return { session_id: sessionId, game_id: play?.gameId, status: play?.status ?? "running" };
  }

  // 推进一个 step：追加其消息，挂上其 reply（若有）。
  private advance(play: MockPlay): void {
    const next = play.stepIdx + 1;
    if (next >= play.game.steps.length) {
      play.status = "ended";
      play.pending = null;
      return;
    }
    play.stepIdx = next;
    const step = play.game.steps[next];
    for (const m of step.msgs) {
      play.seq += 1;
      play.messages.push(mockMsgToInteraction(m, play.seq, "mock", play.game.phase, step.channel));
    }
    if (step.reply) {
      const reqId = `req-${play.stepIdx}`;
      play.pending = mockReplyToRequest(step.reply, reqId);
      // 把 pending 挂到最后一条消息上（协议：reply_request 挂在某条 message）。
      if (play.messages.length > 0) play.messages[play.messages.length - 1].reply_request = play.pending;
      play.status = "running";
    } else {
      play.pending = null;
      play.status = next + 1 >= play.game.steps.length ? "ended" : "running";
      // 无 reply 的 step 自动继续推进到下一个需要交互的 step。
      if (play.status !== "ended") this.advance(play);
    }
  }

  async assign(): Promise<void> {}
  async start(sessionId: string): Promise<void> {
    const play = this.plays.get(sessionId);
    if (play && play.stepIdx < 0) this.advance(play);
  }
  async pause(): Promise<void> {}
  async resume(): Promise<void> {}
  async restart(sessionId: string): Promise<void> {
    const play = this.plays.get(sessionId);
    if (play) {
      play.stepIdx = -1;
      play.messages = [];
      play.seq = 0;
      play.pending = null;
      play.status = "running";
      this.advance(play);
    }
  }
  async step(): Promise<void> {}
  async setStepMode(): Promise<void> {}
  async terminate(sessionId: string): Promise<void> {
    const play = this.plays.get(sessionId);
    if (play) play.status = "ended";
  }

  async getInbox(sessionId: string, _seat: string, after: number): Promise<InboxResponse> {
    const play = this.plays.get(sessionId);
    if (!play) return { messages: [], cursor: after, pending: null, status: "ended" };
    const messages = play.messages.filter((m) => m.seq > after);
    return {
      messages,
      cursor: play.seq,
      pending: play.pending,
      phase: play.game.phase,
      status: play.status,
      reset_from: null,
    };
  }

  async postReply(sessionId: string, reply: PlayerReply): Promise<ReplyAck> {
    const play = this.plays.get(sessionId);
    if (!play || !play.pending) return { accepted: false, error: "无待回复请求" };
    if (reply.request_id !== play.pending.request_id) return { accepted: false, error: "请求已过期" };
    play.pending = null;
    this.advance(play);
    // 提交后新产出的消息作为 new_messages 返回（供即时回显）。
    return { accepted: true, new_messages: [] };
  }

  async getView(sessionId: string, seat: string): Promise<StateView> {
    const play = this.plays.get(sessionId);
    const game = play?.game;
    const panels: Record<string, unknown> = {};
    if (game?.affinities) panels.affinity = game.affinities;
    if (game?.stats) panels.stats = game.stats;
    if (game?.circles) panels.circles = game.circles;
    if (game?.boardState) panels.board = game.boardState;
    if (game?.progress) panels.progress_detail = game.progress;
    return {
      seat_id: seat,
      phase: game?.phase ?? null,
      progress: game?.progress ? { label: game.progress.label, current: game.progress.cur, total: game.progress.total } : null,
      players: (game?.players ?? []).map((p) => ({
        seat_id: p.id,
        name: p.name,
        emoji: p.emoji,
        alive: !p.dead,
        online: p.online,
        tag: p.tag,
        tag_text: p.tagText,
      })),
      panels,
      self: {},
    };
  }

  async checkpoint(_s: string, reason: string): Promise<RollbackPoint> {
    return { checkpoint_id: `ckpt-${Date.now()}`, reason };
  }
  async rollbackPoints(): Promise<RollbackPoint[]> {
    return [];
  }
  async rollbackTo(): Promise<void> {}
}

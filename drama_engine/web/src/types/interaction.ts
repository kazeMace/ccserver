// interaction.v1 协议类型定义。
// 严格对齐 drama_engine/docs/interaction_protocol_design.md 第二部分（§2-§7、§9）。
// 前端所有数据面都用这里的类型；游戏差异走开放键（widget/card.kind/props/panels），不改这些类型。

// —— §2.1 消息渲染气质（封闭枚举，决定布局）——
export type MessageRole =
  | "system" // 系统公告/流程通报
  | "narrator" // 旁白叙事
  | "dialogue" // 角色/玩家发言
  | "secret" // 私密信息下发（验人结果/私人剧本）
  | "referee" // 裁判结算/胜负
  | "system_meta"; // 仅 host 可见元事件（guardrail/rollback）

// —— §2.2 富卡片（开放枚举，未知 kind 降级为纯文本）——
export interface RichCard {
  kind: string; // clue | portrait_line | affinity_delta | dice_roll | board_move | seer_result | death_notice | ...
  variant?: string; // 更细的皮肤变体，降级链最高优先级
  data: Record<string, unknown>;
}

export interface MessageBody {
  text: string; // 主文本，任何 role 都有；终极兜底渲染
  style?: "normal" | "dramatic" | "whisper" | "announcement";
  cards?: RichCard[] | null;
}

// —— 发送者 ——
export interface Sender {
  kind: "human" | "agent" | "npc" | "system";
  id?: string;
  name?: string;
  emoji?: string;
  role?: string; // 角色标签（可见性允许时）
  dead?: boolean;
}

// —— §场景-2 交互原语（封闭 8 种）——
export type ReplyPrimitive =
  | "observe" // 只看无输入（不产生 reply_request，此值仅用于映射说明）
  | "text"
  | "choice"
  | "multi_choice"
  | "choice_or_text"
  | "vote"
  | "structured"
  | "form";

// —— §3 待回复 ——
export interface ReplyOption {
  id: string;
  text: string;
  desc?: string | null;
  disabled?: boolean;
  disabled_reason?: string | null;
  meta?: Record<string, unknown> | null; // vote 时可放 emoji / 实时票数
}

export interface FreeInputSpec {
  placeholder?: string;
  max_length?: number | null;
  multiline?: boolean;
  hint?: string | null;
}

export interface ReplyRequest {
  request_id: string;
  primitive: ReplyPrimitive; // 封闭，保证兜底可渲染
  widget?: string | null; // 开放皮肤变体（如 vote:night_kill），未知降级到 primitive
  props?: Record<string, unknown> | null; // §9.4 语义级参数（非纯视觉）
  prompt: string;
  presentation?: "default" | "confirm";
  options?: ReplyOption[] | null;
  free_input?: FreeInputSpec | null;
  schema?: Record<string, unknown> | null;
  timeout_ms?: number | null;
  min_select?: number;
  max_select?: number;
  skippable?: boolean;
}

// —— §2 核心消息体 ——
export interface InteractionMessage {
  seq: number; // 全局单调递增，游标与去重依据
  session_id: string;
  ts: number;
  role: MessageRole;
  sender?: Sender | null;
  body: MessageBody;
  scope: string; // "public" | "private" | "group:<id>"（展示用，安全已在服务端保证）
  phase?: string | null;
  scene_id?: string | null;
  reply_request?: ReplyRequest | null;
}

// —— §5 拉取响应 ——
export type SessionStatus =
  | "running"
  | "waiting_others"
  | "paused"
  | "ended"
  | "failed";

export interface InboxResponse {
  messages: InteractionMessage[]; // seq > after 且该 seat 有权见，升序
  cursor: number; // 本批最大 seq
  pending?: ReplyRequest | null; // 该 seat 当前待回复
  phase?: string | null;
  status: SessionStatus;
  reset_from?: number | null; // §6 回滚对齐：丢弃本地 seq > reset_from 的消息
}

// —— §4 上行回复 ——
export interface PlayerReply {
  session_id: string;
  seat_id: string;
  request_id: string;
  choice_id?: string | null; // choice / vote / confirm
  choice_ids?: string[] | null; // multi_choice
  text?: string | null; // text / choice_or_text
  data?: Record<string, unknown> | null; // structured / form
  client_ts?: number | null;
}

export interface ReplyAck {
  accepted: boolean;
  error?: string | null;
  new_messages?: InteractionMessage[];
}

// —— §7 只读状态视图 ——
export interface PlayerCard {
  seat_id: string;
  name?: string;
  emoji?: string;
  alive?: boolean;
  online?: boolean;
  tag?: string; // 身份标签（按可见性遮蔽后）
  tag_text?: string;
}

export interface StateView {
  seat_id: string;
  phase?: string | null;
  progress?: { label: string; current: number; total: number } | null;
  players: PlayerCard[];
  panels: Record<string, unknown>; // 开放字典：affinity/hand/board/stats/circles...
  self: Record<string, unknown>; // 本 seat 完整属性（含 disclosed）
}

// —— 客户端受众 ——
export type Audience = "public" | "host" | "audience" | `player:${string}`;

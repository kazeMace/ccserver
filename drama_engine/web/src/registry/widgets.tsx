// 输入组件注册表（§9 输入轴降级链）。
// 查表顺序：reply.widget（如 vote:night_kill）→ reply.primitive（封闭 8 种，必命中兜底）。
// 新增游戏输入皮肤 = 在 WIDGET_REGISTRY 加一条，miss 自动降级到 primitive，不改 Composer。

import type { ReplyRequest, PlayerReply } from "../types/interaction";

// 输入组件统一 props：拿到 reply 请求 + 提交回调。
export interface WidgetProps {
  reply: ReplyRequest;
  onSubmit: (partial: Omit<PlayerReply, "session_id" | "seat_id">) => void;
}

export type WidgetRenderer = (props: WidgetProps) => JSX.Element;

// game_pack 皮肤注册表。key = "primitive:widget"。
// 目前不注册专属皮肤——狼人杀 night_kill/day_exile 直接复用通用 vote 组件（props 驱动差异）。
// 未来需要独立皮肤时在此加：WIDGET_REGISTRY["vote:night_kill"] = WolfNightKill。
export const WIDGET_REGISTRY: Record<string, WidgetRenderer> = {};

// 解析皮肤渲染器；未命中返回 null（交回 Composer 用 primitive 兜底）。
export function resolveWidget(reply: ReplyRequest): WidgetRenderer | null {
  if (reply.widget) {
    const key = `${reply.primitive}:${reply.widget}`;
    if (WIDGET_REGISTRY[key]) return WIDGET_REGISTRY[key];
    if (WIDGET_REGISTRY[reply.widget]) return WIDGET_REGISTRY[reply.widget];
  }
  return null;
}

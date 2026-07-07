// 从消息流的 scope 派生「频道」（原型聊天范式的核心）。
// 狼人杀的公开厅 / 狼人频道 / 预言家私聊等，都是 scope 的不同取值——
// 前端据此生成频道切换条，并按当前频道过滤消息。前端零可见性过滤：
// 能看到某 scope 的消息，本就说明该 seat 有权见（§2.3），频道只是分组展示。

import { useMemo } from "react";
import type { InteractionMessage } from "../types/interaction";
import type { ChannelDef } from "../components/Chrome";

// 公开主频道的 scope 别名（后端可能用 public / public_room / villa / table / story / board）。
// 这些都归入同一个「全场」主频道，不拆成多个 tab。
const PUBLIC_SCOPES = new Set(["public", "public_room", "villa", "table", "story", "board", "dungeon", "sanctuary"]);

function isPublicScope(scope: string): boolean {
  return PUBLIC_SCOPES.has(scope) || scope.endsWith("_room") && scope.startsWith("public");
}

// 内置常见 scope 语义名（前端兜底）。理想情况由后端 game_pack 的 projection_profile
// 通过 view.panels.scope_labels 下发（§9 开放键）；未下发时用这份兜底，未知走 scope 原名。
const DEFAULT_SCOPE_LABELS: Record<string, [string, string]> = {
  wolf_den: ["狼人频道", "🐺"],
  seer_room: ["预言家", "🔮"],
  witch_room: ["女巫", "🧪"],
  guard_room: ["守卫", "🛡️"],
  private: ["私聊", "🤫"],
};

// scope → 频道展示名/图标。公开 scope 恒为主频道；group:<id> / private 为子频道。
// game_pack 的语义（如 wolf_den→狼人频道）通过 scopeLabels 注入；未知走内置兜底/原名。
function scopeToChannel(scope: string, labels: Record<string, [string, string]>): ChannelDef {
  const raw = scope.startsWith("group:") ? scope.slice("group:".length) : scope;
  const [name, icon] = labels[raw] ?? labels[scope] ?? DEFAULT_SCOPE_LABELS[raw] ?? [raw, "🔒"];
  return { id: scope, name, icon, lock: true };
}

export interface DerivedChannels {
  channels: ChannelDef[];
  /** 按 scope 过滤某频道的消息（public 频道 = scope==public）。 */
  filter: (messages: InteractionMessage[], channelId: string) => InteractionMessage[];
}

export function useChannels(
  messages: InteractionMessage[],
  scopeLabels: Record<string, [string, string]> = {},
): DerivedChannels {
  const channels = useMemo(() => {
    const seen = new Map<string, ChannelDef>();
    // 主频道恒在最前，聚合所有公开 scope。
    seen.set("public", { id: "public", name: "全场", icon: "🏛️" });
    for (const m of messages) {
      const scope = m.scope || "public";
      if (isPublicScope(scope)) continue; // 公开 scope 归主频道
      if (!seen.has(scope)) seen.set(scope, scopeToChannel(scope, scopeLabels));
    }
    return Array.from(seen.values());
  }, [messages, scopeLabels]);

  const filter = useMemo(
    () => (msgs: InteractionMessage[], channelId: string) => {
      if (channelId === "public") return msgs.filter((m) => isPublicScope(m.scope || "public"));
      return msgs.filter((m) => m.scope === channelId);
    },
    [],
  );

  return { channels, filter };
}

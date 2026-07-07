// 游戏表现层：把后端 game_id / 前端 genre 归一到目标原型的五类游戏入口。
// 这层只服务 UI 导航和主题，不参与真实规则判断。

import { GAMES } from "./mockData";

export type GameKind = "werewolf" | "the_clause" | "mystery" | "variety" | "board";

export const GAME_META = new Map(GAMES.map((g) => [g.id as GameKind, g]));

export function inferGameKind(value?: string | null): GameKind | undefined {
  const v = (value ?? "").toLowerCase();
  if (!v) return undefined;
  if (v.includes("werewolf") || v.includes("wolf") || v.includes("undercover") || v.includes("deduction")) return "werewolf";
  if (v.includes("the_clause") || v.includes("clause") || v.includes("galgame")) return "the_clause";
  if (v.includes("mystery") || v.includes("script") || v.includes("clue") || v.includes("case")) return "mystery";
  if (v.includes("variety") || v.includes("dating") || v.includes("love") || v.includes("show")) return "variety";
  if (v.includes("board") || v.includes("gomoku") || v.includes("chess") || v.includes("uno") || v.includes("xiangqi")) return "board";
  return undefined;
}

export function themeGenreForKind(kind?: GameKind): string | undefined {
  if (!kind) return undefined;
  return kind === "the_clause" ? "galgame" : kind;
}

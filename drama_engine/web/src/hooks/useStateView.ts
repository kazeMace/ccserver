// StateView 轮询 hook（§7）。与 inbox 正交，用于侧边栏富状态。

import { useEffect, useState } from "react";
import { getClient } from "../api/client";
import type { StateView } from "../types/interaction";

export function useStateView(sessionId: string, seat: string, intervalMs = 3000): StateView | null {
  const [view, setView] = useState<StateView | null>(null);
  useEffect(() => {
    if (!sessionId) return;
    let alive = true;
    const load = async () => {
      try {
        const v = await getClient().getView(sessionId, seat);
        if (alive) setView(v);
      } catch {
        /* 忽略瞬时错误，下次轮询重试 */
      }
    };
    load();
    const t = setInterval(load, intervalMs);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [sessionId, seat, intervalMs]);
  return view;
}

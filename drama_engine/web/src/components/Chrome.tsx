// 外壳组件：顶栏、频道条、连接状态。小而通用，供各页面复用。

import type { SessionStatus } from "../types/interaction";

export function Topbar({ title, sub, phase, right }: { title: string; sub?: string; phase?: string | null; right?: React.ReactNode }) {
  return (
    <div className="topbar">
      <div className="topbar-title">
        {title}
        {sub ? <small>{sub}</small> : null}
      </div>
      <div className="topbar-spacer" />
      {phase ? (
        <span className="phase-pill">
          <span className="dot" />
          {phase}
        </span>
      ) : null}
      {right}
    </div>
  );
}

export interface ChannelDef {
  id: string;
  name: string;
  icon?: string;
  lock?: boolean;
  badge?: number;
}

export function Channels({ channels, active, onSelect }: { channels: ChannelDef[]; active: string; onSelect: (id: string) => void }) {
  if (channels.length <= 1) return null;
  return (
    <div className="channels">
      {channels.map((c) => (
        <button key={c.id} className={`channel${active === c.id ? " active" : ""}`} onClick={() => onSelect(c.id)}>
          {c.icon ? <span>{c.icon}</span> : null}
          {c.name}
          {c.lock ? <span className="lock">🔒</span> : null}
          {c.badge ? <span className="badge">{c.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function ConnStatus({ status }: { status: SessionStatus | "connecting" }) {
  const cls = status === "failed" ? "error" : status === "connecting" ? "" : "live";
  const label =
    status === "running"
      ? "运行中"
      : status === "waiting_others"
        ? "等待其他玩家"
        : status === "paused"
          ? "已暂停"
          : status === "ended"
            ? "已结束"
            : status === "failed"
              ? "已失败"
              : "连接中";
  return (
    <span className={`conn ${cls}`}>
      <span className="dot" />
      {label}
    </span>
  );
}

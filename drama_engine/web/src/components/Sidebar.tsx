// 右侧只读状态面板（§7 StateView）。panels 是开放字典，按已知 key 渲染，未知忽略。
// 与消息流正交：不含待回复，纯展示 affinity/stats/circles/board/players/progress。

import type { PlayerCard, StateView } from "../types/interaction";

export function Sidebar({ view, open, onClose }: { view: StateView | null; open?: boolean; onClose?: () => void }) {
  if (!view) return null;
  const panels = view.panels ?? {};
  return (
    <>
      {open ? <div className="sidebar-overlay show" onClick={onClose} /> : null}
      <aside className={`sidebar${open ? " open" : ""}`}>
        {view.progress ? <ProgressSection progress={view.progress} /> : null}
        {Array.isArray(panels.affinity) ? <AffinitySection rows={panels.affinity as AffinityRow[]} /> : null}
        {Array.isArray(panels.circles) ? <CirclesSection circles={panels.circles as CircleRow[]} /> : null}
        {Array.isArray(panels.stats) ? <StatsSection stats={panels.stats as StatRow[]} /> : null}
        {Array.isArray(panels.board) ? <BoardSection rows={panels.board as string[]} /> : null}
        {view.players?.length ? <PlayersSection players={view.players} /> : null}
      </aside>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="side-section">
      <div className="side-title">{title}</div>
      {children}
    </div>
  );
}

function ProgressSection({ progress }: { progress: NonNullable<StateView["progress"]> }) {
  const dots = Array.from({ length: progress.total }, (_, i) => i);
  return (
    <Section title={progress.label}>
      <div className="progress-days">
        {dots.map((i) => (
          <span key={i} className={`day-dot${i < progress.current ? " done" : i === progress.current ? " cur" : ""}`} />
        ))}
      </div>
      <div className="progress-label">
        {progress.current} / {progress.total}
      </div>
    </Section>
  );
}

interface AffinityRow { id: string; name: string; emoji: string; value: number; max: number }
function AffinitySection({ rows }: { rows: AffinityRow[] }) {
  return (
    <Section title="好感度">
      {rows.map((r) => (
        <div className="affinity-row" key={r.id}>
          <div className="pa">{r.emoji}</div>
          <div className="affinity-bar-wrap">
            <div className="affinity-top">
              <span>{r.name}</span>
              <span className="heart">❤️ {r.value}</span>
            </div>
            <div className="affinity-bar">
              <div className="affinity-fill" style={{ width: `${Math.min(100, (r.value / r.max) * 100)}%` }} />
            </div>
          </div>
        </div>
      ))}
    </Section>
  );
}

interface CircleRow { name: string; emoji: string; members: string }
function CirclesSection({ circles }: { circles: CircleRow[] }) {
  return (
    <Section title="关系圈">
      <div className="circles">
        {circles.map((c, i) => (
          <span className="circle-tag" key={i}>
            {c.emoji} {c.name}：{c.members}
          </span>
        ))}
      </div>
    </Section>
  );
}

interface StatRow { icon: string; name: string; value: string }
function StatsSection({ stats }: { stats: StatRow[] }) {
  return (
    <Section title="我的属性">
      {stats.map((s, i) => (
        <div className="stat-row" key={i}>
          <span className="si">{s.icon}</span>
          <span className="sn">{s.name}</span>
          <span className="sv">{s.value}</span>
        </div>
      ))}
    </Section>
  );
}

function BoardSection({ rows }: { rows: string[] }) {
  return (
    <Section title="棋盘">
      <div className="mini-board" style={{ gridTemplateColumns: `repeat(${rows[0]?.length ?? 9},1fr)` }}>
        {rows.flatMap((row, r) =>
          row.split("").map((ch, c) => (
            <div key={`${r}-${c}`} className={`mini-cell${ch === "●" ? " b" : ch === "○" ? " w" : ""}`}>
              {ch === "●" ? "●" : ch === "○" ? "○" : ""}
            </div>
          )),
        )}
      </div>
    </Section>
  );
}

function PlayersSection({ players }: { players: PlayerCard[] }) {
  return (
    <Section title={`玩家 (${players.length})`}>
      <div className="player-list">
        {players.map((p) => (
          <div className={`player-row${p.alive === false ? " dead" : ""}`} key={p.seat_id}>
            <div className={`pa${p.online ? " online" : ""}`}>{p.emoji ?? "🙂"}</div>
            <div className="pinfo">
              <div className="pname">
                {p.name ?? p.seat_id}
                {p.tag === "me" ? <span className="ptag me">我</span> : null}
                {p.alive === false ? <span className="ptag dead">出局</span> : null}
              </div>
              {p.tag_text ? <div className="pmeta">{p.tag_text}</div> : null}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

// 右侧只读状态面板（§7 StateView）。panels 是开放字典，按已知 key 渲染，未知忽略。
// 与消息流正交：不含待回复，纯展示 affinity/stats/circles/board/players/progress。

import { useState } from "react";
import type { PlayerCard, StateView, RoleDefinition } from "../types/interaction";
import { RolesList } from "./RoleCard";

export function Sidebar({ view, open, onClose }: { view: StateView | null; open?: boolean; onClose?: () => void }) {
  if (!view) return null;
  const panels = view.panels ?? {};
  const storyTree = panels.story_tree as StoryTreeData | undefined;
  return (
    <>
      {open ? <div className="sidebar-overlay show" onClick={onClose} /> : null}
      <aside className={`sidebar${open ? " open" : ""}`}>
        {view.progress ? <ProgressSection progress={view.progress} /> : null}
        {storyTree ? <StoryTreeSection data={storyTree} /> : null}
        {Array.isArray(panels.affinity) ? <AffinitySection rows={panels.affinity as AffinityRow[]} /> : null}
        {Array.isArray(panels.circles) ? <CirclesSection circles={panels.circles as CircleRow[]} /> : null}
        {Array.isArray(panels.stats) ? <StatsSection stats={panels.stats as StatRow[]} /> : null}
        {view.roles ? <RolesSection roles={view.roles} /> : null}
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

function RolesSection({ roles }: { roles: Record<string, RoleDefinition> }) {
  return (
    <Section title="角色">
      <RolesList roles={roles} />
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

// ═══ 剧情分支树（树形层级布局）═══

interface StoryTreeNode { id: string; title: string; terminal?: boolean; synopsis?: string }
interface StoryTreeEdge { from: string; to: string; choice_id?: string; choice_text?: string }
interface StoryTreeData {
  current_node: string;
  visited_nodes: string[];
  choice_history: { node: string; choice_id: string; choice_text: string; to: string }[];
  tree: { nodes: StoryTreeNode[]; edges: StoryTreeEdge[] };
}

function StoryTreeSection({ data }: { data: StoryTreeData }) {
  const { current_node, visited_nodes, tree } = data;
  const visitedSet = new Set(visited_nodes);
  const nodeMap = new Map(tree.nodes.map((n) => [n.id, n]));

  // 按层级组织：从 initial 开始 BFS，每层是同级的节点
  const initial = tree.nodes[0]?.id || "";
  const getChildren = (nodeId: string) => tree.edges.filter((e) => e.from === nodeId).map((e) => e.to);

  // BFS 构建层级
  type LayerNode = { id: string; parentId: string | null };
  const layers: LayerNode[][] = [];
  const seen = new Set<string>();
  let queue: LayerNode[] = [{ id: initial, parentId: null }];
  while (queue.length > 0) {
    layers.push(queue);
    const next: LayerNode[] = [];
    for (const item of queue) {
      seen.add(item.id);
      const children = getChildren(item.id);
      for (const cid of children) {
        if (!seen.has(cid)) {
          next.push({ id: cid, parentId: item.id });
          seen.add(cid);
        }
      }
    }
    queue = next;
  }

  // 判断节点是否相邻可见（父节点已访问）
  const adjacentVisible = (nodeId: string, parentId: string | null) => {
    if (visitedSet.has(nodeId)) return true;
    if (parentId && visitedSet.has(parentId)) return true;
    return false;
  };

  // 判断是否是已走过的路径上的连接线
  const isOnPath = (nodeId: string) => visitedSet.has(nodeId);

  const [open, setOpen] = useState(false);
  const visitedCount = visited_nodes.length;
  const totalCount = tree.nodes.length;
  const currentNodeObj = nodeMap.get(current_node);

  return (
    <Section title="剧情路径">
      {/* 预览卡片（点击弹出悬浮窗） */}
      <div className="st-preview" onClick={() => setOpen(true)}>
        <div className="st-preview-info">
          <span className="st-preview-cur">▶ {currentNodeObj?.title || current_node}</span>
          <span className="st-preview-progress">{visitedCount} / {totalCount} 节点已探索</span>
        </div>
        <span className="st-preview-toggle">查看全图 ▸</span>
      </div>
      {/* 悬浮弹窗 */}
      {open ? (
        <div className="st-modal-overlay" onClick={() => setOpen(false)}>
          <div className="st-modal" onClick={(e) => e.stopPropagation()}>
            <div className="st-modal-head">
              <span className="st-modal-title">剧情路径</span>
              <button className="st-modal-close" onClick={() => setOpen(false)}>✕</button>
            </div>
            <div className="st-modal-body">
              <div className="st-tree">
                {layers.map((layer, li) => (
                  <div key={li} className="st-layer">
                    {li > 0 ? <div className="st-connectors" /> : null}
                    <div className="st-row">
                      {layer.map(({ id, parentId }) => {
                        const node = nodeMap.get(id);
                        if (!node) return null;
                        const visible = adjacentVisible(id, parentId);
                        if (!visible) return null;
                        const isVisited = visitedSet.has(id);
                        const isCurrent = id === current_node;
                        const isLocked = !isVisited;
                        return (
                          <div
                            key={id}
                            className={`st-card${isCurrent ? " now" : ""}${isVisited ? " visited" : ""}${isLocked ? " locked" : ""}${node.terminal ? " terminal" : ""}`}
                            title={!isLocked && node.synopsis ? node.synopsis : undefined}
                          >
                            {isCurrent ? <span className="st-now-tag">Now</span> : null}
                            <span className="st-card-icon">
                              {isLocked ? "🔒" : isOnPath(id) ? "✓" : "●"}
                            </span>
                            <span className="st-card-title">
                              {isLocked ? "Undiscovered" : node.title}
                            </span>
                            {!isLocked && node.synopsis ? (
                              <span className="st-card-synopsis">{node.synopsis}</span>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </Section>
  );
}

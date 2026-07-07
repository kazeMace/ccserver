// View 插件系统（移植旧 host 的 __view__ 可视化）。
// 后端下发的视图事件按 view_kind 路由到渲染器；未知 kind 用 fallback（JSON dump）降级。
// 数据来源：StateView.panels 里的 view 事件，或 host 视图的 view 列表。

export interface ViewEvent {
  view_id: string;
  view_kind: string;
  title?: string;
  data: Record<string, unknown>;
  priority?: number;
}

type ViewRenderer = (data: Record<string, unknown>) => React.ReactNode;

function KeyValue(data: Record<string, unknown>) {
  const rows = (data.rows as { label: string; value: unknown }[]) ?? Object.entries(data).map(([k, v]) => ({ label: k, value: v }));
  return (
    <>
      {rows.map((r, i) => (
        <div className="vkv-row" key={i}>
          <span className="k">{r.label}</span>
          <span className="v">{formatVal(r.value)}</span>
        </div>
      ))}
    </>
  );
}

function TextView(data: Record<string, unknown>) {
  return <div style={{ fontSize: 13, lineHeight: 1.6 }}>{String(data.text ?? "")}</div>;
}

function ListView(data: Record<string, unknown>) {
  const items = (data.items as unknown[]) ?? [];
  return (
    <ul style={{ paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
      {items.map((it, i) => (
        <li key={i}>{formatVal(it)}</li>
      ))}
    </ul>
  );
}

function TableView(data: Record<string, unknown>) {
  const cols = (data.columns as string[]) ?? [];
  const rows = (data.rows as unknown[][]) ?? [];
  return (
    <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
      <thead>
        <tr>{cols.map((c) => <th key={c} style={{ textAlign: "left", color: "var(--text-lo)", padding: "3px 4px" }}>{c}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>{row.map((cell, j) => <td key={j} style={{ padding: "3px 4px" }}>{formatVal(cell)}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

function BoardView(data: Record<string, unknown>) {
  // data.cells: { "r,c": "B"|"W" }，data.size: n
  const size = Number(data.size ?? 9);
  const cells = (data.cells as Record<string, string>) ?? {};
  const grid = Array.from({ length: size * size }, (_, idx) => {
    const r = Math.floor(idx / size);
    const c = idx % size;
    return cells[`${r},${c}`] ?? "";
  });
  return (
    <div className="mini-board" style={{ gridTemplateColumns: `repeat(${size},1fr)` }}>
      {grid.map((v, i) => (
        <div key={i} className={`mini-cell${v === "B" ? " b" : v === "W" ? " w" : ""}`}>{v === "B" ? "●" : v === "W" ? "○" : ""}</div>
      ))}
    </div>
  );
}

function TallyView(data: Record<string, unknown>) {
  const counts = (data.counts as Record<string, number>) ?? {};
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  return (
    <>
      {entries.map(([name, n]) => (
        <div className="vkv-row" key={name}>
          <span className="k">{name}</span>
          <span className="v">{n} 票</span>
        </div>
      ))}
    </>
  );
}

function InventoryView(data: Record<string, unknown>) {
  const items = (data.items as { name: string; count: number | string }[]) ?? [];
  return (
    <>
      {items.map((it, i) => (
        <div className="vkv-row" key={i}>
          <span className="k">{it.name}</span>
          <span className="v">×{it.count}</span>
        </div>
      ))}
    </>
  );
}

function FallbackView(data: Record<string, unknown>) {
  return <pre style={{ fontSize: 11, whiteSpace: "pre-wrap", color: "var(--text-mid)" }}>{JSON.stringify(data, null, 2)}</pre>;
}

const VIEW_REGISTRY: Record<string, ViewRenderer> = {
  "key-value": KeyValue,
  text: TextView,
  markdown: TextView,
  list: ListView,
  table: TableView,
  board: BoardView,
  cards: ListView,
  "player-list": ListView,
  tally: TallyView,
  "vote-summary": TallyView,
  "resource-list": InventoryView,
  inventory: InventoryView,
  timeline: ListView,
};

function formatVal(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function ViewPluginCard({ view }: { view: ViewEvent }) {
  const Renderer = VIEW_REGISTRY[view.view_kind] ?? FallbackView;
  return (
    <div className="vplugin">
      <div className="vplugin-title">{view.title ?? view.view_id}</div>
      {Renderer(view.data)}
    </div>
  );
}

export function ViewPluginStack({ views }: { views: ViewEvent[] }) {
  const sorted = [...views].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0));
  if (!sorted.length) return <div className="empty-hint">暂无视图数据</div>;
  return (
    <>
      {sorted.map((v) => (
        <ViewPluginCard key={v.view_id} view={v} />
      ))}
    </>
  );
}

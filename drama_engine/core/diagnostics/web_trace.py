"""
web_trace.py — Drama Engine 观测可视化

把每个 Actor「听到的消息（perceive）」「自己说的话（act）」，以及主持人（Narrator）
「控场发言（narration）」记录下来，跑完一局后渲染成一个**自包含的静态 HTML**：
  - 顶部一张「控场主持人」卡片：主持人对各可见域说的全部话（流程流水）
  - 下面每个 Actor 一张卡片：该 Actor 的视角，按可见域（Scope）颜色区分

为什么需要它：
  控制台日志把所有 Actor 的事件混在一起，很难一眼看清「谁知道什么」。
  狼人杀这类隐藏信息游戏，最关键的就是「可见域隔离」——这个视图让它一目了然。

交互（纯前端 JS，无需服务）：
  - 「按发言顺序排序」按钮：把玩家卡片按各自首次发言(act)的先后重排；再点恢复默认顺序。
  - 「深色/浅色」按钮：切换主题（默认浅色）。

用法（见 run.py）：
  from drama_engine.core.diagnostics.web_trace import PerceptionTracer, render_html
  tracer = PerceptionTracer()
  actor = create_agent_actor(name="Player_1", ..., tracer=tracer)   # 传给每个 actor
  narrator = Narrator(stage=stage, tracer=tracer)                   # 传给主持人
  ...
  await director.run(state)
  render_html(tracer, roles=..., out_path="records/werewolf_view.html")

设计要点：
  - 纯旁路观测：tracer 只被动接收事件，不参与游戏逻辑（SRP / 对应设计文档 §7.H）。
  - 不依赖时钟：用自增序号 seq 保证事件顺序稳定。
  - 渲染为单文件 HTML：内联 CSS/JS，无需起服务、无外部依赖，双击即可看。
"""

from html import escape   # 转义文本，防止消息内容破坏 HTML 结构


# 主持人（控场）在事件流里的特殊 actor 标识；不会被当成玩家卡片
_MODERATOR_KEY = "__moderator__"


# =============================================================================
# 段 1：观测记录器
# =============================================================================


class PerceptionTracer:
    """
    观测记录器 — 收集 perceive / act / narration 三类事件。

    每条事件是一个 dict：
      {
        "seq": int,        # 全局自增序号（保证跨来源的时间顺序）
        "actor": str,      # 事件所属者（玩家名；narration 用 _MODERATOR_KEY）
        "kind": str,       # "perceive"（听到）/ "act"（自己说）/ "narration"（主持人控场）
        "scope": str,      # 可见域名（perceive/narration 有；act 留空）
        "sender": str,     # 发言人（perceive/act 有；narration 留空）
        "text": str,       # 消息正文
      }
    """

    def __init__(self, on_event=None):
        """
        初始化空记录器。

        参数：
          on_event — 可选回调 def fn(event: dict) -> None。
                     每记录一条事件后立即调用一次，用于「实时推送」（如 SSE 直播）。
                     为 None 时不推送，只在内存里累积（供跑完后渲染静态 HTML）。
        """
        self.events: list = []
        self._seq: int = 0
        self._on_event = on_event

    def _next_seq(self) -> int:
        """取下一个自增序号。"""
        self._seq += 1
        return self._seq

    def _emit(self, event: dict) -> None:
        """内部：把事件存入缓冲，并（若挂了回调）实时推送一次。"""
        self.events.append(event)
        if self._on_event is not None:
            # 推送失败不能影响游戏主流程，吞掉异常
            try:
                self._on_event(event)
            except Exception as exc:   # noqa: BLE001  （观测旁路，容错优先）
                print(f"[web_trace] on_event 推送失败（已忽略）：{exc}")

    def record_perceive(self, actor: str, scope: str, sender: str, text: str) -> None:
        """记录一条「Actor 听到了什么」。"""
        self._emit({
            "seq": self._next_seq(),
            "actor": actor, "kind": "perceive",
            "scope": scope, "sender": sender, "text": text,
        })

    def record_act(self, actor: str, text: str) -> None:
        """记录一条「Actor 自己说了什么」（act 的原始输出）。"""
        self._emit({
            "seq": self._next_seq(),
            "actor": actor, "kind": "act",
            "scope": "", "sender": actor, "text": text,
        })

    def record_narration(self, scope: str, text: str) -> None:
        """记录一条「主持人控场发言」（投到哪个 Scope）。"""
        self._emit({
            "seq": self._next_seq(),
            "actor": _MODERATOR_KEY, "kind": "narration",
            "scope": scope, "sender": "", "text": text,
        })

    def actors_in_order(self) -> list:
        """返回出现过的所有「玩家」名（排除主持人），按首次出现顺序去重。"""
        seen = set()
        ordered = []
        for ev in self.events:
            name = ev["actor"]
            if name == _MODERATOR_KEY:
                continue
            if name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered

    def events_of(self, actor: str) -> list:
        """返回某个 Actor 的全部事件（按 seq 顺序）。"""
        return [ev for ev in self.events if ev["actor"] == actor]

    def narration_events(self) -> list:
        """返回主持人的全部控场发言（按 seq 顺序）。"""
        return [ev for ev in self.events if ev["kind"] == "narration"]

    def first_act_seq(self, actor: str) -> int:
        """
        返回某 Actor 首次发言(act)的 seq；从未发言则返回一个很大的数（排序时排末尾）。

        参数：
          actor — Actor 名
        返回：
          首次 act 的 seq，或 10**9（从未发言）
        """
        for ev in self.events:
            if ev["actor"] == actor and ev["kind"] == "act":
                return ev["seq"]
        return 10 ** 9


# =============================================================================
# 段 2：HTML 渲染
# =============================================================================
#
# 不同可见域用不同颜色，让「谁能听到哪个信道」一眼可辨。
# 这里给狼人杀常见的 5 个 Scope 预设了配色；未知 Scope 用灰色兜底。
# =============================================================================

# 可见域 -> (背景色, 左边框色, 中文标签)
_SCOPE_STYLES = {
    "public":        ("#eef2ff", "#6366f1", "全场"),
    "town":          ("#ecfdf5", "#10b981", "白天/存活"),
    "wolf-den":      ("#fef2f2", "#ef4444", "狼人密谈"),
    "whisper:seer":  ("#eff6ff", "#3b82f6", "预言家私密"),
    "whisper:witch": ("#faf5ff", "#a855f7", "女巫私密"),
    "private":       ("#fff7ed", "#f59e0b", "身份私密"),
}
_SCOPE_FALLBACK = ("#f3f4f6", "#9ca3af", "")

# 角色名 -> emoji 徽标
_ROLE_BADGE = {
    "werewolf": "🐺 狼人",
    "seer":     "🔮 预言家",
    "witch":    "🧪 女巫",
    "hunter":   "🔫 猎人",
    "guard":    "🛡 守卫",
    "idiot":    "🎭 白痴",
    "cupid":    "💘 丘比特",
    "villager": "👤 村民",
}


def _scope_style(scope: str):
    """根据可见域名返回 (背景色, 边框色, 标签)。"""
    return _SCOPE_STYLES.get(scope, _SCOPE_FALLBACK)


def _render_event(ev: dict) -> str:
    """把单条事件渲染成一段 HTML。"""
    text = escape(ev["text"] or "")

    if ev["kind"] == "act":
        # 自己说的话：靠右、独立配色
        return (
            '<div class="evt act">'
            '<div class="evt-head">🗣 我说</div>'
            f'<div class="evt-body">{text}</div>'
            '</div>'
        )

    # perceive / narration：按可见域配色
    bg, border, label = _scope_style(ev["scope"])
    scope_tag = escape(ev["scope"] or "")
    label_txt = f"{scope_tag}" + (f" · {label}" if label else "")
    sender = escape(ev["sender"] or "")
    sender_html = f'<span class="sender">{sender}</span>' if sender else ""
    return (
        f'<div class="evt perceive" style="background:{bg};">'
        f'<div class="evt-head"><span class="scope-tag" style="color:{border};">{label_txt}</span>'
        f'{sender_html}</div>'
        f'<div class="evt-body">{text}</div>'
        '</div>'
    )


def _render_moderator_card(events: list) -> str:
    """
    渲染「控场主持人」卡片（全宽，置顶）。

    参数：
      events — 主持人 narration 事件列表（按 seq 顺序）
    返回：
      主持人卡片 HTML
    """
    body = "".join(_render_event(e) for e in events) or '<div class="empty">（无控场记录）</div>'
    return (
        '<div class="card moderator">'
        '<div class="card-head">'
        '<span class="actor-name">🎙 主持人 · 控场</span>'
        f'<span class="counts">{len(events)} 条流程发言</span>'
        '</div>'
        f'<div class="card-body">{body}</div>'
        '</div>'
    )


def _render_card(actor: str, events: list, role_info: dict, order_index: int, first_act: int) -> str:
    """
    渲染单个玩家卡片。

    参数：
      actor       — Actor 名
      events      — 该 Actor 的事件列表（按顺序）
      role_info   — {"role": 角色名, "alive": bool} 或 None
      order_index — 默认顺序下标（用于 JS「恢复默认排序」）
      first_act   — 首次发言 seq（用于 JS「按发言顺序排序」）
    返回：
      一张卡片的 HTML
    """
    badge = ""
    status = ""
    if role_info:
        role = role_info.get("role")
        if role:
            badge = f'<span class="role-badge">{_ROLE_BADGE.get(role, role)}</span>'
        if role_info.get("alive") is True:
            status = '<span class="status alive">存活</span>'
        elif role_info.get("alive") is False:
            status = '<span class="status dead">出局</span>'

    n_perceive = sum(1 for e in events if e["kind"] == "perceive")
    n_act = sum(1 for e in events if e["kind"] == "act")
    body = "".join(_render_event(e) for e in events) or '<div class="empty">（无事件）</div>'

    return (
        f'<div class="card" data-order="{order_index}" data-firstact="{first_act}">'
        '<div class="card-head">'
        f'<span class="actor-name">{escape(actor)}</span>{badge}{status}'
        f'<span class="counts">听到 {n_perceive} · 发言 {n_act}</span>'
        '</div>'
        f'<div class="card-body">{body}</div>'
        '</div>'
    )


def render_html(tracer: PerceptionTracer, roles: dict = None, out_path: str = "drama_view.html",
                title: str = "Drama Engine · Actor 视角") -> str:
    """
    把 tracer 里的事件渲染成一个自包含的静态 HTML 文件。

    参数：
      tracer   — PerceptionTracer 实例（已收集完事件）
      roles    — dict：actor名 -> {"role": 角色名, "alive": bool}，用于卡片头徽标。可为 None。
      out_path — 输出 HTML 文件路径
      title    — 页面标题
    返回：
      实际写入的文件路径（字符串）
    """
    roles = roles or {}
    actors = tracer.actors_in_order()

    # 可见域图例
    legend = "".join(
        f'<span class="legend-item" style="background:{bg};">'
        f'{escape(scope)} · {escape(label)}</span>'
        for scope, (bg, border, label) in _SCOPE_STYLES.items()
    )

    # 主持人控场卡片（置顶，全宽）
    moderator_card = _render_moderator_card(tracer.narration_events())

    # 玩家卡片
    cards = "".join(
        _render_card(a, tracer.events_of(a), roles.get(a), i, tracer.first_act_seq(a))
        for i, a in enumerate(actors)
    )

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  /* 主题用 CSS 变量；默认浅色，body.dark 时切深色 */
  :root {{
    --page-bg:#f1f5f9; --title:#0f172a; --subtitle:#64748b;
    --card-bg:#ffffff; --card-head-bg:#1e293b; --card-head-fg:#f1f5f9;
    --evt-text:#1f2937; --shadow:0 2px 8px rgba(0,0,0,.10);
  }}
  body.dark {{
    --page-bg:#0f172a; --title:#f1f5f9; --subtitle:#94a3b8;
    --card-bg:#ffffff; --card-head-bg:#1e293b; --card-head-fg:#f1f5f9;
    --evt-text:#1f2937; --shadow:0 4px 14px rgba(0,0,0,.35);
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:24px; background:var(--page-bg); color:var(--evt-text);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
  h1 {{ color:var(--title); font-size:20px; margin:0 0 6px; }}
  .subtitle {{ color:var(--subtitle); font-size:13px; margin:0 0 14px; }}
  .toolbar {{ display:flex; gap:10px; margin-bottom:14px; }}
  .btn {{ cursor:pointer; border:1px solid #cbd5e1; background:#ffffff; color:#0f172a;
         font-size:13px; padding:6px 14px; border-radius:8px; }}
  .btn:hover {{ background:#f1f5f9; }}
  .btn.on {{ background:#6366f1; color:#fff; border-color:#6366f1; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; }}
  .legend-item {{ font-size:12px; padding:4px 10px; border-radius:4px; color:#374151; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:16px; align-items:start; }}
  .card {{ background:var(--card-bg); border-radius:10px; overflow:hidden; box-shadow:var(--shadow); }}
  .card.moderator {{ margin-bottom:18px; border:2px solid #6366f1; }}
  .card.moderator .card-head {{ background:#4338ca; }}
  .card.moderator .card-body {{ max-height:300px; }}
  .card-head {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap;
               padding:12px 14px; background:var(--card-head-bg); color:var(--card-head-fg); }}
  .actor-name {{ font-weight:700; font-size:15px; }}
  .role-badge {{ font-size:12px; padding:2px 8px; border-radius:10px; background:#334155; color:#e2e8f0; }}
  .status {{ font-size:11px; padding:2px 8px; border-radius:10px; }}
  .status.alive {{ background:#065f46; color:#d1fae5; }}
  .status.dead {{ background:#7f1d1d; color:#fee2e2; }}
  .counts {{ margin-left:auto; font-size:11px; color:#94a3b8; }}
  .card-body {{ padding:10px; max-height:560px; overflow-y:auto; }}
  .evt {{ margin:6px 0; padding:8px 10px; border-radius:6px; font-size:13px; line-height:1.5; }}
  .evt-head {{ display:flex; justify-content:space-between; gap:8px; font-size:11px; margin-bottom:3px; }}
  .scope-tag {{ font-weight:600; }}
  .sender {{ color:#6b7280; }}
  .evt-body {{ white-space:pre-wrap; word-break:break-word; color:var(--evt-text); }}
  .evt.act {{ background:#1f2937; color:#f9fafb; margin-left:24px; }}
  .evt.act .evt-head {{ color:#fbbf24; }}
  .evt.act .evt-body {{ color:#f3f4f6; }}
  .empty {{ color:#9ca3af; font-size:13px; padding:8px; }}
</style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <p class="subtitle">彩色块 = 该 Actor「听到」的消息（按可见域配色）；深色块 = 它「自己说」的话。顶部为主持人控场流水。</p>
  <div class="toolbar">
    <button id="sortBtn" class="btn" onclick="toggleSort()">按发言顺序排序</button>
    <button id="themeBtn" class="btn" onclick="toggleTheme()">深色主题</button>
  </div>
  <div class="legend">{legend}</div>
  {moderator_card}
  <div class="grid" id="grid">{cards}</div>

<script>
  // 「按发言顺序排序」：在「默认顺序(data-order)」与「首次发言顺序(data-firstact)」间切换
  var sorted = false;
  function toggleSort() {{
    var grid = document.getElementById('grid');
    var cards = Array.prototype.slice.call(grid.querySelectorAll('.card'));
    sorted = !sorted;
    var key = sorted ? 'firstact' : 'order';
    cards.sort(function(a, b) {{
      return parseInt(a.dataset[key], 10) - parseInt(b.dataset[key], 10);
    }});
    cards.forEach(function(c) {{ grid.appendChild(c); }});
    var btn = document.getElementById('sortBtn');
    btn.textContent = sorted ? '恢复默认顺序' : '按发言顺序排序';
    btn.classList.toggle('on', sorted);
  }}

  // 「深色/浅色主题」切换（默认浅色）
  function toggleTheme() {{
    var dark = document.body.classList.toggle('dark');
    var btn = document.getElementById('themeBtn');
    btn.textContent = dark ? '浅色主题' : '深色主题';
    btn.classList.toggle('on', dark);
  }}
</script>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[web_trace] 已生成可视化：{out_path}"
          f"（{len(actors)} 个 Actor + 主持人，{len(tracer.events)} 条事件）")
    return out_path

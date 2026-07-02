// New Drama Engine service adapter.
// Host dashboard frontend for service sessions.
// but talks to the new /api/sessions/{session_id}/... endpoints.
const serviceContext = (() => {
  const pathMatch = window.location.pathname.match(/\/host\/sessions\/([^/]+)/);
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session_id") || (pathMatch ? decodeURIComponent(pathMatch[1]) : "");
  return { sessionId };
})();

function requireSessionId() {
  if (!serviceContext.sessionId) {
    throw new Error("缺少 session_id，请从创建房间页进入 Host 页面。");
  }
  return serviceContext.sessionId;
}

function sessionApi(path) {
  return `/api/sessions/${encodeURIComponent(requireSessionId())}${path}`;
}

async function postSession(path, body) {
  const options = { method: "POST" };
  if (body !== undefined) {
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(sessionApi(path), options);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response;
}

const dashboardLayoutStorageKey = "drama_engine_dashboard_columns_v1";
const dashboardLayoutDefaults = {
  left: 280,
  plugin: 368,
  history: 286,
};
const dashboardLayoutLimits = {
  left: { min: 220, max: 460 },
  plugin: { min: 260, max: 560 },
  history: { min: 220, max: 460 },
  centerMin: 560,
};

const state = {
  scopeStyles: {},
  roleBadges: {},
  moderatorKey: "__moderator__",
  seen: new Set(),
  cards: {},
  playerState: {},
  playerOrder: [],
  moderator: null,
  orderCounter: 0,
  selectedActor: null,
  started: false,
  assigned: false,
  paused: false,
  stepMode: false,
  autoStepTimer: null,
  bubbleTimers: {},
  bubbleDurationMs: 5000,
  bubbleQueueGapMs: 250,
  seenSpeechKeys: new Map(),
  initialReplaySeqMax: 0,
  hasConnectedEvents: false,
  historyMode: "heard",
  phaseText: "尚未开始",
  roundNo: 0,
  dayNo: 0,
  nightNo: 0,
  conversationCount: 0,
  viewEvents: {},
  viewHost: null,
  stepGate: {},
  dashboardLayout: Object.assign({}, dashboardLayoutDefaults),
};

const defaultRoleBadges = {
  werewolf: "狼人",
  seer: "预言家",
  witch: "女巫",
  hunter: "猎人",
  guard: "守卫",
  idiot: "白痴",
  cupid: "丘比特",
  villager: "村民",
};

function el(id) {
  return document.getElementById(id);
}

function clampNumber(value, min, max) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return min;
  return Math.max(min, Math.min(max, numeric));
}

function readDashboardLayout() {
  try {
    const raw = window.localStorage.getItem(dashboardLayoutStorageKey);
    if (!raw) return Object.assign({}, dashboardLayoutDefaults);
    const saved = JSON.parse(raw);
    return {
      left: clampNumber(saved.left, dashboardLayoutLimits.left.min, dashboardLayoutLimits.left.max),
      plugin: clampNumber(saved.plugin, dashboardLayoutLimits.plugin.min, dashboardLayoutLimits.plugin.max),
      history: clampNumber(saved.history, dashboardLayoutLimits.history.min, dashboardLayoutLimits.history.max),
    };
  } catch (error) {
    console.warn("读取 dashboard 列宽失败，使用默认列宽。", error);
    return Object.assign({}, dashboardLayoutDefaults);
  }
}

function saveDashboardLayout() {
  try {
    window.localStorage.setItem(dashboardLayoutStorageKey, JSON.stringify(state.dashboardLayout));
  } catch (error) {
    console.warn("保存 dashboard 列宽失败。", error);
  }
}

function maxColumnWidth(column, nextLayout) {
  const app = document.querySelector(".app");
  if (!app) return dashboardLayoutLimits[column].max;
  const rect = app.getBoundingClientRect();
  const compactMode = window.matchMedia("(max-width: 980px)").matches;
  if (compactMode || rect.width <= 0) return dashboardLayoutLimits[column].max;

  const resizerCount = window.matchMedia("(max-width: 1280px)").matches ? 2 : 3;
  const reservedByOtherColumns = Object.keys(dashboardLayoutDefaults).reduce((total, key) => {
    if (key === column) return total;
    if (key === "history" && resizerCount === 2) return total;
    return total + Number(nextLayout[key] || dashboardLayoutDefaults[key]);
  }, resizerCount * 10 + dashboardLayoutLimits.centerMin);
  const available = rect.width - reservedByOtherColumns;
  return Math.max(dashboardLayoutLimits[column].min, Math.min(dashboardLayoutLimits[column].max, available));
}

function applyDashboardLayout(options) {
  const shouldSave = !options || options.save !== false;
  const nextLayout = Object.assign({}, state.dashboardLayout);
  nextLayout.left = clampNumber(nextLayout.left, dashboardLayoutLimits.left.min, maxColumnWidth("left", nextLayout));
  nextLayout.plugin = clampNumber(nextLayout.plugin, dashboardLayoutLimits.plugin.min, maxColumnWidth("plugin", nextLayout));
  nextLayout.history = clampNumber(nextLayout.history, dashboardLayoutLimits.history.min, maxColumnWidth("history", nextLayout));
  state.dashboardLayout = nextLayout;

  const root = document.documentElement;
  root.style.setProperty("--dashboard-left-width", `${nextLayout.left}px`);
  root.style.setProperty("--dashboard-plugin-width", `${nextLayout.plugin}px`);
  root.style.setProperty("--dashboard-history-width", `${nextLayout.history}px`);
  if (shouldSave) saveDashboardLayout();
}

function updateDashboardColumn(column, width, options) {
  assertDashboardColumn(column);
  state.dashboardLayout[column] = Number(width);
  applyDashboardLayout(options);
}

function assertDashboardColumn(column) {
  if (!Object.prototype.hasOwnProperty.call(dashboardLayoutDefaults, column)) {
    throw new Error(`未知 dashboard 列：${column}`);
  }
}

function resizeColumnFromPointer(column, clientX) {
  const app = document.querySelector(".app");
  if (!app) return;
  const rect = app.getBoundingClientRect();
  let width = state.dashboardLayout[column];
  if (column === "left") {
    width = clientX - rect.left;
  } else if (column === "plugin") {
    width = rect.right - clientX;
    if (!el("debugView").classList.contains("hidden") && !window.matchMedia("(max-width: 1280px)").matches) {
      width = rect.right - clientX - state.dashboardLayout.history - 10;
    }
  } else {
    width = rect.right - clientX;
  }
  updateDashboardColumn(column, width);
}

function bindColumnResizer(handleId, column) {
  const handle = el(handleId);
  if (!handle) return;
  assertDashboardColumn(column);
  handle.addEventListener("pointerdown", (event) => {
    if (window.matchMedia("(max-width: 980px)").matches) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("is-dragging");
    document.body.classList.add("is-resizing-columns");
    resizeColumnFromPointer(column, event.clientX);
  });
  handle.addEventListener("pointermove", (event) => {
    if (!handle.classList.contains("is-dragging")) return;
    resizeColumnFromPointer(column, event.clientX);
  });
  handle.addEventListener("pointerup", (event) => {
    if (handle.hasPointerCapture(event.pointerId)) {
      handle.releasePointerCapture(event.pointerId);
    }
    handle.classList.remove("is-dragging");
    document.body.classList.remove("is-resizing-columns");
    saveDashboardLayout();
  });
  handle.addEventListener("pointercancel", () => {
    handle.classList.remove("is-dragging");
    document.body.classList.remove("is-resizing-columns");
    saveDashboardLayout();
  });
  handle.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 40 : 16;
    let delta = 0;
    if (event.key === "ArrowLeft") delta = column === "left" ? -step : step;
    if (event.key === "ArrowRight") delta = column === "left" ? step : -step;
    if (delta === 0) return;
    event.preventDefault();
    updateDashboardColumn(column, state.dashboardLayout[column] + delta);
  });
}

function initDashboardColumnResizers() {
  state.dashboardLayout = readDashboardLayout();
  applyDashboardLayout({ save: false });
  bindColumnResizer("leftColumnResizer", "left");
  bindColumnResizer("pluginColumnResizer", "plugin");
  bindColumnResizer("historyColumnResizer", "history");
  window.addEventListener("resize", () => applyDashboardLayout({ save: false }));
}


function esc(text) {
  const node = document.createElement("div");
  node.textContent = text == null ? "" : text;
  return node.innerHTML;
}

function clip(text, limit) {
  const value = text || "";
  return value.length > limit ? `${value.slice(0, limit)}...` : value;
}

function displayValue(value) {
  if (Array.isArray(value)) return value.length ? value.join("、") : "无";
  if (value === null || value === undefined || value === "") return "无";
  if (typeof value === "object") return formatObjectValue(value);
  return formatStringValue(String(value));
}

function formatStringValue(value) {
  const text = String(value || "").trim();
  if (!text) return "无";
  const sheriffJoinMatched = text.match(/^【(Player_\d+)｜上警】(?:该玩家|我)选择(是|否)。?(.*)$/);
  if (sheriffJoinMatched) {
    const actor = sheriffJoinMatched[1];
    const choice = sheriffJoinMatched[2] === "是" ? "上警" : "不上警";
    const suffix = sheriffJoinMatched[3] || "";
    return `【${actor}｜上警】我选择${choice}。${suffix}`;
  }
  if (text.startsWith("{") && text.endsWith("}")) {
    try {
      return formatObjectValue(JSON.parse(text));
    } catch {
      return text;
    }
  }
  return text;
}

function formatObjectValue(value) {
  // 把结构化动作数据格式化成新人能读懂的人话，避免气泡直接显示 JSON/dict。
  // Convert structured action payloads into readable text instead of leaking JSON/dict.
  if (!value || typeof value !== "object" || Array.isArray(value)) return String(value || "无");
  const reason = value.reason ? `理由：${value.reason}` : "";
  if (Object.prototype.hasOwnProperty.call(value, "vote")) {
    return `我投给 ${value.vote}。${reason}`;
  }
  if (Object.prototype.hasOwnProperty.call(value, "target")) {
    if (Object.prototype.hasOwnProperty.call(value, "action") && value.action === false) {
      return `我选择不行动。${reason}`;
    }
    return `我选择 ${value.target}。${reason}`;
  }
  if (Object.prototype.hasOwnProperty.call(value, "choose")) {
    return `我选择 ${value.choose}。${reason}`;
  }
  if (Object.prototype.hasOwnProperty.call(value, "action")) {
    return `我选择${value.action ? "是" : "否"}。${reason}`;
  }
  return Object.keys(value).map((key) => `${key}: ${displayValue(value[key])}`).join("；");
}


function normalizeEvent(rawEvent) {
  const raw = rawEvent || {};
  const payload = raw.payload && typeof raw.payload === "object" ? raw.payload : null;
  // 兼容两类 SSE：
  // 1. 直接事件：{type:"act", actor:"Player_1", text:"..."}
  // 2. 包装事件：{type:"trace", payload:{type:"act", ...}}
  // payload 应优先保留内部真实 type，否则 trace 包装会把 act 覆盖掉，气泡不会触发。
  const event = payload ? Object.assign({}, raw, payload) : Object.assign({}, raw);
  const rawType = payload ? payload.type : event.type;
  const rawKind = payload ? payload.kind : event.kind;
  const eventType = rawType || rawKind || event.type || event.kind || "";
  event.type = eventType;
  // kind 仅作旧事件兼容字段，不作为前端主语义读取。
  event.kind = rawKind || eventType;
  if (event.actor == null || event.actor === "") event.actor = event.seat_id || event._seat_id || event.sender || "";
  if (event.sender == null) event.sender = "";
  if (event.scope == null) event.scope = "";
  if (event.text == null || event.text === "") {
    event.text = event.message || event.template || event.cue || event.reason || event.result || "";
  }
  if (event.view_id == null && event.id != null) event.view_id = event.id;
  if (event.view_kind == null && event.type === "__view__" && event.view_type) event.view_kind = event.view_type;
  if (event.view_kind == null && event.type === "__view__" && rawKind && rawKind !== "__view__") event.view_kind = rawKind;
  return event;
}

function isDialogueKind(kind) {
  return kind === "narration" || kind === "act" || kind === "perceive";
}

function hasDialoguePayload(event) {
  if (!event || !isDialogueKind(event.type)) return false;
  if (event.type === "narration") return Boolean(event.text || event.scope);
  if (event.type === "act" || event.type === "perceive") {
    return Boolean(event.actor && (event.text || event.scope || event.sender));
  }
  return false;
}

function actorNumber(actor) {
  const matched = String(actor || "").match(/(\d+)$/);
  return matched ? Number.parseInt(matched[1], 10) : 9999;
}

function displayActor(actor) {
  if (!actor || actor === "undefined") return "系统";
  if (String(actor).startsWith("Player_")) return `${actorNumber(actor)}号玩家`;
  return String(actor);
}

function scopeStyle(scope) {
  return state.scopeStyles[scope] || ["#f3f4f6", "#9ca3af", ""];
}

function scopeLabel(scope) {
  const eventScope = scope || "host";
  const style = scopeStyle(eventScope);
  return `${eventScope}${style[2] ? ` · ${style[2]}` : ""}`;
}

function scopeColor(scope) {
  return scopeStyle(scope || "host")[1];
}

function roleName(actor) {
  const player = state.playerState[actor] || {};
  if (!player.role) return "未知身份";
  return displayRole(player.role);
}

function displayRole(role) {
  const label = state.roleBadges[role] || defaultRoleBadges[role] || role || "未知身份";
  return String(label).replace(/^[^\u4e00-\u9fa5A-Za-z0-9]+\s*/, "");
}

function roleTone(role) {
  const known = [
    "werewolf",
    "seer",
    "witch",
    "hunter",
    "guard",
    "idiot",
    "cupid",
    "villager",
  ];
  return known.includes(role) ? role : "unknown";
}

function roleFaction(actor) {
  const player = state.playerState[actor] || {};
  if (!player.role) return "neutral";
  return player.role === "werewolf" ? "wolf" : "good";
}

function isPlayerActorName(actor) {
  return /^Player_\d+$/.test(String(actor || ""));
}

function ensurePlayer(actor) {
  // 只有真实玩家席位可以进入 playerState。主持人/系统事件必须走 narration/moderator 通道。
  // Only real player seats may enter playerState. Moderator/system events must stay
  // in narration/moderator channels, otherwise they render as 9999号/13号 fake seats.
  if (!isPlayerActorName(actor)) return null;
  if (!state.playerState[actor]) {
    state.playerState[actor] = {
      name: actor,
      role: "",
      alive: true,
      events: [],
      nP: 0,
      nA: 0,
      bubbleText: "",
      bubbleExpiresAt: 0,
      bubbleQueue: [],
      bubbleActive: false,
    };
    state.playerOrder.push(actor);
    if (!state.selectedActor) state.selectedActor = actor;
  }
  return state.playerState[actor];
}

function orderedPlayers() {
  return state.playerOrder.filter(isPlayerActorName).slice().sort((a, b) => {
    const left = actorNumber(a);
    const right = actorNumber(b);
    if (left !== right) return left - right;
    return state.playerOrder.indexOf(a) - state.playerOrder.indexOf(b);
  });
}

function setStatus(kind, text) {
  state.phaseText = text;
  el("gameStatus").textContent = text;
  el("topPhase").textContent = text;
  el("tableCoreStatus").textContent = text;
}

function switchView(name) {
  const showingCards = name === "cards";
  const app = document.querySelector(".app");
  if (app) app.classList.toggle("history-collapsed", showingCards);
  el("tableView").classList.toggle("hidden", showingCards);
  el("cardsView").classList.toggle("hidden", !showingCards);
  el("debugView").classList.toggle("hidden", showingCards);
  el("tableTab").classList.toggle("on", name === "table");
  el("debugTab").classList.toggle("on", name === "debug");
  el("cardsTab").classList.toggle("on", showingCards);
  if (name === "debug" && !state.selectedActor) {
    const first = orderedPlayers()[0];
    if (first) state.selectedActor = first;
  }
  renderAllViews();
}

function seatLayout(index, count) {
  const leftCount = Math.ceil(count / 2);
  const rightCount = count - leftCount;
  const isLeft = index < leftCount;
  const sideCount = isLeft ? leftCount : rightCount;
  const rowIndex = isLeft ? index : index - leftCount;
  const edgePadding = sideCount <= 4 ? 10 : 6;
  const usableRange = 100 - edgePadding * 2;
  const y = sideCount <= 1
    ? 50
    : edgePadding + (rowIndex / (sideCount - 1)) * usableRange;

  return {
    side: isLeft ? "left" : "right",
    x: isLeft ? 0 : 100,
    y,
  };
}

function selectActor(actor) {
  state.selectedActor = actor;
  renderAllViews();
}

function resetConversation() {
  state.conversationCount = 0;
  el("conversationMessages").innerHTML = `
    <div class="message">
      <div class="mini-avatar"><span class="face"></span></div>
      <div>
        <div class="message-meta"><strong>系统</strong><span>--</span></div>
        <p>等待随机分配角色。</p>
      </div>
    </div>
  `;
  el("currentSpeakerMeta").textContent = "等待中";
  el("currentSpeakerTitle").textContent = "等待中";
  el("currentSpeakerSub").textContent = "尚未开始";
  el("speakerNotes").textContent = "点击座位或右侧玩家列表查看详情。";
}

function updateSpeaker(actor, text) {
  el("currentSpeakerMeta").textContent = displayActor(actor);
  el("currentSpeakerTitle").textContent = displayActor(actor);
  el("currentSpeakerSub").textContent = `${roleName(actor)} · ${state.phaseText}`;
  el("speakerNotes").textContent = text || "暂无发言内容";
}

function appendConversation(event) {
  if (event.type !== "narration" && event.type !== "act") return;
  const box = el("conversationMessages");
  if (state.conversationCount === 0) box.innerHTML = "";
  state.conversationCount += 1;

  const isAct = event.type === "act";
  const row = document.createElement("div");
  row.className = `message${isAct ? " active" : ""}`;
  const eventScope = event.scope || (isAct ? "发言" : "host");
  row.style.setProperty("--scope-color", scopeColor(eventScope));
  row.innerHTML = `
    <div class="mini-avatar"><span class="face"></span></div>
    <div>
      <div class="message-meta">
        <strong>${esc(isAct ? displayActor(event.actor) : "主持人")}</strong>
        <span>${esc(event.seq == null ? "--" : String(event.seq).padStart(2, "0"))}</span>
        ${!isAct ? `<span class="message-scope">${esc(scopeLabel(eventScope))}</span>` : ""}
      </div>
      <p>${esc(displayValue(event.text))}</p>
    </div>
  `;
  box.appendChild(row);
  while (box.children.length > 80) {
    box.removeChild(box.firstElementChild);
  }
  box.scrollTop = box.scrollHeight;
}

function shouldHideMechanicalActionBubble(text) {
  // 气泡表达“玩家刚刚说了什么”，不要因为文本包含查验/守护/投票等动作词就跳过。
  // Speech bubbles represent what a player just said. Do not hide natural player
  // utterances merely because they contain action words such as 查验/守护/投票.
  const value = displayValue(text).trim();
  if (!value || value === "无") return true;
  if (value.startsWith('{') && value.includes('"action"')) return true;
  // 系统广播的第三人称转述不显示为玩家气泡；玩家第一人称“我选择...”仍然显示。
  // Hide third-person system paraphrases, while keeping first-person “我选择...” speech.
  if (/^【Player_\d+｜[^】]+】该玩家/.test(value)) return true;
  return false;
}

function shouldShowSpeechBubble(text) {
  return !shouldHideMechanicalActionBubble(text);
}

function latestBubble(actor) {
  const player = state.playerState[actor];
  if (!player) return "";
  // 桌面气泡只显示正在播放的消息；5 秒后必须消失，不再从历史发言兜底恢复。
  // Table bubbles only show the currently playing message. They must disappear
  // after 5 seconds instead of falling back to historical speech.
  if (player.bubbleText && Date.now() < player.bubbleExpiresAt) {
    return player.bubbleText;
  }
  return "";
}

function speechEventKey(actor, text, scope) {
  const normalizedText = displayValue(text).trim();
  return `${actor}|${scope || ""}|${normalizedText}`;
}

function isInitialReplayEvent(seq) {
  const eventSeq = Number(seq || 0);
  return Boolean(state.initialReplaySeqMax && eventSeq && eventSeq <= state.initialReplaySeqMax);
}

function activateNextBubble(actor) {
  const player = ensurePlayer(actor);
  if (!player || player.bubbleActive) return;
  const nextBubble = player.bubbleQueue.shift();
  if (!nextBubble) {
    player.bubbleText = "";
    player.bubbleExpiresAt = 0;
    renderRoundTable();
    return;
  }
  player.bubbleActive = true;
  player.bubbleText = nextBubble.text;
  player.bubbleExpiresAt = Date.now() + state.bubbleDurationMs;
  renderRoundTable();
  if (state.bubbleTimers[actor]) window.clearTimeout(state.bubbleTimers[actor]);
  state.bubbleTimers[actor] = window.setTimeout(() => {
    player.bubbleActive = false;
    player.bubbleText = "";
    player.bubbleExpiresAt = 0;
    delete state.bubbleTimers[actor];
    renderRoundTable();
    if (player.bubbleQueue.length > 0) {
      state.bubbleTimers[actor] = window.setTimeout(() => {
        delete state.bubbleTimers[actor];
        activateNextBubble(actor);
      }, state.bubbleQueueGapMs);
    }
  }, state.bubbleDurationMs);
}

function showBubble(actor, text, seq, traceSeq, scope) {
  const player = ensurePlayer(actor);
  if (!player) return;
  const bubbleText = displayValue(text);
  if (!shouldShowSpeechBubble(bubbleText)) return;
  const key = speechEventKey(actor, bubbleText, scope);
  const eventSeq = Number(seq || traceSeq || 0);
  const previousSeq = Number(state.seenSpeechKeys.get(key) || 0);
  const duplicateWindow = Math.max(8, orderedPlayers().length + 2);
  if (previousSeq && eventSeq && eventSeq - previousSeq <= duplicateWindow) return;
  state.seenSpeechKeys.set(key, eventSeq || Date.now());
  if (isInitialReplayEvent(seq)) {
    return;
  }
  player.bubbleQueue.push({text: bubbleText, seq: seq || traceSeq || Date.now()});
  activateNextBubble(actor);
}

function renderRoundTable() {
  const table = el("roundTable");
  Array.from(table.querySelectorAll(".seat")).forEach((node) => node.remove());

  const actors = orderedPlayers();
  actors.forEach((actor, index) => {
    const player = state.playerState[actor];
    const pos = seatLayout(index, actors.length);
    const button = document.createElement("button");
    const faction = roleFaction(actor);
    button.type = "button";
    button.className = [
      "seat",
      faction === "wolf" ? "wolf" : "",
      faction === "good" ? "good" : "",
      faction === "neutral" ? "neutral" : "",
      player.alive === false ? "dead" : "",
      actor === state.selectedActor ? "selected" : "",
      pos.side === "left" ? "seat-left bubble-right" : "seat-right bubble-left",
    ].filter(Boolean).join(" ");
    button.style.left = `${pos.x}%`;
    button.style.top = `${pos.y}%`;
    button.onclick = () => selectActor(actor);

    const seatNo = actorNumber(actor) === 9999 ? index + 1 : actorNumber(actor);
    const bubble = latestBubble(actor);
    const isSpeaking = player.events.length > 0 && player.events[player.events.length - 1].type === "act";
    if (bubble) button.classList.add("has-bubble");
    button.innerHTML = `
      <span class="seat-frame">
        <span class="seat-no">${seatNo}号</span>
        <span class="seat-ring">
          <span class="avatar-head">
            <span class="avatar-hair"></span>
            <span class="avatar-ear left"></span>
            <span class="avatar-ear right"></span>
            <span class="avatar-face">
              <i class="avatar-brow left"></i>
              <i class="avatar-brow right"></i>
              <i class="avatar-blush left"></i>
              <i class="avatar-blush right"></i>
              <i class="avatar-mouth"></i>
            </span>
          </span>
        </span>
        <span class="seat-name">${esc(actor)}</span>
      </span>
      ${bubble ? `<span class="bubble${isSpeaking ? " speaking" : ""}">${esc(bubble)}</span>` : ""}
    `;
    table.appendChild(button);
  });
}

function renderDebugList() {
  const box = el("debugList");
  box.innerHTML = "";
  orderedPlayers().forEach((actor) => {
    const player = state.playerState[actor];
    const button = document.createElement("button");
    button.type = "button";
    button.className = `debug-player${actor === state.selectedActor ? " active" : ""}`;
    button.onclick = () => {
      state.selectedActor = actor;
      renderAllViews();
    };
    button.innerHTML = `
      <span class="player-tile-no">${esc(displayActor(actor).replace("玩家", ""))}</span>
      <b class="role-pill role-${esc(roleTone(player.role))}">${esc(roleName(actor))}</b>
      <small>听 ${player.nP}<br>说 ${player.nA}</small>
    `;
    box.appendChild(button);
  });
}

function historyItem(event) {
  const time = event.seq == null ? "--" : String(event.seq).padStart(2, "0");
  const actor = event.type === "act" ? "我" : (event.sender || event.scope || "系统");
  return `
    <div class="history-item ${event.type === "act" ? "speech" : "heard"}">
      <small>${esc(time)}</small>
      <div><span class="actor">${esc(actor)}：</span>${esc(displayValue(event.text))}</div>
    </div>
  `;
}

function renderHistory() {
  if (!state.selectedActor || !state.playerState[state.selectedActor]) {
    el("overviewPanel").classList.remove("hidden");
    el("flowPanel").classList.remove("hidden");
    el("historyPanel").classList.add("hidden");
    return;
  }

  el("overviewPanel").classList.add("hidden");
  el("flowPanel").classList.add("hidden");
  el("historyPanel").classList.remove("hidden");
  const player = state.playerState[state.selectedActor];
  const heard = player.events.filter((event) => event.type === "perceive");
  const speech = player.events.filter((event) => event.type === "act");
  const rows = state.historyMode === "heard" ? heard : speech;
  el("historyTitle").textContent = `玩家历史：${state.selectedActor.replace("Player_", "")}号`;
  el("heardTab").classList.toggle("on", state.historyMode === "heard");
  el("speechTab").classList.toggle("on", state.historyMode === "speech");
  el("history").innerHTML = rows.length
    ? rows.map(historyItem).join("")
    : "<div class=\"empty\">暂无记录</div>";
}

function renderAllViews() {
  renderRoundTable();
  renderDebugList();
  renderRoleSummary();
  renderHistory();
  renderSystemPluginCards();
}

class ViewPluginRegistry {
  constructor() {
    this.plugins = [];
  }

  register(plugin) {
    this.plugins.push(plugin);
    this.plugins.sort((a, b) => (b.priority || 0) - (a.priority || 0));
  }

  resolve(event) {
    return this.plugins.find((plugin) => {
      if (typeof plugin.canRender === "function") return plugin.canRender(event);
      return (plugin.kinds || []).includes(event.view_kind);
    }) || fallbackViewPlugin;
  }
}

class ViewHost {
  constructor(root, registry) {
    this.root = root;
    this.registry = registry;
    this.nodes = new Map();
  }

  render(events) {
    const ordered = events.slice().sort((a, b) => {
      const priorityDiff = Number(b.priority || 0) - Number(a.priority || 0);
      if (priorityDiff !== 0) return priorityDiff;
      return String(a.view_id).localeCompare(String(b.view_id));
    });
    ordered.forEach((event) => this.update(event));
    Array.from(this.nodes.keys()).forEach((id) => {
      if (!ordered.some((event) => event.view_id === id)) {
        const node = this.nodes.get(id);
        if (node) node.remove();
        this.nodes.delete(id);
      }
    });
    el("pluginCount").textContent = String(ordered.length);
  }

  update(event) {
    const plugin = this.registry.resolve(event);
    let node = this.nodes.get(event.view_id);
    if (!node) {
      node = document.createElement("article");
      node.className = "plugin-card";
      node.dataset.viewId = event.view_id;
      this.nodes.set(event.view_id, node);
      this.root.appendChild(node);
      if (typeof plugin.create === "function") plugin.create(node, event, this.context());
    }
    node.dataset.private = event.private ? "true" : "false";
    node.dataset.plugin = plugin.id;
    plugin.update(node, event, this.context());
  }

  context() {
    return {
      displayActor: (name) => String(name || "").replace("Player_", "") + (String(name || "").startsWith("Player_") ? "号" : ""),
      displayValue,
    };
  }
}

function pluginShell(event, body) {
  return `
    <div class="plugin-title">
      <strong>${esc(event.title || event.view_id)}</strong>
      <span>${esc(event.view_kind || "view")}</span>
    </div>
    ${body}
  `;
}

function viewData(event) {
  return event && event.data && typeof event.data === "object" ? event.data : {};
}

function arrayValue(value) {
  return Array.isArray(value) ? value : [];
}

function textValue(data) {
  return data.text ?? data.content ?? data.body ?? data.value ?? data.message ?? "";
}

function markdownHtml(text) {
  const raw = String(text || "").trim();
  if (!raw) return "<div class=\"empty\">暂无内容</div>";
  return raw.split(/\n{2,}/).map((block) => {
    const lines = block.split(/\n/);
    const isList = lines.every((line) => /^\s*[-*]\s+/.test(line));
    if (isList) {
      return `<ul>${lines.map((line) => `<li>${esc(line.replace(/^\s*[-*]\s+/, ""))}</li>`).join("")}</ul>`;
    }
    return `<p>${esc(block)}</p>`;
  }).join("");
}

function tableColumns(data, rows) {
  const configured = arrayValue(data.columns);
  if (configured.length) {
    return configured.map((column) => {
      if (typeof column === "string") return { key: column, label: column };
      return {
        key: String(column.key || column.name || column.label || ""),
        label: String(column.label || column.name || column.key || ""),
      };
    }).filter((column) => column.key);
  }
  const firstObject = rows.find((row) => row && typeof row === "object" && !Array.isArray(row));
  return firstObject
    ? Object.keys(firstObject).map((key) => ({ key, label: key }))
    : [];
}

function tableCell(row, column, index) {
  if (Array.isArray(row)) return row[index];
  if (row && typeof row === "object") return row[column.key];
  return index === 0 ? row : "";
}

function boardRows(data) {
  if (Array.isArray(data.rows)) return data.rows;
  if (Array.isArray(data.grid)) return data.grid;
  if (Array.isArray(data.board)) return data.board;
  if (data.board && typeof data.board === "object") {
    return boardDictRows(
      data.board,
      Number(data.rows_count || data.height || data.size || 15),
      Number(data.cols_count || data.width || data.size || 15),
    );
  }
  return [];
}

function boardDictRows(board, rowCount, colCount) {
  const safeRows = Number.isFinite(rowCount) && rowCount > 0 ? Math.min(rowCount, 30) : 15;
  const safeCols = Number.isFinite(colCount) && colCount > 0 ? Math.min(colCount, 30) : safeRows;
  const rows = Array.from({ length: safeRows }, () => Array.from({ length: safeCols }, () => ""));
  Object.entries(board || {}).forEach(([key, value]) => {
    const parts = String(key).split(",");
    if (parts.length !== 2) return;
    const row = Number(parts[0]);
    const col = Number(parts[1]);
    if (!Number.isInteger(row) || !Number.isInteger(col)) return;
    if (row < 0 || col < 0 || row >= safeRows || col >= safeCols) return;
    rows[row][col] = value;
  });
  return rows;
}

const keyValueViewPlugin = {
  id: "core.key-value",
  kinds: ["key-value"],
  priority: 100,
  update(container, event, context) {
    const data = viewData(event);
    const rows = arrayValue(data.rows).map((row) => {
      const label = row && typeof row === "object" ? row.label : "";
      const value = row && typeof row === "object" ? row.value : row;
      return `
        <div class="kv-row">
          <span>${esc(label)}</span>
          <b>${esc(context.displayValue(value))}</b>
        </div>
      `;
    }).join("");
    const progress = typeof data.progress === "number"
      ? `<div class="progress"><i style="width:${Math.max(0, Math.min(100, data.progress))}%"></i></div>`
      : "";
    container.innerHTML = pluginShell(event, `<div class="list-box">${rows || "<div class=\"empty\">暂无数据</div>"}${progress}</div>`);
  },
};

const textViewPlugin = {
  id: "core.text",
  kinds: ["text"],
  priority: 100,
  update(container, event) {
    const text = String(textValue(viewData(event)) || "").trim();
    container.innerHTML = pluginShell(event, `
      <div class="text-view">${text ? esc(text) : "<div class=\"empty\">暂无内容</div>"}</div>
    `);
  },
};

const markdownViewPlugin = {
  id: "core.markdown",
  kinds: ["markdown"],
  priority: 100,
  update(container, event) {
    container.innerHTML = pluginShell(event, `
      <div class="markdown-view">${markdownHtml(textValue(viewData(event)))}</div>
    `);
  },
};

const tableViewPlugin = {
  id: "core.table",
  kinds: ["table"],
  priority: 100,
  update(container, event, context) {
    const data = viewData(event);
    const rows = arrayValue(data.rows);
    const columns = tableColumns(data, rows);
    const head = columns.map((column) => `<th>${esc(column.label)}</th>`).join("");
    const body = rows.map((row) => `
      <tr>
        ${columns.map((column, index) => `<td>${esc(context.displayValue(tableCell(row, column, index)))}</td>`).join("")}
      </tr>
    `).join("");
    const table = columns.length
      ? `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
      : "<div class=\"empty\">暂无表格数据</div>";
    container.innerHTML = pluginShell(event, `<div class="table-scroll">${table}</div>`);
  },
};

const listViewPlugin = {
  id: "core.list",
  kinds: ["list"],
  priority: 100,
  update(container, event, context) {
    const data = viewData(event);
    const configuredItems = arrayValue(data.items);
    const items = configuredItems.length ? configuredItems : arrayValue(data.rows);
    const body = items.map((item) => {
      if (item && typeof item === "object") {
        const label = item.label || item.title || item.name || item.key || "";
        const value = item.value ?? item.detail ?? item.description ?? item.count ?? "";
        return `
          <div class="list-item">
            <b>${esc(label)}</b>
            <span>${esc(context.displayValue(value))}</span>
          </div>
        `;
      }
      return `<div class="list-item"><span>${esc(context.displayValue(item))}</span></div>`;
    }).join("");
    container.innerHTML = pluginShell(event, `<div class="list-view">${body || "<div class=\"empty\">暂无列表数据</div>"}</div>`);
  },
};

const boardViewPlugin = {
  id: "core.board",
  kinds: ["board"],
  priority: 100,
  update(container, event, context) {
    const data = viewData(event);
    const rows = boardRows(data);
    const width = Number(data.width || (rows[0] || []).length || 0);
    const cells = rows.flatMap((row) => Array.isArray(row) ? row : []);
    const body = cells.map((cell) => {
      const value = cell && typeof cell === "object" ? (cell.value ?? cell.label ?? cell.piece ?? "") : cell;
      const tone = cell && typeof cell === "object" ? (cell.tone || cell.color || "") : "";
      return `<div class="board-cell ${esc(tone)}">${esc(context.displayValue(value))}</div>`;
    }).join("");
    const grid = rows.length && width
      ? `<div class="board-view" style="grid-template-columns:repeat(${Math.max(1, width)},minmax(0,1fr))">${body}</div>`
      : "<div class=\"empty\">暂无棋盘数据</div>";
    container.innerHTML = pluginShell(event, grid);
  },
};

const cardsViewPlugin = {
  id: "core.cards",
  kinds: ["cards"],
  priority: 100,
  update(container, event, context) {
    const data = viewData(event);
    const configuredCards = arrayValue(data.cards);
    const cards = configuredCards.length ? configuredCards : arrayValue(data.items);
    const body = cards.map((card) => {
      const title = card && typeof card === "object"
        ? (card.title || [card.rank, card.suit].filter(Boolean).join(" ") || card.name || card.id || "")
        : card;
      const detail = card && typeof card === "object" ? (card.text || card.detail || card.owner || "") : "";
      return `
        <div class="mini-card">
          <b>${esc(context.displayValue(title))}</b>
          ${detail ? `<span>${esc(context.displayValue(detail))}</span>` : ""}
        </div>
      `;
    }).join("");
    container.innerHTML = pluginShell(event, `<div class="cards-view">${body || "<div class=\"empty\">暂无卡牌</div>"}</div>`);
  },
};

const playerListViewPlugin = {
  id: "core.player-list",
  kinds: ["player-list"],
  priority: 100,
  update(container, event) {
    const data = viewData(event);
    const groups = arrayValue(data.groups).map((group) => `
      <div class="card-section">
        <div class="kv-row"><span>${esc(group.label)}</span><b>${(group.players || []).length}人</b></div>
        <div class="player-list">
          ${(group.players || []).map((player) => `<span class="chip ${esc(group.tone || "")}">${esc(player)}</span>`).join("")}
        </div>
      </div>
    `).join("");
    container.innerHTML = pluginShell(event, groups || "<div class=\"empty\">暂无玩家</div>");
  },
};

const tallyViewPlugin = {
  id: "core.tally",
  kinds: ["tally", "vote-summary"],
  priority: 100,
  update(container, event, context) {
    const data = viewData(event);
    const configuredRows = arrayValue(data.rows);
    const rows = (configuredRows.length ? configuredRows : arrayValue(data.votes)).map((row) => {
      const target = row && typeof row === "object" ? (row.target ?? row.label ?? row.option) : row;
      const value = row && typeof row === "object" ? (row.value ?? row.votes ?? 0) : 0;
      return `
        <div class="vote-cell">
          <b>${esc(context.displayValue(target))}</b>
          <span>${esc(context.displayValue(value))}</span>
        </div>
      `;
    }).join("");
    container.innerHTML = pluginShell(event, `
      <div class="vote-grid">${rows || "<div class=\"empty\">暂无计数</div>"}</div>
      ${data.note ? `<p class="plugin-note">${esc(data.note)}</p>` : ""}
    `);
  },
};

const resourceListViewPlugin = {
  id: "core.resource-list",
  kinds: ["resource-list", "inventory"],
  priority: 100,
  update(container, event, context) {
    const data = viewData(event);
    const items = arrayValue(data.items).map((item) => {
      const label = item && typeof item === "object" ? (item.label || item.key) : item;
      const count = item && typeof item === "object" ? item.count : "";
      return `
        <div class="inventory-row">
          <div class="item-left">
            <span class="item-icon">${esc(String(label || "?").slice(0, 1))}</span>
            <b>${esc(label)}</b>
          </div>
          <span class="count-pill ${Number(count) === 0 ? "zero" : ""}">剩余：${esc(context.displayValue(count))}</span>
        </div>
      `;
    }).join("");
    container.innerHTML = pluginShell(event, `<div class="list-box">${items || "<div class=\"empty\">暂无资源</div>"}</div>`);
  },
};

const timelineViewPlugin = {
  id: "core.timeline",
  kinds: ["timeline"],
  priority: 100,
  update(container, event) {
    const data = viewData(event);
    const items = arrayValue(data.items).map((item) => `
      <div class="timeline-item">
        <small>${esc(item.time || "")}</small>
        <strong>${esc(item.title || "")}</strong>
        <span>${esc(item.detail || "")}</span>
      </div>
    `).join("");
    container.innerHTML = pluginShell(event, `<div class="list-box">${items || "<div class=\"empty\">暂无时间线</div>"}</div>`);
  },
};

const fallbackViewPlugin = {
  id: "core.fallback",
  kinds: ["*"],
  priority: 0,
  update(container, event) {
    container.innerHTML = pluginShell(event, `
      <div class="list-box">
        <pre class="json-dump">${esc(JSON.stringify(event.data || {}, null, 2))}</pre>
      </div>
    `);
  },
};

function createViewHost() {
  const registry = new ViewPluginRegistry();
  registry.register(keyValueViewPlugin);
  registry.register(textViewPlugin);
  registry.register(markdownViewPlugin);
  registry.register(tableViewPlugin);
  registry.register(listViewPlugin);
  registry.register(boardViewPlugin);
  registry.register(cardsViewPlugin);
  registry.register(playerListViewPlugin);
  registry.register(tallyViewPlugin);
  registry.register(resourceListViewPlugin);
  registry.register(timelineViewPlugin);
  state.viewHost = new ViewHost(el("pluginStack"), registry);
}

function renderPluginEvents() {
  if (!state.viewHost) return;
  state.viewHost.render(Object.values(state.viewEvents));
}

function renderSystemPluginCards() {
  const players = orderedPlayers();
  const alive = players.filter((actor) => state.playerState[actor].alive !== false);
  const dead = players.filter((actor) => state.playerState[actor].alive === false);
  state.viewEvents["core-phase-info"] = {
    type: "__view__",
    kind: "__view__",
    view_id: "core-phase-info",
    view_kind: "key-value",
    title: "阶段信息",
    audience: "public",
    priority: -10,
    data: {
      rows: [
        { label: "当前阶段", value: state.phaseText },
        { label: "回合", value: state.roundNo },
        { label: "白天", value: state.dayNo },
        { label: "夜晚", value: state.nightNo },
      ],
    },
  };
  state.viewEvents["core-player-status"] = {
    type: "__view__",
    kind: "__view__",
    view_id: "core-player-status",
    view_kind: "player-list",
    title: "玩家状态",
    audience: "public",
    priority: -20,
    data: {
      groups: [
        { label: "存活", players: alive.map((actor) => `${actorNumber(actor)}号`), tone: "green" },
        { label: "出局", players: dead.map((actor) => `${actorNumber(actor)}号`), tone: "red" },
      ],
    },
  };
  renderPluginEvents();
}

function inferPhaseFromText(text) {
  const value = text || "";
  if (value.includes("天黑") || value.includes("夜晚") || value.includes("狼人们")) {
    if (state.phaseText !== "夜晚阶段") {
      state.roundNo += 1;
      state.nightNo += 1;
    }
    return "夜晚阶段";
  }
  if (value.includes("天亮")) {
    state.dayNo = Math.max(state.dayNo + 1, state.nightNo);
    return "白天阶段";
  }
  if (value.includes("发言") || value.includes("讨论")) return "白天发言阶段";
  if (value.includes("投票")) return "投票阶段";
  if (value.includes("获胜") || value.includes("胜利")) return "结果公布";
  return "";
}

function applyPhaseText(text) {
  const next = inferPhaseFromText(text);
  if (next) {
    state.phaseText = next;
    el("gameStatus").textContent = next;
    el("topPhase").textContent = next;
    el("tableCoreStatus").textContent = next;
  }
  el("roundNo").textContent = String(state.roundNo);
  el("dayNo").textContent = String(state.dayNo);
  el("nightNo").textContent = String(state.nightNo);
}

function renderRoleSummary() {
  const counts = {};
  orderedPlayers().forEach((actor) => {
    const role = state.playerState[actor].role || "unknown";
    counts[role] = (counts[role] || 0) + 1;
  });
  const roles = Object.keys(counts);
  const title = roles.length ? "角色分配" : "角色分配（未分配）";
  el("overviewPanel").querySelector("h2").textContent = title;
  el("roleSummary").innerHTML = roles.length
    ? roles.map((role) => `
      <div class="role-row">
        <span class="role-label"><i class="dot ${role === "werewolf" ? "wolf" : (role === "unknown" ? "neutral" : "good")}"></i>${esc(displayRole(role))}</span>
        <span class="role-count">× ${counts[role]}</span>
      </div>
    `).join("")
    : `
      <div class="role-row">
        <span class="role-label"><i class="dot neutral"></i>等待随机分配</span>
        <span class="role-count">× 0</span>
      </div>
    `;
  const count = orderedPlayers().length;
  if (count) {
    el("playerCountSelect").innerHTML = `<option>${count} 人局</option>`;
  }
}

function recordPlayerEvent(event) {
  if (event.type !== "perceive" && event.type !== "act") return;
  const player = ensurePlayer(event.actor);
  if (!player) return;
  player.events.push(event);
  if (event.type === "perceive") {
    player.nP += 1;
    if (isPlayerActorName(event.sender)) {
      // 私密职业动作（whisper:seer/guard/witch）通常是 sender == actor 的自听事件；
      // 也要触发对应职业玩家的圆桌气泡。短窗口去重会避免同一句话多次投递重复起泡。
      // Private role actions are often self-perceive events (sender == actor);
      // they still need table bubbles for the role player. Short-window dedupe
      // prevents repeated deliveries of the same speech from bubbling multiple times.
      showBubble(event.sender, event.text, event.seq, event.trace_seq, event.scope);
      if (shouldShowSpeechBubble(event.text)) {
        // Dashboard speaker card should follow the actual speaking player, not only
        // act events. Role whispers are delivered as self-perceive events, so keep
        // the right-side speaker card in sync with the same speech that bubbles.
        // Dashboard 右侧发言卡跟随真正说话的玩家；职业私聊常以自听 perceive 到达。
        updateSpeaker(event.sender, displayValue(event.text));
      }
    }
  }
  if (event.type === "act") {
    player.nA += 1;
    updateSpeaker(event.actor, displayValue(event.text));
    showBubble(event.actor, event.text, event.seq, event.trace_seq, event.scope);
  }
  renderAllViews();
}

function ensureModerator() {
  if (state.moderator) return state.moderator;
  const node = document.createElement("div");
  node.className = "card moderator";
  node.innerHTML = `
    <div class="card-head">
      <span class="actor-name">主持人 · 控场</span>
      <span class="counts">0 条流程发言</span>
    </div>
    <div class="card-body"></div>
  `;
  el("moderator").appendChild(node);
  state.moderator = {
    body: node.querySelector(".card-body"),
    countsEl: node.querySelector(".counts"),
    n: 0,
  };
  return state.moderator;
}

function ensureCard(actor) {
  if (state.cards[actor]) return state.cards[actor];
  const node = document.createElement("div");
  node.className = "card";
  node.dataset.order = String(state.orderCounter);
  node.dataset.firstact = String(1e9);
  state.orderCounter += 1;
  node.innerHTML = `
    <div class="card-head">
      <span class="actor-name">${esc(actor)}</span>
      <span class="badge-slot"></span>
      <span class="status-slot"></span>
      <span class="counts">听到 0 · 发言 0</span>
    </div>
    <div class="card-body"></div>
  `;
  el("grid").appendChild(node);
  state.cards[actor] = {
    el: node,
    body: node.querySelector(".card-body"),
    countsEl: node.querySelector(".counts"),
    badge: node.querySelector(".badge-slot"),
    statusEl: node.querySelector(".status-slot"),
    nP: 0,
    nA: 0,
  };
  return state.cards[actor];
}

function renderEvent(event) {
  const div = document.createElement("div");
  if (event.type === "act") {
    div.className = "evt act";
    div.innerHTML = `<div class="evt-head">我说</div><div class="evt-body">${esc(displayValue(event.text))}</div>`;
    return div;
  }
  const eventScope = event.scope || "host";
  const label = scopeLabel(eventScope);
  div.className = "evt perceive";
  div.style.setProperty("--scope-color", scopeColor(eventScope));
  div.innerHTML = `
    <div class="evt-head">
      <span class="scope-tag">${esc(label)}</span>
      ${event.sender ? `<span class="sender">${esc(event.sender)}</span>` : ""}
    </div>
    <div class="evt-body">${esc(displayValue(event.text))}</div>
  `;
  return div;
}

function applyRoles(roles) {
  Object.keys(roles || {}).forEach((actor) => {
    if (!isPlayerActorName(actor)) return;
    const player = ensurePlayer(actor);
    const info = roles[actor] || {};
    if (player) {
      player.role = info.role || player.role;
      if (typeof info.alive === "boolean") player.alive = info.alive;
    }
    const card = state.cards[actor];
    if (card) {
      if (info.role) {
        card.badge.className = "role-badge";
        card.badge.textContent = displayRole(info.role);
      }
      if (info.alive === true) {
        card.statusEl.className = "status alive";
        card.statusEl.textContent = "存活";
      } else if (info.alive === false) {
        card.statusEl.className = "status dead";
        card.statusEl.textContent = "出局";
      }
    }
  });
  renderAllViews();
}

function addEvent(rawEvent) {
  const event = normalizeEvent(rawEvent);
  if (event.type === "__reset__" || event.type === "session_restarted") {
    clearBoard();
    markAssigning();
    return;
  }
  if (event.type === "__assigned__" || event.type === "session_assigned") {
    markAssigned();
    return;
  }
  if (event.type === "__started__" || event.type === "session_started") {
    markStarted();
    return;
  }
  if (event.type === "__ended__" || event.type === "session_ended") {
    markEnded();
    return;
  }
  if (event.type === "__paused__" || event.type === "session_paused" || event.type === "gate_paused") {
    markPaused(true);
    return;
  }
  if (event.type === "__resumed__" || event.type === "session_resumed" || event.type === "gate_resumed") {
    markPaused(false);
    return;
  }
  if (event.type === "__step_mode__") {
    markStepMode(Boolean(event.enabled));
    return;
  }
  if (event.type === "__meta__" || event.type === "roles_snapshot") {
    applyRoles(event.roles);
    return;
  }
  if (event.type === "__view__") {
    state.viewEvents[event.view_id] = event;
    renderPluginEvents();
    return;
  }
  if (event.type === "__step__") return;
  if (!hasDialoguePayload(event)) {
    return;
  }
  if (event.seq != null && state.seen.has(event.seq)) return;
  if (event.seq != null) state.seen.add(event.seq);
  recordPlayerEvent(event);

  if (event.type === "narration") {
    applyPhaseText(event.text || "");
    appendConversation(event);
    const moderator = ensureModerator();
    moderator.body.appendChild(renderEvent(event));
    moderator.body.scrollTop = moderator.body.scrollHeight;
    moderator.n += 1;
    moderator.countsEl.textContent = `${moderator.n} 条流程发言`;
    return;
  }

  if (event.type === "act") {
    appendConversation(event);
  }

  const card = ensureCard(event.actor);
  if (!card) return;
  card.body.appendChild(renderEvent(event));
  card.body.scrollTop = card.body.scrollHeight;
  if (event.type === "perceive") card.nP += 1;
  if (event.type === "act") {
    card.nA += 1;
    if (card.el.dataset.firstact === String(1e9)) card.el.dataset.firstact = String(event.seq);
  }
  card.countsEl.textContent = `听到 ${card.nP} · 发言 ${card.nA}`;
}

function clearBoard() {
  stopAutoStep();
  state.seen.clear();
  state.cards = {};
  state.playerState = {};
  state.playerOrder = [];
  state.moderator = null;
  state.orderCounter = 0;
  state.selectedActor = null;
  Object.values(state.bubbleTimers).forEach((timer) => window.clearTimeout(timer));
  state.bubbleTimers = {};
  state.seenSpeechKeys.clear();
  state.initialReplaySeqMax = 0;
  state.historyMode = "heard";
  state.phaseText = "分配中";
  state.roundNo = 0;
  state.dayNo = 0;
  state.nightNo = 0;
  state.conversationCount = 0;
  state.viewEvents = {};
  el("grid").innerHTML = "";
  el("moderator").innerHTML = "";
  el("pluginStack").innerHTML = "";
  resetConversation();
  if (state.viewHost) state.viewHost.nodes = new Map();
  applyPhaseText("");
  renderAllViews();
}

function markAssigning() {
  state.assigned = false;
  state.started = false;
  el("assignBtn").disabled = true;
  el("assignBtn").textContent = "分配中...";
  el("startBtn").disabled = true;
  el("restartBtn").disabled = true;
  setStatus("off", "分配中");
}

function markAssigned() {
  state.assigned = true;
  state.started = false;
  el("assignBtn").disabled = false;
  el("assignBtn").textContent = "重新随机分配";
  el("startBtn").disabled = false;
  el("restartBtn").disabled = false;
  setStatus("off", "已分配，待开始");
}

function markStarted() {
  state.started = true;
  state.paused = false;
  el("assignBtn").disabled = true;
  el("startBtn").disabled = true;
  el("startBtn").textContent = "游戏进行中";
  el("pauseBtn").disabled = false;
  el("restartBtn").disabled = false;
  refreshRunControls();
  refreshRunStatus();
}

function markEnded() {
  state.started = false;
  state.assigned = false;
  state.paused = false;
  stopAutoStep();
  el("assignBtn").disabled = false;
  el("assignBtn").textContent = "随机分配角色";
  el("startBtn").disabled = true;
  el("startBtn").textContent = "开始游戏";
  el("pauseBtn").disabled = true;
  el("pauseBtn").textContent = "暂停";
  el("pauseBtn").classList.remove("on");
  el("nextStepBtn").disabled = true;
  el("autoStepBtn").disabled = true;
  el("restartBtn").disabled = false;
  setStatus("off", "本局结束");
}

function refreshRunControls() {
  el("pauseBtn").textContent = state.paused ? "继续" : "暂停";
  el("pauseBtn").classList.toggle("on", state.paused);
  el("nextStepBtn").disabled = !(state.started && state.stepMode && !state.paused);
  el("autoStepBtn").disabled = !(state.started && state.stepMode && !state.paused);
}

function refreshRunStatus() {
  if (!state.started) return;
  if (state.paused) {
    setStatus("off", state.stepMode ? "单步暂停" : "已暂停");
    return;
  }
  setStatus(state.stepMode ? "off" : "live", state.stepMode ? "单步模式" : "进行中");
}

function markPaused(paused) {
  state.paused = Boolean(paused);
  if (state.paused) stopAutoStep();
  refreshRunControls();
  refreshRunStatus();
}

function markStepMode(enabled) {
  state.stepMode = Boolean(enabled);
  el("stepModeBtn").classList.toggle("on", state.stepMode);
  el("stepModeBtn").textContent = state.stepMode ? "退出单步" : "单步模式";
  if (!state.stepMode) stopAutoStep();
  refreshRunControls();
  refreshRunStatus();
}

function applyStepGate(gate) {
  state.stepGate = gate || {};
  markStepMode(Boolean(state.stepGate.step_mode));
  const waiting = Number(state.stepGate.waiting_count || 0);
  const permits = Number(state.stepGate.permits || 0);
  if (state.stepMode) {
    el("currentSpeakerSub").textContent = `单步模式 · 等待 ${waiting} · 许可 ${permits}`;
  }
}

function stepIntervalMs() {
  const input = el("stepInterval");
  let value = Number.parseInt(input.value, 10);
  if (!Number.isFinite(value)) value = 1000;
  value = Math.max(200, Math.min(10000, value));
  input.value = String(value);
  return value;
}

function toggleAutoStep() {
  if (state.autoStepTimer !== null) {
    stopAutoStep();
    return;
  }
  if (!state.started || !state.stepMode) return;
  el("autoStepBtn").textContent = "暂停自动单步";
  el("autoStepBtn").classList.add("on");
  state.autoStepTimer = window.setInterval(nextStep, stepIntervalMs());
}

function stopAutoStep() {
  if (state.autoStepTimer !== null) {
    window.clearInterval(state.autoStepTimer);
    state.autoStepTimer = null;
  }
  el("autoStepBtn").textContent = "自动单步";
  el("autoStepBtn").classList.remove("on");
}

function restartAutoStepIfNeeded() {
  if (state.autoStepTimer === null) return;
  stopAutoStep();
  toggleAutoStep();
}

function assignRoles() {
  if (state.assigned || state.started) {
    restartGame();
    return;
  }
  markAssigning();
  postSession("/assign").catch((error) => {
    el("assignBtn").disabled = false;
    el("assignBtn").textContent = "随机分配角色";
    alert("分配失败：" + error.message);
  });
}

function startGame() {
  el("startBtn").disabled = true;
  el("startBtn").textContent = "启动中...";
  postSession("/start").catch(() => {
    el("startBtn").disabled = false;
    el("startBtn").textContent = "开始游戏";
  });
}

function togglePause() {
  const wasPaused = state.paused;
  const nextPaused = !wasPaused;
  if (nextPaused) stopAutoStep();
  markPaused(nextPaused);
  postSession(wasPaused ? "/resume" : "/pause").catch(() => {
    markPaused(wasPaused);
  });
}

async function toggleStepMode() {
  const enabled = !state.stepMode;
  try {
    const response = await postSession("/step-mode?enabled=" + encodeURIComponent(enabled ? "true" : "false"));
    const result = await response.json();
    applyStepGate(result.gate || {});
  } catch (error) {
    alert("切换单步模式失败：" + error.message);
  }
}

async function nextStep() {
  if (!state.stepMode) return;
  try {
    const response = await postSession("/step?count=1");
    const result = await response.json();
    applyStepGate(result.gate || {});
  } catch (error) {
    alert("单步执行失败：" + error.message);
  }
}

function restartGame() {
  if (!confirm("确定清空当前局并重新发牌？玩家链接和真人席位会保留。")) return;
  stopAutoStep();
  clearBoard();
  markAssigning();
  postSession("/restart").catch((error) => {
    alert("重新开始失败：" + error.message);
    syncHostSnapshot();
  });
}

async function loadJoinSlots() {
  try {
    const response = await fetch(sessionApi("/seats"));
    const payload = await response.json();
    const slots = Array.isArray(payload)
      ? payload.filter((seat) => seat.controller_type === "human" && seat.join_link)
      : Object.keys(payload).map((name) => ({seat_id: name, join_link: payload[name]}));
    el("joinSlots").innerHTML = "";
    el("joinPanel").classList.remove("hidden");
    if (slots.length === 0) {
      el("joinSlots").innerHTML = "<div class=\"join-empty\">当前没有可接管的真人玩家。</div>";
      return;
    }
    slots.forEach((seat) => {
      const button = document.createElement("button");
      button.className = "join-btn";
      button.textContent = `扮演 ${seat.seat_id}`;
      button.onclick = () => window.open(seat.join_link, "_blank");
      el("joinSlots").appendChild(button);
    });
  } catch {
    el("joinPanel").classList.remove("hidden");
    el("joinSlots").innerHTML = "<div class=\"join-empty\">真人玩家入口暂时不可用。</div>";
  }
}

function renderLegend() {
  Object.keys(state.scopeStyles).forEach((name) => {
    const style = state.scopeStyles[name];
    const span = document.createElement("span");
    span.className = "legend-item";
    span.style.background = style[0];
    span.textContent = `${name} · ${style[2]}`;
    el("legend").appendChild(span);
  });
}

async function initConfig() {
  const response = await fetch("/api/frontend/config");
  const config = await response.json();
  document.title = config.title;
  el("pageTitle").textContent = config.title;
  el("versionSelect").innerHTML = `<option>${esc(config.title || "当前 YAML")}</option>`;
  state.scopeStyles = config.scopeStyles || {};
  state.roleBadges = config.roleBadges || {};
  state.moderatorKey = config.moderatorKey || "__moderator__";
  renderLegend();
}

function bindControls() {
  el("assignBtn").onclick = assignRoles;
  el("startBtn").onclick = startGame;
  el("pauseBtn").onclick = togglePause;
  el("stepModeBtn").onclick = toggleStepMode;
  el("nextStepBtn").onclick = nextStep;
  el("autoStepBtn").onclick = toggleAutoStep;
  el("restartBtn").onclick = restartGame;
  el("stepInterval").onchange = restartAutoStepIfNeeded;
  el("tableTab").onclick = () => {
    state.selectedActor = null;
    switchView("table");
  };
  el("debugTab").onclick = () => switchView("debug");
  el("cardsTab").onclick = () => switchView("cards");
  el("closeHistoryBtn").onclick = () => {
    state.selectedActor = null;
    switchView("table");
  };
  el("heardTab").onclick = () => {
    state.historyMode = "heard";
    renderHistory();
  };
  el("speechTab").onclick = () => {
    state.historyMode = "speech";
    renderHistory();
  };
}

function connectEvents() {
  state.hasConnectedEvents = true;
  const source = new EventSource(sessionApi("/events/host"));
  source.onopen = () => {
    el("connStatus").textContent = "已连接";
  };
  source.onerror = () => {
    el("connStatus").textContent = "连接断开";
  };
  source.onmessage = (message) => {
    try {
      addEvent(JSON.parse(message.data));
    } catch {
      // Ignore malformed frames.
    }
  };
}

async function loadModeratorPanels() {
  const pendingPanel = el("pendingPanel");
  if (!pendingPanel) return;
  // ── seats 列表（控制方式/认领状态/角色快照）──────────────────────
  try {
    const resp = await fetch(sessionApi("/seats"));
    if (resp.ok) {
      const seats = await resp.json();
      const box = el("seatsList");
      box.innerHTML = "";
      let humanCount = 0;
      if (!seats || seats.length === 0) {
        box.innerHTML = "<div class=\"join-empty\">暂无 seat 信息。</div>";
      } else {
        seats.forEach((s) => {
          if (s.controller_type === "human") humanCount += 1;
          const row = document.createElement("div");
          row.className = "seat-row";
          const ctl = s.controller_type === "human" ? "真人" : (s.controller_type === "ai" ? "AI" : "Mock");
          const claim = s.claim_status || "";
          const role = s.role_snapshot || "-";
          const alive = s.alive_snapshot ? "" : "(出局)";
          row.innerHTML =
            "<span class=\"seat-name\">" + esc(s.seat_id) + "</span>" +
            "<span class=\"seat-tag\">" + ctl + "</span>" +
            "<span class=\"seat-tag\">" + esc(claim) + "</span>" +
            "<span class=\"seat-role\">" + esc(role) + alive + "</span>";
          const toggleBtn = document.createElement("button");
          toggleBtn.className = "mod-btn";
          toggleBtn.textContent = s.controller_type === "human" ? "切 AI" : "切真人";
          toggleBtn.onclick = () => moderatorSetController(
            s.seat_id, s.controller_type === "human" ? "ai" : "human");
          row.appendChild(toggleBtn);
          // 真人 seat 提供打开/重置链接按钮 / Human seat: open / reset link
          if (s.controller_type === "human" && s.join_link) {
            const openBtn = document.createElement("button");
            openBtn.className = "mod-btn";
            openBtn.textContent = "打开链接";
            openBtn.onclick = () => window.open(s.join_link, "_blank");
            row.appendChild(openBtn);
            const resetBtn = document.createElement("button");
            resetBtn.className = "mod-btn";
            resetBtn.textContent = "重置链接";
            resetBtn.onclick = () => moderatorResetLink(s.seat_id);
            row.appendChild(resetBtn);
          }
          box.appendChild(row);
        });
        // 同步真人数量输入框 / Sync human count input
        const countInput = el("humanCountInput");
        if (countInput) countInput.value = humanCount;
      }
    }
  } catch {
    /* 忽略 / ignore */
  }

  // ── pending actions（谁未提交/剩余时间/代操作/AI 接管）──────────────
  try {
    const resp = await fetch(sessionApi("/pending-actions"));
    if (resp.ok) {
      const items = await resp.json();
      const box = el("pendingList");
      box.innerHTML = "";
      if (!items || items.length === 0) {
        box.innerHTML = "<div class=\"join-empty\">暂无等待中的动作。</div>";
        return;
      }
      items.forEach((p) => {
        const row = document.createElement("div");
        row.className = "pending-row";
        const status = p.submitted ? "已提交(" + (p.submission_source || "") + ")" : "等待中";
        row.innerHTML =
          "<span class=\"seat-name\">" + esc(p.seat_id) + "</span>" +
          "<span class=\"seat-tag\">" + esc(p.type || p.kind || "") + "</span>" +
          "<span class=\"seat-tag\">" + status + "</span>" +
          "<span class=\"seat-role\">" + esc((p.cue || "").slice(0, 30)) + "</span>";
        if (!p.submitted) {
          const takeBtn = document.createElement("button");
          takeBtn.className = "mod-btn";
          takeBtn.textContent = "AI 接管";
          takeBtn.onclick = () => moderatorTakeover(p.seat_id);
          row.appendChild(takeBtn);

          const submitBtn = document.createElement("button");
          submitBtn.className = "mod-btn";
          submitBtn.textContent = "代操作";
          submitBtn.onclick = () => moderatorSubmit(p.seat_id, p.type || p.kind, p.cue);
          row.appendChild(submitBtn);
        }
        box.appendChild(row);
      });
    }
  } catch {
    /* 忽略 / ignore */
  }
}

async function moderatorTakeover(seatId) {
  if (!confirm("确定将 " + seatId + " 切换为 AI 接管？")) return;
  await postSession("/moderator/takeover?seat=" + encodeURIComponent(seatId));
  loadModeratorPanels();
}

async function moderatorResetLink(seatId) {
  if (!confirm("确定重置 " + seatId + " 的加入链接？旧链接立即失效。")) return;
  const resp = await postSession("/moderator/reset-link?seat=" + encodeURIComponent(seatId));
  const result = await resp.json();
  const link = result.join_link || result.url || "";
  if (result.ok && link) {
    prompt("新加入链接：", link);
  }
  loadModeratorPanels();
  loadJoinSlots();
}

async function moderatorSubmit(seatId, kind, cue) {
  // 代操作：根据 kind 提示主持人输入 / Submit on behalf: prompt moderator input based on kind
  var data = {};
  var text = "";
  if (kind === "speech") {
    text = prompt("代 " + seatId + " 发言（输入文本）：") || "";
    if (!text) return;
    data = {"text": text};
  } else if (kind === "vote" || kind === "night_action") {
    var target = prompt("代 " + seatId + " 选择目标（输入 seat 名，空=弃权/不行动）：") || "";
    if (!target) {
      data = {"action": false, "target": null};
      text = "弃权（主持人代操作）";
    } else {
      data = {"target": target, "vote": target, "action": true, "reason": "主持人代操作"};
      text = "选择 " + target + "（主持人代操作）";
    }
  } else {
    text = prompt("代 " + seatId + " 操作文本：") || "";
    if (!text) return;
    data = {"text": text};
  }
  try {
    const response = await postSession("/moderator/submit?seat=" + encodeURIComponent(seatId), {data: data, text: text});
    const result = await response.json();
    if (result.validation_error) {
      alert("提交未通过校验：" + result.validation_error);
    }
  } catch (error) {
    alert("代操作失败：" + error.message);
  }
  loadModeratorPanels();
}

async function moderatorTerminate() {
  if (!confirm("确定终止当前会话？所有游戏进程将被中止。")) return;
  await postSession("/terminate");
  loadModeratorPanels();
}

async function moderatorSetController(seatId, controllerType) {
  await postSession("/moderator/set-controller?seat=" + encodeURIComponent(seatId)
    + "&controller=" + encodeURIComponent(controllerType));
  loadModeratorPanels();
  loadJoinSlots();
}

async function applyHumanCount() {
  const count = el("humanCountInput").value || "0";
  await postSession("/moderator/set-human-count?count=" + encodeURIComponent(count));
  loadModeratorPanels();
  loadJoinSlots();
}


async function syncHostSnapshot() {
  if (!serviceContext.sessionId) return;
  try {
    const response = await fetch(sessionApi("/view/host"));
    if (!response.ok) return;
    const snapshot = await response.json();
    if (!state.hasConnectedEvents && Array.isArray(snapshot.timeline)) {
      state.initialReplaySeqMax = snapshot.timeline.reduce((maxSeq, event) => {
        const eventSeq = Number(event.seq || 0);
        return eventSeq > maxSeq ? eventSeq : maxSeq;
      }, state.initialReplaySeqMax);
    }
    applySessionStatus(snapshot.session_status);
    if (snapshot.meta && snapshot.meta.step_gate) {
      applyStepGate(snapshot.meta.step_gate);
    }
    if (snapshot.seats) {
      const roles = {};
      snapshot.seats.forEach((seat) => {
        ensurePlayer(seat.seat_id);
        roles[seat.seat_id] = {role: seat.role_snapshot || "", alive: seat.alive_snapshot !== false};
      });
      applyRoles(roles);
    }
  } catch {
    /* 快照同步失败时不打断 SSE / Ignore snapshot sync failure. */
  }
}

function applySessionStatus(status) {
  if (status === "lobby") {
    state.assigned = false;
    state.started = false;
    setStatus("off", "大厅等待中");
    el("assignBtn").disabled = false;
    el("startBtn").disabled = true;
  } else if (status === "assigned") {
    markAssigned();
  } else if (status === "running") {
    markStarted();
  } else if (status === "paused") {
    markStarted();
    markPaused(true);
  } else if (["ended", "failed", "terminated"].includes(status)) {
    markEnded();
  }
}

async function main() {
  createViewHost();
  bindControls();
  initDashboardColumnResizers();
  await initConfig();
  await loadJoinSlots();
  window.setInterval(loadJoinSlots, 3000);
  if (el("pendingPanel")) {
    await loadModeratorPanels();
    window.setInterval(loadModeratorPanels, 2000);
  }
  await syncHostSnapshot();
  window.setInterval(syncHostSnapshot, 2000);
  connectEvents();
  renderAllViews();
}

main();

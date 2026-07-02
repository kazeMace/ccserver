function el(id) { return document.getElementById(id); }

const AVAILABLE_SCRIPTS = {
  werewolf_v1_12p_guard: {
    label: "预女猎守局12人",
    gameId: "werewolf_v1_12p_guard",
    presetPath: "drama_engine/scripts/presets/deduction/werewolf/werewolf_v1_12p_guard.preset.yaml",
    scriptPath: "drama_engine/scripts/fixed_flow/deduction/werewolf_v1_guard.yaml",
    totalPlayers: 12,
    params: {total_players: 12, werewolf_count: 4, dry_run: false, use_runner: true}
  }
};

let latestSession = null;

async function createSession() {
  const scriptId = el("scriptSelect").value || "werewolf_v1_12p_guard";
  const script = AVAILABLE_SCRIPTS[scriptId];
  const humanCount = normalizeHumanCount(el("humanCountInput").value);
  const humanSeats = Array.from({length: humanCount}, (_, index) => `Player_${index + 1}`);
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      game_id: script.gameId,
      script_path: script.scriptPath,
      seat_ids: Array.from({length: script.totalPlayers}, (_, index) => `Player_${index + 1}`),
      human_seat_ids: humanSeats,
      params: script.params,
      metadata: {preset_path: script.presetPath, preset_label: script.label}
    })
  });
  if (!response.ok) {
    alert("创建失败：" + await response.text());
    return;
  }
  latestSession = await response.json();
  renderCreatedRoom(script, humanSeats, latestSession);
}

function normalizeHumanCount(value) {
  let count = Number.parseInt(value || "0", 10);
  if (!Number.isFinite(count)) count = 0;
  count = Math.max(0, Math.min(12, count));
  el("humanCountInput").value = String(count);
  return count;
}

function updateHumanCountMode() {
  const count = normalizeHumanCount(el("humanCountInput").value);
  const hint = el("humanCountHint");
  const button = el("createRoomBtn");
  if (count === 0) {
    hint.textContent = "0 人：观战模式，全部玩家由真实 Agent 托管。";
    button.textContent = "创建观战模式房间";
  } else {
    hint.textContent = `${count} 人真人：Player_1 到 Player_${count} 使用玩家链接进入，其余由真实 Agent 托管。`;
    button.textContent = "创建房间";
  }
}

function renderCreatedRoom(script, humanSeats, session) {
  const panel = el("createdRoomPanel");
  const summary = el("createdRoomSummary");
  const links = el("playerLinks");
  const hostUrl = `/host/sessions/${encodeURIComponent(session.session_id)}`;
  panel.classList.remove("hidden");
  summary.innerHTML = `
    <div><strong>剧本：</strong>${escapeHtml(script.label)}</div>
    <div><strong>Session：</strong><code>${escapeHtml(session.session_id)}</code></div>
    <div><strong>真人玩家：</strong>${humanSeats.length} / ${script.totalPlayers}</div>
  `;
  links.innerHTML = "";
  if (humanSeats.length === 0) {
    links.innerHTML = '<div class="join-empty">本房间没有真人玩家链接。</div>';
  } else {
    humanSeats.forEach((seatId) => {
      const link = session.player_links[seatId];
      const row = document.createElement("div");
      row.className = "player-link-row";
      row.innerHTML = `
        <span>${escapeHtml(seatId)}</span>
        <a href="${escapeAttr(link)}" target="_blank" rel="noreferrer">进入玩家视角</a>
        <button type="button" class="mod-btn">复制链接</button>
      `;
      row.querySelector("button").onclick = () => copyText(link);
      links.appendChild(row);
    });
  }
  el("openHostBtn").onclick = () => { window.location.href = session.host_url || hostUrl; };
  panel.scrollIntoView({behavior: "smooth", block: "start"});
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    prompt("复制链接：", text);
  }
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value == null ? "" : String(value);
  return node.innerHTML;
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, "&quot;");
}

function initCreatePage() {
  if (!el("createRoomBtn")) return;
  updateHumanCountMode();
  el("humanCountInput").oninput = updateHumanCountMode;
  el("humanCountInput").onchange = updateHumanCountMode;
  el("createRoomBtn").onclick = createSession;
}

initCreatePage();

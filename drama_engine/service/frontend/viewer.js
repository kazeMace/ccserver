const pathMatch = window.location.pathname.match(/\/viewer\/sessions\/([^/]+)/);
const sessionId = pathMatch ? decodeURIComponent(pathMatch[1]) : new URLSearchParams(window.location.search).get("session_id");

function el(id) { return document.getElementById(id); }
function esc(value) { const node = document.createElement("div"); node.textContent = value == null ? "" : String(value); return node.innerHTML; }

async function loadPublicView() {
  if (!sessionId) { el("status").textContent = "缺少 session_id"; return; }
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/view/public`);
  if (!response.ok) { el("status").textContent = "连接失败"; return; }
  const snapshot = await response.json();
  el("status").textContent = snapshot.session_status;
  el("seats").innerHTML = (snapshot.seats || []).map((seat) => `
    <div class="seat"><strong>${esc(seat.seat_id)}</strong><br>${seat.alive_snapshot === false ? "出局" : "存活/未知"}</div>
  `).join("");
  el("timeline").innerHTML = (snapshot.timeline || []).map((event) => `
    <div class="event">${esc(event.text || event.type || event.kind || JSON.stringify(event))}</div>
  `).join("") || "<div class='muted'>暂无公开事件</div>";
}

loadPublicView();
window.setInterval(loadPublicView, 3000);

const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
let latestSnapshot = null;
let renderedActionKey = "";
let latestActionError = "";

function el(id) { return document.getElementById(id); }
function esc(value) {
  const node = document.createElement("div");
  node.textContent = value == null ? "" : String(value);
  return node.innerHTML;
}

async function loadPlayerView() {
  if (!token) {
    el("status").textContent = "缺少 token";
    return;
  }
  const response = await fetch(`/api/player/view?token=${encodeURIComponent(token)}`);
  if (!response.ok) {
    el("status").textContent = "连接失败";
    return;
  }
  latestSnapshot = await response.json();
  renderSnapshot(latestSnapshot);
}

function renderSnapshot(snapshot) {
  el("status").textContent = snapshot.session_status;
  renderRole(snapshot.role_card);
  renderScopes(snapshot.visible_scopes || []);
  renderSeats(snapshot.seats || []);
  renderTimeline(snapshot.timeline || []);
  renderAction(snapshot.current_action);
}

function renderRole(role) {
  if (!role) {
    el("roleCard").innerHTML = "等待发牌";
    return;
  }
  el("roleCard").innerHTML = `
    <strong>${esc(role.title || role.role)}</strong><br>
    角色：${esc(role.role)}<br>
    阵营：${esc(role.faction)}<br>
    状态：${role.alive === false ? "出局" : "存活"}
  `;
}

function renderScopes(scopes) {
  el("scopes").innerHTML = scopes.map((scope) => `<span class="tag">${esc(scope)}</span>`).join("");
}

function renderSeats(seats) {
  el("seats").innerHTML = seats.map((seat) => `
    <div class="seat">
      <strong>${esc(seat.seat_id)}</strong><br>
      ${seat.alive_snapshot === false ? "出局" : "存活/未知"}
      ${seat.role_snapshot ? `<br>身份：${esc(seat.role_snapshot)}` : ""}
    </div>
  `).join("");
}


function normalizeEvent(rawEvent) {
  const event = Object.assign({}, rawEvent || {});
  event.type = event.type || event.kind || "";
  event.kind = event.kind || event.type;
  return event;
}

function eventText(rawEvent) {
  const event = normalizeEvent(rawEvent);
  if (event.type === "actor_profile") return `身份信息：${event.role_display_name || event.role || "未知"}`;
  if (event.type === "human_input_request") return `需要操作：${event.cue || ""}`;
  if (event.type === "perceive") return `[${event.scope || ""}] ${event.sender || "系统"}: ${event.text || ""}`;
  if (event.type === "submission_accepted") return `已提交：${event.text || JSON.stringify(event.data || {})}`;
  return `${event.type}: ${event.text || event.cue || JSON.stringify(event)}`;
}

function renderTimeline(events) {
  el("timeline").innerHTML = events.map((event) => `<div class="event">${esc(eventText(event))}</div>`).join("") || "<div class='muted'>暂无消息</div>";
}

function renderAction(action) {
  const box = el("actionBox");
  if (!action) {
    renderedActionKey = "";
    box.className = "muted";
    box.innerHTML = "暂无待处理操作";
    return;
  }
  const nextActionKey = actionRenderKey(action);
  if (renderedActionKey === nextActionKey && el("actionForm")) {
    // 同一个 pending request 轮询刷新时不要重建表单，否则正在输入的文本会被清空。
    // Do not rebuild the form for the same pending request during polling,
    // otherwise in-progress typing loses content and focus.
    return;
  }
  renderedActionKey = nextActionKey;
  box.className = "";
  const fieldName = inferFieldName(action);
  const control = buildActionControl(action, fieldName);
  const reasonControl = buildReasonControl(action);
  box.innerHTML = `
    <form class="action-form" id="actionForm">
      <div><strong>${esc(actionLabel(action))}</strong></div>
      ${buildActionMeta(action)}
      <div class="action-cue">${esc(action.cue || "请操作")}</div>
      <div id="actionError" class="action-error" style="${latestActionError ? "" : "display:none"}">${esc(latestActionError)}</div>
      ${control}
      ${reasonControl}
      <button type="submit">提交</button>
    </form>
  `;
  const mode = el("actionMode");
  if (mode) mode.onchange = () => updateCustomInputVisibility();
  updateCustomInputVisibility();
  el("actionForm").onsubmit = async (event) => {
    event.preventDefault();
    try {
      const raw = readActionValue(action, fieldName);
      const note = (el("actionReason") && el("actionReason").value.trim()) || "";
      const data = buildSubmissionData(action, fieldName, raw, note);
      await submitAction(data, submissionText(action, fieldName, raw, note));
    } catch (error) {
      showActionError(error.message || String(error));
    }
  };
}

function buildActionMeta(action) {
  const items = [];
  const scene = action.scene_display_name || action.scene_name || "";
  if (scene) items.push(`场景：${esc(scene)}`);
  const kind = action.kind || action.type || "";
  if (kind) items.push(`类型：${esc(kind)}`);
  const deadline = deadlineText(action);
  if (deadline) items.push(deadline);
  if (action.allow_resubmit) items.push("允许重复提交");
  if (!items.length) return "";
  return `<div class="action-meta">${items.map((item) => `<span>${item}</span>`).join("")}</div>`;
}

function deadlineText(action) {
  if (action.deadline_at == null) return "";
  const timeout = action.timeout_seconds == null ? "" : `${Math.round(Number(action.timeout_seconds))} 秒`;
  return timeout ? `限时：${esc(timeout)}` : "有限时";
}

function actionRenderKey(action) {
  const requestId = action.request_id || "";
  if (requestId) return `request:${requestId}`;
  const candidates = JSON.stringify(action.candidates || []);
  const schemaKeys = Object.keys((action.schema && action.schema.properties) || {}).join(",");
  return [action.scene_name || "", action.kind || action.type || "", action.cue || "", schemaKeys, candidates].join("|");
}

function actionLabel(action) {
  const sceneName = action.scene_name || action.scene || "";
  const cue = String(action.cue || "");
  if (sceneName === "sheriff-join" || cue.includes("上警")) return "上警选择";
  return (action.type || action.kind) || "action";
}

function buildActionControl(action, fieldName) {
  const schema = action.schema || {};
  const properties = schema.properties || {};
  const cue = String(action.cue || "");
  const candidates = action.candidates || [];
  const hasAction = Object.prototype.hasOwnProperty.call(properties, "action");

  if (hasAction && cue.includes("上警")) {
    return `
      <label class="field-label" for="actionValue">请选择是否上警</label>
      <select id="actionValue">
        <option value="true">我选择上警</option>
        <option value="false">我选择不上警</option>
      </select>
    `;
  }
  if (hasAction && (cue.includes("是否") || cue.includes("是="))) {
    return `
      <label class="field-label" for="actionValue">请选择行动</label>
      <select id="actionValue">
        <option value="true">我选择是</option>
        <option value="false">我选择否</option>
      </select>
    `;
  }
  if (candidates.length) {
    return `
      <label class="field-label" for="actionValue">选择目标</label>
      <select id="actionValue">${candidates.map((item) => `<option value="${esc(item)}">${esc(item)}</option>`).join("")}</select>
      <label class="custom-toggle"><input id="actionMode" type="checkbox" /> 使用自定义输入</label>
      <input id="customActionValue" class="custom-action-value" placeholder="自定义输入，如 Player_1 或其他内容" />
    `;
  }
  if (((action.type || action.kind) || "").includes("vote") || ((action.type || action.kind) || "").includes("night")) {
    return `<label class="field-label" for="actionValue">输入目标</label><input id="actionValue" placeholder="输入目标，如 Player_1" />`;
  }
  if (fieldName === "action") {
    return `
      <label class="field-label" for="actionValue">请选择行动</label>
      <select id="actionValue"><option value="true">我选择是</option><option value="false">我选择否</option></select>
    `;
  }
  if (Object.keys(properties).length > 0) {
    return buildSchemaFields(action, fieldName);
  }
  return `<label class="field-label" for="actionValue">输入内容</label><textarea id="actionValue" placeholder="输入你的发言或操作"></textarea>`;
}

function buildSchemaFields(action, preferredFieldName) {
  const schema = action.schema || {};
  const properties = schema.properties || {};
  const required = new Set(schema.required || []);
  const keys = Object.keys(properties);
  const ordered = keys.includes(preferredFieldName)
    ? [preferredFieldName].concat(keys.filter((key) => key !== preferredFieldName))
    : keys;
  return `
    <div class="schema-grid">
      ${ordered.map((key) => buildSchemaField(key, properties[key] || {}, required.has(key))).join("")}
    </div>
  `;
}

function buildSchemaField(key, spec, required) {
  const label = esc(spec.title || key);
  const requiredMark = required ? " *" : "";
  const enumValues = spec.enum || spec.const;
  const description = spec.description ? `<div class="field-help">${esc(spec.description)}</div>` : "";
  if (Array.isArray(enumValues)) {
    return `
      <label class="field-label" for="schema_${esc(key)}">${label}${requiredMark}</label>
      <select id="schema_${esc(key)}" data-schema-field="${esc(key)}" data-schema-type="${esc(spec.type || "string")}">
        ${enumValues.map((item) => `<option value="${esc(item)}">${esc(item)}</option>`).join("")}
      </select>
      ${description}
    `;
  }
  if (spec.type === "boolean") {
    return `
      <label class="field-label" for="schema_${esc(key)}">${label}${requiredMark}</label>
      <select id="schema_${esc(key)}" data-schema-field="${esc(key)}" data-schema-type="boolean">
        <option value="true">是</option>
        <option value="false">否</option>
      </select>
      ${description}
    `;
  }
  if (spec.type === "array" || spec.type === "object") {
    return `
      <label class="field-label" for="schema_${esc(key)}">${label}${requiredMark}</label>
      <textarea id="schema_${esc(key)}" data-schema-field="${esc(key)}" data-schema-type="${esc(spec.type)}" placeholder='${spec.type === "array" ? "[...]" : "{...}"}'></textarea>
      ${description}
    `;
  }
  return `
    <label class="field-label" for="schema_${esc(key)}">${label}${requiredMark}</label>
    <input id="schema_${esc(key)}" data-schema-field="${esc(key)}" data-schema-type="${esc(spec.type || "string")}" />
    ${description}
  `;
}

function buildReasonControl(action) {
  const schema = action.schema || {};
  const properties = schema.properties || {};
  const requiresReason = Object.prototype.hasOwnProperty.call(properties, "reason");
  const label = requiresReason ? "理由 / 补充发言" : "补充发言（可选）";
  return `
    <label class="field-label" for="actionReason">${label}</label>
    <textarea id="actionReason" placeholder="可以补充你的理由、发言或备注"></textarea>
  `;
}

function updateCustomInputVisibility() {
  const mode = el("actionMode");
  const custom = el("customActionValue");
  if (!custom) return;
  custom.style.display = mode && mode.checked ? "block" : "none";
}

function readActionValue(action, fieldName) {
  const mode = el("actionMode");
  const custom = el("customActionValue");
  if (mode && mode.checked && custom) return custom.value.trim();
  const schemaInput = document.querySelector(`[data-schema-field="${CSS.escape(fieldName)}"]`);
  if (schemaInput) return schemaInput.value;
  const input = el("actionValue");
  return input ? input.value : "";
}

function inferFieldName(action) {
  const schema = action.schema || {};
  const properties = schema.properties || {};
  const keys = Object.keys(properties);
  const cue = String(action.cue || "");
  if (keys.includes("action") && (cue.includes("是否") || cue.includes("是=") || cue.includes("上警"))) return "action";
  if (keys.includes("vote")) return "vote";
  if (keys.includes("target")) return "target";
  if (keys.includes("action")) return "action";
  if (keys.includes("text")) return "text";
  if (keys.length > 0) return keys[0];
  if (((action.type || action.kind) || "").includes("vote")) return "vote";
  if (((action.type || action.kind) || "").includes("night")) return "target";
  return "text";
}

function normalizeBooleanValue(raw) {
  if (raw === true || raw === "true") return true;
  if (raw === false || raw === "false") return false;
  return Boolean(raw);
}

function buildSubmissionData(action, fieldName, raw, note) {
  const schema = action.schema || {};
  const properties = schema.properties || {};
  const schemaData = readSchemaSubmissionData(properties);
  if (Object.keys(schemaData).length) {
    if (note && Object.prototype.hasOwnProperty.call(properties, "reason") && !schemaData.reason) {
      schemaData.reason = note;
    }
    return schemaData;
  }
  const value = raw === "true" ? true : raw === "false" ? false : raw;
  const data = {};
  if (fieldName === "vote") {
    data.vote = value;
  } else if (fieldName === "target") {
    data.target = value || null;
    if (Object.prototype.hasOwnProperty.call(properties, "action")) data.action = Boolean(value);
  } else if (fieldName === "action") {
    data.action = normalizeBooleanValue(value);
  } else {
    data[fieldName] = value;
  }
  if (Object.prototype.hasOwnProperty.call(properties, "reason")) {
    data.reason = note || "真人玩家提交";
  }
  if (Object.prototype.hasOwnProperty.call(properties, "target") && !data.target && typeof value === "string" && value) {
    data.target = value;
  }
  if (Object.prototype.hasOwnProperty.call(properties, "vote") && !data.vote && typeof value === "string" && value) {
    data.vote = value;
  }
  return data;
}

function readSchemaSubmissionData(properties) {
  const fields = Array.from(document.querySelectorAll("[data-schema-field]"));
  const data = {};
  for (const field of fields) {
    const key = field.getAttribute("data-schema-field");
    const type = field.getAttribute("data-schema-type") || ((properties[key] || {}).type || "string");
    const raw = field.value;
    if (raw === "" && !(properties[key] || {}).default) continue;
    data[key] = coerceSchemaValue(raw, type);
  }
  return data;
}

function coerceSchemaValue(raw, type) {
  if (type === "boolean") return normalizeBooleanValue(raw);
  if (type === "integer") return Number.parseInt(raw, 10);
  if (type === "number") return Number(raw);
  if (type === "array" || type === "object") {
    try {
      return raw ? JSON.parse(raw) : (type === "array" ? [] : {});
    } catch (_) {
      latestActionError = "JSON 格式不正确，请检查数组或对象字段。";
      const errorBox = el("actionError");
      if (errorBox) {
        errorBox.style.display = "block";
        errorBox.textContent = latestActionError;
      }
      throw new Error(latestActionError);
    }
  }
  return raw;
}

function submissionText(action, fieldName, raw, note) {
  const cue = String(action.cue || "");
  if (fieldName === "action" && cue.includes("上警")) {
    const choice = normalizeBooleanValue(raw) ? "我选择上警" : "我选择不上警";
    return note ? `${choice}。${note}` : choice;
  }
  if (note) return note;
  return raw;
}

async function submitAction(data, text) {
  latestActionError = "";
  const response = await fetch("/api/player/input", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({token, data, text})
  });
  if (!response.ok) {
    latestActionError = "提交失败：" + await response.text();
    showActionError(latestActionError);
    return;
  }
  const result = await response.json();
  if (result.validation_error) {
    latestActionError = "提交未通过校验：" + result.validation_error;
    showActionError(latestActionError);
    return;
  }
  await loadPlayerView();
}

function showActionError(message) {
  const box = el("actionError");
  if (!box) {
    alert(message);
    return;
  }
  box.style.display = "block";
  box.textContent = message;
}

loadPlayerView();
window.setInterval(loadPlayerView, 2000);

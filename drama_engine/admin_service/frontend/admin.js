const state = { scripts: [], selectedScriptId: null, selectedInspection: null, selectedFlow: null };

const $ = (id) => document.getElementById(id);

function setStatus(message) {
  $('statusBox').textContent = message;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function formatStatus(value) {
  const map = {
    draft: '草稿',
    valid: '已检查',
    approved: '已发布',
    archived: '已归档',
    created: '已创建',
    assigned: '已发牌',
    running: '运行中',
    ended: '已结束',
    failed: '失败',
    terminated: '已终止',
  };
  return map[value] || value || '';
}

function formatSource(value) {
  const map = { builtin: '内置', uploaded: '上传' };
  return map[value] || value || '';
}

function formatLevel(value) {
  const map = { fatal: '致命', error: '错误', warning: '警告', info: '提示' };
  return map[value] || value || '';
}

function formatKey(value) {
  const map = {
    name: '名称',
    display_name: '显示名称',
    faction: '阵营',
    scopes: '可见域',
    abilities: '能力',
    inventory: '道具',
    index: '序号',
    type: '类型',
    scope: '可见域',
    turn_policy: '发言策略',
    has_when: '有条件',
    performers: '执行者',
    candidates: '候选项',
    effect_count: '效果数',
    path: '路径',
    initial_value: '初始值',
    written_by: '写入位置',
    read_by: '读取位置',
    source: '来源',
    code: '编码',
    message: '消息',
  };
  return map[value] || value;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { raw: text };
  }
  if (!response.ok) {
    const detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail || payload);
    throw new Error(detail);
  }
  return payload;
}

function showPanel(panelId) {
  document.querySelectorAll('.panel').forEach((el) => el.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach((el) => el.classList.remove('active'));
  $(panelId).classList.add('active');
  const btn = document.querySelector(`[data-panel="${panelId}"]`);
  if (btn) btn.classList.add('active');
}

async function loadScripts() {
  const payload = await api('/admin/api/scripts');
  state.scripts = payload.scripts || [];
  renderScripts();
}

function renderScripts() {
  const rows = state.scripts.map((script) => {
    const summary = script.last_validation_summary || {};
    const errors = (summary.fatal || 0) + (summary.error || 0);
    const warnings = summary.warning || 0;
    return `<tr>
      <td><strong>${esc(script.name)}</strong><br><span class="badge">${esc(script.script_id)}</span></td>
      <td><span class="badge ${esc(script.status)}">${esc(formatStatus(script.status))}</span></td>
      <td>${esc(formatSource(script.source))}</td>
      <td>${errors}</td>
      <td>${warnings}</td>
      <td>${esc(script.updated_at || '-')}</td>
      <td>
        <button class="small-btn" onclick="validateScript('${esc(script.script_id)}')">检查</button>
        <button class="small-btn" onclick="inspectScript('${esc(script.script_id)}')">查看</button>
        <button class="small-btn" onclick="createPlaytest('${esc(script.script_id)}')">试玩</button>
        <button class="small-btn" onclick="promoteScript('${esc(script.script_id)}')">发布</button>
      </td>
    </tr>`;
  }).join('');
  $('scriptsTbody').innerHTML = rows || '<tr><td colspan="7">暂无剧本。</td></tr>';
}

async function validateScript(scriptId) {
  setStatus(`正在检查 ${scriptId} ...`);
  const payload = await api(`/admin/api/scripts/${encodeURIComponent(scriptId)}/validate`, { method: 'POST' });
  setStatus(`检查完成：${JSON.stringify(payload.validation.summary)}`);
  await loadScripts();
}

async function promoteScript(scriptId) {
  setStatus(`正在发布 ${scriptId} ...`);
  try {
    const payload = await api(`/admin/api/scripts/${encodeURIComponent(scriptId)}/promote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: true }),
    });
    setStatus(`已发布：${payload.script.script_id}`);
  } catch (error) {
    setStatus(`发布失败：${error.message}`);
  }
  await loadScripts();
}

async function inspectScript(scriptId) {
  setStatus(`正在查看 ${scriptId} ...`);
  const [inspectionPayload, flowPayload] = await Promise.all([
    api(`/admin/api/scripts/${encodeURIComponent(scriptId)}/inspect`),
    api(`/admin/api/scripts/${encodeURIComponent(scriptId)}/flow`),
  ]);
  state.selectedScriptId = scriptId;
  state.selectedInspection = inspectionPayload.inspection;
  state.selectedFlow = flowPayload.flow;
  renderInspect(inspectionPayload.script);
  showPanel('inspectPanel');
  setStatus(`剧本检查已加载：${scriptId}`);
}

function renderInspect(script) {
  const data = state.selectedInspection;
  const flow = state.selectedFlow;
  $('inspectTitle').textContent = `检查剧本：${script.name}`;
  $('overviewTab').innerHTML = renderOverview(data.overview, data.players, data.issues.summary);
  $('rolesTab').innerHTML = renderTable(data.roles || []);
  $('scopesTab').innerHTML = renderTable((data.scopes || []).map((s) => ({ ...s, members: JSON.stringify(s.members) })));
  $('scenesTab').innerHTML = renderTable(data.scenes || []);
  $('issuesTab').innerHTML = renderIssues(data.issues.issues || []);
  $('sequenceTab').innerHTML = renderSequence(flow.sequence);
  $('stateTab').innerHTML = renderStateMachine(flow.state_machine);
  $('treeTab').innerHTML = `<div class="tree">${renderTree(flow.tree)}</div>`;
  $('rawTab').innerHTML = `<pre>${esc(data.raw_yaml || '')}</pre>`;
}

function renderOverview(overview, players, summary) {
  return `<div class="card">
    ${kv('标题', overview.title)}${kv('描述', overview.description)}${kv('玩家', `${players.count || '-'} / ${JSON.stringify(players.distribution || {})}`)}
    ${kv('角色数', overview.role_count)}${kv('可见域数', overview.scope_count)}${kv('场景数', overview.scene_count)}${kv('状态数', overview.state_count)}${kv('是否循环', overview.loop ? '是' : '否')}
    ${kv('检查结果', `致命=${summary.fatal}, 错误=${summary.error}, 警告=${summary.warning}, 提示=${summary.info}`)}
  </div>`;
}

function kv(k, v) {
  return `<div class="kv"><span>${esc(k)}</span><span>${esc(v)}</span></div>`;
}

function renderTable(rows) {
  if (!rows.length) return '<div class="card">暂无数据。</div>';
  const keys = Object.keys(rows[0]).filter((key) => key !== 'raw' && key !== 'brief' && key !== 'cue');
  return `<table><thead><tr>${keys.map((k) => `<th>${esc(formatKey(k))}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${keys.map((k) => `<td>${esc(Array.isArray(row[k]) ? row[k].join(', ') : JSON.stringify(row[k]) === '{}' ? '' : row[k])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

function renderIssues(issues) {
  if (!issues.length) return '<div class="card">暂无问题。</div>';
  return issues.map((issue) => `<div class="issue ${esc(issue.level)}"><strong>${esc(formatLevel(issue.level))} · ${esc(issue.code)}</strong><br>${esc(issue.message)}<br><span class="badge">${esc(issue.path || '-')}</span><p>${esc(issue.suggestion || '')}</p></div>`).join('');
}

function renderSequence(sequence) {
  return `<div class="grid"><div class="card"><h3>流程节点</h3><div class="flow-list">${(sequence.nodes || []).map((n) => `<div class="flow-node"><strong>${esc(n.index + 1)}. ${esc(n.label)}</strong><br><span class="badge">${esc(n.type)}</span> <span class="badge">${esc(n.scope)}</span></div>`).join('')}</div></div><div class="card"><h3>流程图源码</h3><pre>${esc(sequence.mermaid)}</pre></div></div>`;
}

function renderStateMachine(machine) {
  return `<div class="grid"><div class="card"><h3>状态</h3>${renderTable(machine.states || [])}</div><div class="card"><h3>流程图源码</h3><pre>${esc(machine.mermaid)}</pre></div><div class="card"><h3>状态问题</h3>${renderIssues(machine.issues || [])}</div></div>`;
}

function renderTree(node) {
  if (!node) return '';
  const children = (node.children || []).map(renderTree).join('');
  return `<ul><li><strong>${esc(node.label)}</strong> <span class="node-type">${esc(node.type || '')}</span>${children}</li></ul>`;
}

async function createPlaytest(scriptId) {
  const payload = await api(`/admin/api/scripts/${encodeURIComponent(scriptId)}/playtests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: 'dry_run', human_player_count: 0, step_mode: true }),
  });
  setStatus(`已创建试玩：${payload.playtest.playtest_id}`);
  showPanel('playtestsPanel');
  await loadPlaytests();
}

async function loadPlaytests() {
  const payload = await api('/admin/api/playtests');
  $('playtestsList').innerHTML = (payload.playtests || []).map((pt) => `<div class="card"><h3>${esc(pt.playtest_id)}</h3>${kv('剧本', pt.script_id)}${kv('状态', formatStatus(pt.status))}${kv('步数', pt.current_step)}<button class="small-btn" onclick="assignPlaytest('${esc(pt.playtest_id)}')">发牌</button><button class="small-btn" onclick="startPlaytest('${esc(pt.playtest_id)}')">开始</button><button class="small-btn" onclick="stepPlaytest('${esc(pt.playtest_id)}')">下一步</button><button class="small-btn" onclick="showRuntime('${esc(pt.playtest_id)}')">运行状态</button><pre>${esc((pt.events || []).slice(-8).map((e) => `[${e.step}] ${e.kind}: ${e.message}`).join('\n'))}</pre></div>`).join('') || '<div class="card">暂无试玩测试。</div>';
}

async function assignPlaytest(playtestId) {
  await api(`/admin/api/playtests/${encodeURIComponent(playtestId)}/assign`, { method: 'POST' });
  await loadPlaytests();
}

async function startPlaytest(playtestId) {
  await api(`/admin/api/playtests/${encodeURIComponent(playtestId)}/start`, { method: 'POST' });
  await loadPlaytests();
}

async function showRuntime(playtestId) {
  const payload = await api(`/admin/api/playtests/${encodeURIComponent(playtestId)}/runtime`);
  setStatus(JSON.stringify(payload.runtime, null, 2));
}

async function stepPlaytest(playtestId) {
  await api(`/admin/api/playtests/${encodeURIComponent(playtestId)}/step`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ count: 1 }),
  });
  await loadPlaytests();
}

async function loadPlugins() {
  const payload = await api('/admin/api/plugins');
  $('pluginsList').innerHTML = (payload.plugins || []).map((plugin) => `<div class="card"><h3>${esc(plugin.name)}</h3><p>${esc(plugin.description)}</p>${kv('编号', plugin.plugin_id)}${kv('类型', plugin.plugin_type)}<button class="small-btn" onclick="runPlugin('${esc(plugin.plugin_id)}')">运行</button></div>`).join('') || '<div class="card">暂无插件。</div>';
}

async function runPlugin(pluginId) {
  const payload = await api(`/admin/api/plugins/${encodeURIComponent(pluginId)}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: { selected_script_id: state.selectedScriptId } }),
  });
  setStatus(JSON.stringify(payload, null, 2));
}

document.addEventListener('click', (event) => {
  const nav = event.target.closest('.nav-btn');
  if (nav) showPanel(nav.dataset.panel);
  const tab = event.target.closest('.tab');
  if (tab) {
    document.querySelectorAll('.tab').forEach((el) => el.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach((el) => el.classList.remove('active'));
    tab.classList.add('active');
    $(tab.dataset.tab).classList.add('active');
  }
});

$('refreshBtn').addEventListener('click', async () => {
  await boot();
  setStatus('已刷新。');
});

$('uploadForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  setStatus('正在上传...');
  const payload = await api('/admin/api/scripts/upload', { method: 'POST', body: form });
  setStatus(`已上传 ${payload.script.script_id}。检查结果：${JSON.stringify(payload.validation.summary)}`);
  await loadScripts();
  await inspectScript(payload.script.script_id);
});

$('generateForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const prompt = $('generatePrompt').value.trim();
  if (!prompt) {
    setStatus('请先填写需求描述。');
    return;
  }
  const payload = await api('/admin/api/scripts/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
  });
  setStatus(`已生成草稿 ${payload.script.script_id}.\n${payload.notes.join('\n')}`);
  await loadScripts();
  await inspectScript(payload.script.script_id);
});

async function boot() {
  await Promise.all([loadScripts(), loadPlugins(), loadPlaytests()]);
}

boot().catch((error) => setStatus(`初始化失败：${error.message}`));

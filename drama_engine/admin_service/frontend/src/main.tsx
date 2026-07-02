import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, jsonRequest } from './api';
import { displayValue, formatKey, formatLevel, formatSource, formatStatus } from './format';
import { DslTree, FlowGraph } from './components/FlowGraph';
import type { FlowInspection, Issue, Playtest, PluginInfo, ScriptInspection, ScriptRecord } from './types';
import './styles.css';

type Panel = 'scriptsPanel' | 'uploadPanel' | 'inspectPanel' | 'generatePanel' | 'pluginsPanel' | 'playtestsPanel';
type Tab = 'overviewTab' | 'rolesTab' | 'scopesTab' | 'scenesTab' | 'issuesTab' | 'sequenceTab' | 'stateTab' | 'treeTab' | 'rawTab';

type InspectionState = {
  script: ScriptRecord;
  inspection: ScriptInspection;
  flow: FlowInspection;
};

function App() {
  const [panel, setPanel] = useState<Panel>('scriptsPanel');
  const [tab, setTab] = useState<Tab>('overviewTab');
  const [status, setStatus] = useState('就绪。');
  const [scripts, setScripts] = useState<ScriptRecord[]>([]);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [playtests, setPlaytests] = useState<Playtest[]>([]);
  const [selected, setSelected] = useState<InspectionState | null>(null);

  async function loadScripts() {
    const payload = await api<{ scripts: ScriptRecord[] }>('/admin/api/scripts');
    setScripts(payload.scripts || []);
  }

  async function loadPlugins() {
    const payload = await api<{ plugins: PluginInfo[] }>('/admin/api/plugins');
    setPlugins(payload.plugins || []);
  }

  async function loadPlaytests() {
    const payload = await api<{ playtests: Playtest[] }>('/admin/api/playtests');
    setPlaytests(payload.playtests || []);
  }

  async function boot() {
    await Promise.all([loadScripts(), loadPlugins(), loadPlaytests()]);
  }

  useEffect(() => { boot().catch((error) => setStatus(`初始化失败：${error.message}`)); }, []);

  async function validateScript(scriptId: string) {
    setStatus(`正在检查 ${scriptId} ...`);
    const payload = await api<{ validation: { summary: Record<string, number> } }>(`/admin/api/scripts/${encodeURIComponent(scriptId)}/validate`, { method: 'POST' });
    setStatus(`检查完成：${JSON.stringify(payload.validation.summary)}`);
    await loadScripts();
  }

  async function inspectScript(scriptId: string) {
    setStatus(`正在查看 ${scriptId} ...`);
    const [inspectionPayload, flowPayload] = await Promise.all([
      api<{ script: ScriptRecord; inspection: ScriptInspection }>(`/admin/api/scripts/${encodeURIComponent(scriptId)}/inspect`),
      api<{ flow: FlowInspection }>(`/admin/api/scripts/${encodeURIComponent(scriptId)}/flow`),
    ]);
    setSelected({ script: inspectionPayload.script, inspection: inspectionPayload.inspection, flow: flowPayload.flow });
    setPanel('inspectPanel');
    setTab('overviewTab');
    setStatus(`剧本检查已加载：${scriptId}`);
  }

  async function promoteScript(scriptId: string) {
    setStatus(`正在发布 ${scriptId} ...`);
    try {
      const payload = await api<{ script: ScriptRecord }>(`/admin/api/scripts/${encodeURIComponent(scriptId)}/promote`, jsonRequest({ force: true }));
      setStatus(`已发布：${payload.script.script_id}`);
    } catch (error) {
      setStatus(`发布失败：${(error as Error).message}`);
    }
    await loadScripts();
  }

  async function createPlaytest(scriptId: string) {
    const payload = await api<{ playtest: Playtest }>(`/admin/api/scripts/${encodeURIComponent(scriptId)}/playtests`, jsonRequest({ mode: 'dry_run', human_player_count: 0, step_mode: true }));
    setStatus(`已创建试玩：${payload.playtest.playtest_id}`);
    setPanel('playtestsPanel');
    await loadPlaytests();
  }

  async function assignPlaytest(playtestId: string) {
    await api(`/admin/api/playtests/${encodeURIComponent(playtestId)}/assign`, { method: 'POST' });
    await loadPlaytests();
  }

  async function startPlaytest(playtestId: string) {
    await api(`/admin/api/playtests/${encodeURIComponent(playtestId)}/start`, { method: 'POST' });
    await loadPlaytests();
  }

  async function stepPlaytest(playtestId: string) {
    await api(`/admin/api/playtests/${encodeURIComponent(playtestId)}/step`, jsonRequest({ count: 1 }));
    await loadPlaytests();
  }

  async function showRuntime(playtestId: string) {
    const payload = await api(`/admin/api/playtests/${encodeURIComponent(playtestId)}/runtime`);
    setStatus(JSON.stringify(payload, null, 2));
  }

  async function runPlugin(pluginId: string) {
    const payload = await api(`/admin/api/plugins/${encodeURIComponent(pluginId)}/run`, jsonRequest({ input: { selected_script_id: selected?.script.script_id } }));
    setStatus(JSON.stringify(payload, null, 2));
  }

  async function uploadScript(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setStatus('正在上传...');
    const payload = await api<{ script: ScriptRecord; validation: { summary: Record<string, number> } }>('/admin/api/scripts/upload', { method: 'POST', body: form });
    setStatus(`已上传 ${payload.script.script_id}。检查结果：${JSON.stringify(payload.validation.summary)}`);
    await loadScripts();
    await inspectScript(payload.script.script_id);
  }

  async function generateScript(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const prompt = String(form.get('prompt') || '').trim();
    if (!prompt) { setStatus('请先填写需求描述。'); return; }
    const payload = await api<{ script: ScriptRecord; notes: string[] }>('/admin/api/scripts/generate', jsonRequest({ prompt }));
    setStatus(`已生成草稿 ${payload.script.script_id}。\n${(payload.notes || []).join('\n')}`);
    await loadScripts();
    await inspectScript(payload.script.script_id);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">Drama Engine<br /><span>管理控制台</span></div>
        <NavButton active={panel === 'scriptsPanel'} onClick={() => setPanel('scriptsPanel')}>剧本管理</NavButton>
        <NavButton active={panel === 'uploadPanel'} onClick={() => setPanel('uploadPanel')}>上传剧本</NavButton>
        <NavButton active={panel === 'generatePanel'} onClick={() => setPanel('generatePanel')}>自然语言创建</NavButton>
        <NavButton active={panel === 'pluginsPanel'} onClick={() => setPanel('pluginsPanel')}>剧本插件</NavButton>
        <NavButton active={panel === 'playtestsPanel'} onClick={() => setPanel('playtestsPanel')}>试玩测试</NavButton>
      </aside>
      <main className="main">
        <header className="topbar"><div><h1>Drama Engine 管理控制台</h1><p>管理剧本、检查 DSL、检查剧本、流程可视化、试玩测试、插件与自然语言草稿入口。</p></div><button className="primary" onClick={() => boot().then(() => setStatus('已刷新。'))}>刷新</button></header>
        <section className="status-box"><pre>{status}</pre></section>
        {panel === 'scriptsPanel' && <ScriptsPanel scripts={scripts} onValidate={validateScript} onInspect={inspectScript} onPlaytest={createPlaytest} onPromote={promoteScript} />}
        {panel === 'uploadPanel' && <UploadPanel onSubmit={uploadScript} />}
        {panel === 'inspectPanel' && <InspectPanel selected={selected} tab={tab} onTab={setTab} />}
        {panel === 'generatePanel' && <GeneratePanel onSubmit={generateScript} />}
        {panel === 'pluginsPanel' && <PluginsPanel plugins={plugins} onRun={runPlugin} />}
        {panel === 'playtestsPanel' && <PlaytestsPanel playtests={playtests} onAssign={assignPlaytest} onStart={startPlaytest} onStep={stepPlaytest} onRuntime={showRuntime} />}
      </main>
    </div>
  );
}

function NavButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button className={`nav-btn ${active ? 'active' : ''}`} onClick={onClick}>{children}</button>;
}

function ScriptsPanel({ scripts, onValidate, onInspect, onPlaytest, onPromote }: {
  scripts: ScriptRecord[]; onValidate: (id: string) => void; onInspect: (id: string) => void; onPlaytest: (id: string) => void; onPromote: (id: string) => void;
}) {
  return <section className="panel active"><div className="panel-head"><h2>剧本管理</h2><p>内置剧本为只读的“已发布”状态；上传剧本会先进入“草稿”状态，必须经过“检查 / 查看 / 试玩”后才能发布。</p></div><div className="table-wrap"><table><thead><tr><th>名称</th><th>状态</th><th>来源</th><th>错误</th><th>警告</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{scripts.length ? scripts.map((script) => { const summary = script.last_validation_summary || {}; const errors = (summary.fatal || 0) + (summary.error || 0); const warnings = summary.warning || 0; return <tr key={script.script_id}><td><strong>{script.name}</strong><br /><span className="badge">{script.script_id}</span></td><td><span className={`badge ${script.status}`}>{formatStatus(script.status)}</span></td><td>{formatSource(script.source)}</td><td>{errors}</td><td>{warnings}</td><td>{script.updated_at || '-'}</td><td><button className="small-btn" onClick={() => onValidate(script.script_id)}>检查</button><button className="small-btn" onClick={() => onInspect(script.script_id)}>查看</button><button className="small-btn" onClick={() => onPlaytest(script.script_id)}>试玩</button><button className="small-btn" onClick={() => onPromote(script.script_id)}>发布</button></td></tr>; }) : <tr><td colSpan={7}>暂无剧本。</td></tr>}</tbody></table></div></section>;
}

function UploadPanel({ onSubmit }: { onSubmit: (event: React.FormEvent<HTMLFormElement>) => void }) {
  return <section className="panel active"><div className="panel-head"><h2>上传剧本</h2><p>上传 YAML 后会自动保存为草稿，并执行 DSL 检查。</p></div><form className="card form-grid" onSubmit={onSubmit}><label>剧本名称<input name="name" placeholder="例如：自定义狼人杀" /></label><label>剧本描述<input name="description" placeholder="描述这个剧本" /></label><label>YAML 文件<input name="file" type="file" accept=".yaml,.yml" required /></label><button className="primary" type="submit">上传并检查</button></form></section>;
}

function InspectPanel({ selected, tab, onTab }: { selected: InspectionState | null; tab: Tab; onTab: (tab: Tab) => void }) {
  if (!selected) return <section className="panel active"><div className="card">请先在剧本管理中选择一个剧本查看。</div></section>;
  const data = selected.inspection;
  const flow = selected.flow;
  const tabs: Array<[Tab, string]> = [['overviewTab', '概览'], ['rolesTab', '角色'], ['scopesTab', '可见域'], ['scenesTab', '场景'], ['issuesTab', '问题'], ['sequenceTab', '顺序流程'], ['stateTab', '状态机'], ['treeTab', 'DSL 树'], ['rawTab', '原始 YAML']];
  return <section className="panel active"><div className="panel-head"><h2>检查剧本：{selected.script.name}</h2><p>结构化查看剧本、校验问题、顺序流程、状态机和 DSL 树。</p></div><div className="tabs">{tabs.map(([id, label]) => <button key={id} className={`tab ${tab === id ? 'active' : ''}`} onClick={() => onTab(id)}>{label}</button>)}</div><div className="tab-panel active">{tab === 'overviewTab' && <Overview overview={data.overview} players={data.players} summary={data.issues.summary} />}{tab === 'rolesTab' && <DataTable rows={data.roles || []} />}{tab === 'scopesTab' && <DataTable rows={(data.scopes || []).map((s) => ({ ...s, members: JSON.stringify(s.members) }))} />}{tab === 'scenesTab' && <DataTable rows={data.scenes || []} />}{tab === 'issuesTab' && <Issues issues={data.issues.issues || []} />}{tab === 'sequenceTab' && <FlowGraph mode="sequence" sequence={flow.sequence} />}{tab === 'stateTab' && <FlowGraph mode="state" stateMachine={flow.state_machine} />}{tab === 'treeTab' && <DslTree node={flow.tree} />}{tab === 'rawTab' && <pre>{data.raw_yaml || ''}</pre>}</div></section>;
}

function Overview({ overview, players, summary }: { overview: Record<string, unknown>; players: Record<string, unknown>; summary: Record<string, number> }) {
  return <div className="card">{kv('标题', overview.title)}{kv('描述', overview.description)}{kv('玩家', `${players.count || '-'} / ${JSON.stringify(players.distribution || {})}`)}{kv('角色数', overview.role_count)}{kv('可见域数', overview.scope_count)}{kv('场景数', overview.scene_count)}{kv('状态数', overview.state_count)}{kv('是否循环', overview.loop ? '是' : '否')}{kv('检查结果', `致命=${summary.fatal}, 错误=${summary.error}, 警告=${summary.warning}, 提示=${summary.info}`)}</div>;
}

function kv(k: string, v: unknown) { return <div className="kv"><span>{k}</span><span>{displayValue(v)}</span></div>; }

function DataTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <div className="card">暂无数据。</div>;
  const keys = Object.keys(rows[0]).filter((key) => key !== 'raw' && key !== 'brief' && key !== 'cue');
  return <table><thead><tr>{keys.map((key) => <th key={key}>{formatKey(key)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{keys.map((key) => <td key={key}>{displayValue(row[key])}</td>)}</tr>)}</tbody></table>;
}

function Issues({ issues }: { issues: Issue[] }) {
  if (!issues.length) return <div className="card">暂无问题。</div>;
  return <>{issues.map((issue) => <div key={`${issue.code}-${issue.path}-${issue.message}`} className={`issue ${issue.level}`}><strong>{formatLevel(issue.level)} · {issue.code}</strong><br />{issue.message}<br /><span className="badge">{issue.path || '-'}</span><p>{issue.suggestion || ''}</p></div>)}</>;
}

function GeneratePanel({ onSubmit }: { onSubmit: (event: React.FormEvent<HTMLFormElement>) => void }) {
  return <section className="panel active"><div className="panel-head"><h2>自然语言创建</h2><p>自然语言入口当前生成可编辑的草稿模板。未来可在这里接入 Skill；生成结果不能绕过检查、查看和试玩。</p></div><form className="card" onSubmit={onSubmit}><label>需求描述<textarea name="prompt" rows={7} placeholder="描述你想创建的聚会游戏规则、角色、流程、胜负条件..." /></label><button className="primary" type="submit">生成草稿</button></form></section>;
}

function PluginsPanel({ plugins, onRun }: { plugins: PluginInfo[]; onRun: (id: string) => void }) {
  return <section className="panel active"><div className="panel-head"><h2>剧本插件</h2><p>剧本插件只能生成草稿或检查建议，不能直接发布正式剧本。</p></div><div className="grid">{plugins.length ? plugins.map((plugin) => <div key={plugin.plugin_id} className="card"><h3>{plugin.name}</h3><p>{plugin.description}</p>{kv('编号', plugin.plugin_id)}{kv('类型', plugin.plugin_type)}<button className="small-btn" onClick={() => onRun(plugin.plugin_id)}>运行</button></div>) : <div className="card">暂无插件。</div>}</div></section>;
}

function PlaytestsPanel({ playtests, onAssign, onStart, onStep, onRuntime }: { playtests: Playtest[]; onAssign: (id: string) => void; onStart: (id: string) => void; onStep: (id: string) => void; onRuntime: (id: string) => void }) {
  return <section className="panel active"><div className="panel-head"><h2>试玩测试</h2><p>试玩测试用于验证剧本执行链路、状态变化和单步推进。</p></div><div className="grid">{playtests.length ? playtests.map((pt) => <div key={pt.playtest_id} className="card"><h3>{pt.playtest_id}</h3>{kv('剧本', pt.script_id)}{kv('状态', formatStatus(pt.status))}{kv('步数', pt.current_step)}<button className="small-btn" onClick={() => onAssign(pt.playtest_id)}>发牌</button><button className="small-btn" onClick={() => onStart(pt.playtest_id)}>开始</button><button className="small-btn" onClick={() => onStep(pt.playtest_id)}>下一步</button><button className="small-btn" onClick={() => onRuntime(pt.playtest_id)}>运行状态</button><pre>{(pt.events || []).slice(-8).map((e) => `[${e.step}] ${e.type || e.kind}: ${e.message}`).join('\n')}</pre></div>) : <div className="card">暂无试玩测试。</div>}</div></section>;
}

createRoot(document.getElementById('root')!).render(<App />);

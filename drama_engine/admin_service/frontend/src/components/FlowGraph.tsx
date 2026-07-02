import { useMemo, useState } from 'react';
import type { FlowEdge, FlowNode, SequenceFlow, StateInfo, StateMachineFlow, TreeNode } from '../types';

type Detail = { title: string; data: unknown };

type FlowGraphProps =
  | { mode: 'sequence'; sequence: SequenceFlow }
  | { mode: 'state'; stateMachine: StateMachineFlow };

function edgeMap(edges: FlowEdge[]): Map<string, FlowEdge[]> {
  const map = new Map<string, FlowEdge[]>();
  edges.forEach((edge) => {
    const key = String(edge.from || '');
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(edge);
  });
  return map;
}

function incomingMap(edges: FlowEdge[]): Map<string, FlowEdge[]> {
  const map = new Map<string, FlowEdge[]>();
  edges.forEach((edge) => {
    const key = String(edge.to || '');
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(edge);
  });
  return map;
}

export function FlowGraph(props: FlowGraphProps) {
  if (props.mode === 'sequence') return <SequenceGraph sequence={props.sequence} />;
  return <StateGraph stateMachine={props.stateMachine} />;
}

function SequenceGraph({ sequence }: { sequence: SequenceFlow }) {
  const nodes = sequence.nodes || [];
  const edges = sequence.edges || [];
  const outgoing = useMemo(() => edgeMap(edges), [edges]);
  const [selected, setSelected] = useState<Detail>({ title: '详情', data: '点击流程节点查看详情。' });
  const [selectedId, setSelectedId] = useState('');

  if (!nodes.length) return <div className="card">暂无顺序流程。</div>;

  return (
    <div className="flow-component flow-component-sequence">
      <div className="flow-toolbar">
        <div><strong>顺序流程图</strong><span>{nodes.length} 个场景 · {edges.length} 条连接</span></div>
        <button className="small-btn" onClick={() => { setSelectedId(''); setSelected({ title: '详情', data: '点击流程节点查看详情。' }); }}>重置选择</button>
      </div>
      <div className="flow-canvas sequence-canvas">
        {nodes.map((node, index) => (
          <SequenceNode
            key={node.id || index}
            node={node}
            index={index}
            edges={outgoing.get(String(node.id)) || []}
            selected={selectedId === node.id}
            onSelect={() => { setSelectedId(node.id); setSelected({ title: `流程节点：${node.label || node.id}`, data: node }); }}
          />
        ))}
      </div>
      <DetailPanel detail={selected} />
    </div>
  );
}

function SequenceNode({ node, index, edges, selected, onSelect }: {
  node: FlowNode; index: number; edges: FlowEdge[]; selected: boolean; onSelect: () => void;
}) {
  return (
    <button type="button" className={`flow-step ${selected ? 'selected' : ''}`} onClick={onSelect}>
      <span className="flow-step-index">{index + 1}</span>
      <span className="flow-step-body">
        <strong className="flow-step-title">{node.label || node.id}</strong>
        <span className="flow-step-meta">
          <span>{node.type || 'scene'}</span>
          <span>{node.scope || '无可见域'}</span>
          <span>{node.condition ? '有条件' : '无条件'}</span>
        </span>
        <span className="flow-step-next">
          {edges.length ? `下一步：${edges.map((edge) => edge.to).join('、')}` : '终点'}
        </span>
      </span>
    </button>
  );
}

function StateGraph({ stateMachine }: { stateMachine: StateMachineFlow }) {
  const states = stateMachine.states || [];
  const edges = stateMachine.edges || [];
  const [selected, setSelected] = useState<Detail>({ title: '详情', data: '点击状态或关系查看详情。' });
  const [selectedKey, setSelectedKey] = useState('');
  const stateByName = useMemo(() => new Map(states.map((item) => [item.name, item])), [states]);
  const names = useMemo(() => {
    const result = new Set<string>(states.map((item) => item.name));
    edges.forEach((edge) => { if (edge.from) result.add(edge.from); if (edge.to) result.add(edge.to); });
    return Array.from(result).sort();
  }, [states, edges]);
  const incoming = useMemo(() => incomingMap(edges), [edges]);

  if (!names.length && !edges.length) return <div className="card">暂无状态机数据。</div>;

  return (
    <div className="flow-component flow-component-state">
      <div className="flow-toolbar">
        <div><strong>状态读写图</strong><span>{names.length} 个状态 · {edges.length} 条读写关系</span></div>
        <button className="small-btn" onClick={() => { setSelectedKey(''); setSelected({ title: '详情', data: '点击状态或关系查看详情。' }); }}>重置选择</button>
      </div>
      <div className="state-graph-layout">
        <div className="state-column">
          <h3>状态</h3>
          {names.map((name) => (
            <StateNode
              key={name}
              name={name}
              state={stateByName.get(name)}
              incoming={incoming.get(name) || []}
              selected={selectedKey === `state:${name}`}
              onSelect={() => { setSelectedKey(`state:${name}`); setSelected({ title: `状态：${name}`, data: stateByName.get(name) || { name } }); }}
            />
          ))}
        </div>
        <div className="state-column">
          <h3>读写关系</h3>
          {edges.slice(0, 160).map((edge, index) => (
            <StateEdge
              key={`${edge.from}-${edge.to}-${edge.kind}-${index}`}
              edge={edge}
              selected={selectedKey === `edge:${index}`}
              onSelect={() => { setSelectedKey(`edge:${index}`); setSelected({ title: `关系：${edge.kind || ''}`, data: edge }); }}
            />
          ))}
          {!edges.length && <div className="muted-card">暂无关系。</div>}
        </div>
      </div>
      <DetailPanel detail={selected} />
      {(stateMachine.issues || []).length > 0 && (
        <div className="flow-issues">{stateMachine.issues!.slice(0, 12).map((issue) => <span key={`${issue.code}-${issue.message}`} className={`issue-chip ${issue.level}`}>{issue.level} · {issue.code}</span>)}</div>
      )}
    </div>
  );
}

function StateNode({ name, state, incoming, selected, onSelect }: {
  name: string; state?: StateInfo; incoming: FlowEdge[]; selected: boolean; onSelect: () => void;
}) {
  const readCount = (state?.read_by || []).length + incoming.filter((edge) => edge.kind === 'read').length;
  const writeCount = (state?.written_by || []).length + incoming.filter((edge) => edge.kind === 'write').length;
  return <button type="button" className={`state-node ${selected ? 'selected' : ''}`} onClick={onSelect}><strong>{name}</strong><span>读 {readCount} · 写 {writeCount}</span></button>;
}

function StateEdge({ edge, selected, onSelect }: { edge: FlowEdge; selected: boolean; onSelect: () => void }) {
  const label = edge.kind === 'write' ? '写入' : edge.kind === 'read' ? '读取' : edge.kind || '关系';
  return <button type="button" className={`state-edge ${edge.kind || ''} ${selected ? 'selected' : ''}`} onClick={onSelect}><span className="edge-kind">{label}</span><span>{edge.from || '-'}</span><i>→</i><span>{edge.to || '-'}</span></button>;
}

function DetailPanel({ detail }: { detail: Detail }) {
  return <div className="flow-detail"><strong>{detail.title}</strong><pre>{typeof detail.data === 'string' ? detail.data : JSON.stringify(detail.data, null, 2)}</pre></div>;
}

export function DslTree({ node }: { node?: TreeNode }) {
  if (!node) return <div className="card">暂无 DSL 树。</div>;
  return <div className="tree react-tree"><TreeItem node={node} /></div>;
}

function TreeItem({ node }: { node: TreeNode }) {
  return <ul><li><strong>{node.label}</strong> <span className="node-type">{node.type || ''}</span>{(node.children || []).map((child) => <TreeItem key={child.id || child.label} node={child} />)}</li></ul>;
}

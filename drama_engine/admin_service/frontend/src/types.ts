export type ScriptRecord = {
  script_id: string;
  name: string;
  description?: string;
  status: string;
  source: string;
  path?: string;
  updated_at?: string;
  last_validation_summary?: Record<string, number>;
};

export type Issue = {
  level: string;
  code: string;
  message: string;
  path?: string;
  suggestion?: string;
};

export type FlowNode = {
  id: string;
  label: string;
  type: string;
  scope?: string;
  index?: number;
  condition?: unknown;
};

export type FlowEdge = {
  from: string;
  to: string;
  condition?: string;
  kind?: string;
};

export type SequenceFlow = {
  nodes: FlowNode[];
  edges: FlowEdge[];
  mermaid?: string;
};

export type StateInfo = {
  name: string;
  initial_value?: unknown;
  written_by?: string[];
  read_by?: string[];
};

export type StateMachineFlow = {
  states: StateInfo[];
  edges: FlowEdge[];
  issues?: Issue[];
  mermaid?: string;
};

export type TreeNode = {
  id?: string;
  label: string;
  type?: string;
  children?: TreeNode[];
};

export type FlowInspection = {
  sequence: SequenceFlow;
  state_machine: StateMachineFlow;
  tree: TreeNode;
};

export type ScriptInspection = {
  overview: Record<string, unknown>;
  players: Record<string, unknown>;
  roles: Record<string, unknown>[];
  scopes: Record<string, unknown>[];
  scenes: Record<string, unknown>[];
  issues: { summary: Record<string, number>; issues: Issue[] };
  raw_yaml: string;
};

export type Playtest = {
  playtest_id: string;
  script_id: string;
  status: string;
  current_step: number;
  events?: Array<{ step: number; type?: string; kind?: string; message?: string }>;
};

export type PluginInfo = {
  plugin_id: string;
  name: string;
  description?: string;
  plugin_type?: string;
};

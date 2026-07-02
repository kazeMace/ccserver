export function formatStatus(value?: string): string {
  const map: Record<string, string> = {
    draft: '草稿', valid: '已检查', approved: '已发布', archived: '已归档',
    created: '已创建', assigned: '已发牌', running: '运行中', ended: '已结束',
    failed: '失败', terminated: '已终止',
  };
  return map[value || ''] || value || '';
}

export function formatSource(value?: string): string {
  const map: Record<string, string> = { builtin: '内置', uploaded: '上传' };
  return map[value || ''] || value || '';
}

export function formatLevel(value?: string): string {
  const map: Record<string, string> = { fatal: '致命', error: '错误', warning: '警告', info: '提示' };
  return map[value || ''] || value || '';
}

export function formatKey(value: string): string {
  const map: Record<string, string> = {
    name: '名称', display_name: '显示名称', faction: '阵营', scopes: '可见域',
    abilities: '能力', inventory: '道具', index: '序号', type: '类型', scope: '可见域',
    turn_policy: '发言策略', has_when: '有条件', performers: '执行者', candidates: '候选项',
    effect_count: '效果数', path: '路径', initial_value: '初始值', written_by: '写入位置',
    read_by: '读取位置', source: '来源', code: '编码', message: '消息',
  };
  return map[value] || value;
}

export function displayValue(value: unknown): string {
  if (Array.isArray(value)) return value.join('、');
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

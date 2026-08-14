/**
 * 前端侧的变量引用解析：把 {{node.field.sub}} 模板解析成 variables 里的实际值。
 *
 * task.variables 是一个扁平 dict（key 为 node_id 或保留字 input/system），
 * 每个节点的输出是嵌套 dict。本函数按 `.` 分段逐层取值。
 *
 * 后端 ExpressionEngine 的 resolve 也做类似的事，但审批视图采用"前端解析"策略
 * （checkpoint 只存配置模板，不存解析后的大内容），所以需要这套独立实现。
 */

/**
 * 判断字符串是否包含变量引用 {{...}}。
 */
export function hasVarRef(s: unknown): s is string {
  return typeof s === 'string' && s.includes('{{');
}

/**
 * 解析单个变量引用模板。
 *
 * - 纯引用（整个字符串就是一个 {{...}}）：按路径取值，保留原始类型（str/number/list/dict/bool）。
 * - 混合文本（如 "金额: {{input.amount}} 元"）：用字符串替换，结果一定是 string。
 * - 非模板字符串：原样返回。
 *
 * 路径不存在时返回 undefined（前端渲染层负责优雅降级）。
 */
export function resolveVar(template: unknown, variables: Record<string, unknown>): unknown {
  if (typeof template !== 'string') return template;
  if (!template.includes('{{')) return template;

  // 纯引用：{{ node.field.sub }} → 取值保留类型
  const pure = template.match(/^\s*\{\{([^}]+)\}\}\s*$/);
  if (pure) {
    return resolvePath(pure[1].trim(), variables);
  }

  // 混合文本：逐个替换 {{...}}，结果为 string
  return template.replace(/\{\{([^}]+)\}\}/g, (_full, expr: string) => {
    const val = resolvePath(expr.trim(), variables);
    return val === undefined || val === null ? '' : formatScalar(val);
  });
}

/**
 * 按点分路径从 variables 取值。
 * resolvePath('agent_risk.report.content', vars) → vars['agent_risk']['report']['content']
 */
function resolvePath(path: string, variables: Record<string, unknown>): unknown {
  if (!path) return undefined;
  const segments = path.split('.');
  let cur: unknown = variables;
  for (const seg of segments) {
    if (cur == null) return undefined;
    if (typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[seg];
  }
  return cur;
}

/**
 * 把标量值格式化为字符串（用于混合文本替换）。
 * dict/list 转成 JSON，避免出现 [object Object]。
 */
function formatScalar(val: unknown): string {
  if (typeof val === 'string') return val;
  if (typeof val === 'number' || typeof val === 'boolean') return String(val);
  if (val === null || val === undefined) return '';
  try {
    return JSON.stringify(val);
  } catch {
    return String(val);
  }
}

/**
 * 把解析后的值格式化为人类可读的展示文本（用于 fields 区块的 value 展示）。
 * 与 formatScalar 的区别：对 boolean 显示"是/否"，对 list/dict 缩进 JSON。
 */
export function formatDisplay(val: unknown): string {
  if (val === undefined || val === null) return '';
  if (typeof val === 'boolean') return val ? '是' : '否';
  if (typeof val === 'string') return val;
  if (typeof val === 'number') return String(val);
  try {
    return JSON.stringify(val, null, 2);
  } catch {
    return String(val);
  }
}

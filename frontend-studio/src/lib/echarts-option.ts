/**
 * echarts option 分层校验。
 *
 * echarts 对坏 option 多数「静默白图」而非抛错，必须在喂给 echarts 之前校验。
 * 分层防御（与 EChartBlock 配合）：
 *   - parseOption: JSON.parse + 纯数据校验（拒函数 / undefined / 字符串 formatter）
 *   - validateOption: 最小契约（series 必须数组，每 series 必须有 type 枚举 + data 数组）
 *   - 体积上限（>1MB 拒）
 *
 * 不引入 zod —— 契约足够小，手写更直接。
 * 绝不 eval / new Function：纯 JSON.parse 天然拒函数注入。
 */

export type ChartOption = Record<string, unknown>;

export type ValidationSuccess = { ok: true; option: ChartOption };
export type ValidationFailure = {
  ok: false;
  reason: ValidationReason;
  message: string;
};

export type ValidationResult = ValidationSuccess | ValidationFailure;

export function isValidationSuccess(
  r: ValidationResult,
): r is ValidationSuccess {
  return r.ok === true;
}

export type ValidationReason =
  | 'streaming' // 流式未完整（括号未配平）
  | 'syntax' // JSON 语法错 / 含函数 / 非法 JSON
  | 'not-object' // 解析成功但非对象
  | 'too-large' // 体积超上限
  | 'no-series' // 缺 series 或非数组
  | 'bad-series' // 某 series 缺 type/data 或类型非法
  | 'invalid-type'; // series.type 非法枚举

const MAX_OPTION_BYTES = 1024 * 1024; // 1MB 上限
const VALID_SERIES_TYPES = new Set([
  'bar',
  'line',
  'pie',
  'scatter',
  'radar',
  'heatmap',
  'funnel',
  'gauge',
  'tree',
  'treemap',
  'sunburst',
  'sankey',
  'graph',
  'boxplot',
  'candlestick',
  'effectScatter',
  'lines',
  'themeRiver',
  'pictorialBar',
]);

/** 流式完整性启发式：括号 / 大括号 / 方括号配平 + 引号状态。 */
export function looksComplete(raw: string): boolean {
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (ch === '\\' && inString) {
      escape = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (ch === '{' || ch === '[') depth++;
    else if (ch === '}' || ch === ']') depth--;
    if (depth < 0) return false; // 提前闭合
  }
  return depth === 0 && !inString;
}

/**
 * 递归检测 option 内是否含非 JSON 可序列化构造（函数 / undefined / Symbol）。
 * JSON.parse 本身已拒函数字符串，但保险起见再扫一遍（防外部传入已对象）。
 */
function containsUnsafeValue(value: unknown): boolean {
  if (typeof value === 'function') return true;
  if (typeof value === 'symbol') return true;
  if (typeof value === 'string') {
    // formatter / 回调字符串注入：拒含 "function" / "=>" 函数构造
    const v = value.trim();
    if (v.startsWith('function') || v.includes('=>')) return true;
  }
  if (typeof value === 'undefined') return true;
  if (value === null) return false;
  if (Array.isArray(value)) {
    return value.some((v) => containsUnsafeValue(v));
  }
  if (typeof value === 'object') {
    return Object.values(value).some((v) => containsUnsafeValue(v));
  }
  return false;
}

/** 解析 + 安全校验（不动契约）。 */
export function parseOption(raw: string): ValidationResult {
  const trimmed = raw.trim();
  if (trimmed.length === 0) return { ok: false, reason: 'streaming', message: '图表渲染中…' };
  if (!looksComplete(trimmed)) {
    return { ok: false, reason: 'streaming', message: '图表渲染中…' };
  }
  if (new Blob([trimmed]).size > MAX_OPTION_BYTES) {
    return { ok: false, reason: 'too-large', message: '图表数据过大（>1MB），已拒绝渲染' };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return {
      ok: false,
      reason: 'syntax',
      message: '图表数据格式错误（JSON 语法错误或含函数配置）',
    };
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, reason: 'not-object', message: '图表数据必须是一个对象' };
  }
  if (containsUnsafeValue(parsed)) {
    return { ok: false, reason: 'syntax', message: '图表数据含函数 / 不安全配置，已拒绝' };
  }
  return { ok: true, option: parsed as ChartOption };
}

/**
 * 最小契约校验：series 必须数组，每 series 必须有 type（合法枚举）+ data（数组）。
 * 其余字段放行（不限 agent 自由表达）。失败按原因分类提示。
 */
export function validateOption(option: ChartOption): ValidationResult {
  const series = option.series;
  if (!Array.isArray(series) || series.length === 0) {
    return { ok: false, reason: 'no-series', message: '图表数据不完整：缺少 series（系列）' };
  }
  for (let i = 0; i < series.length; i++) {
    const s = series[i];
    if (s === null || typeof s !== 'object' || Array.isArray(s)) {
      return { ok: false, reason: 'bad-series', message: `series[${i}] 不是对象` };
    }
    const obj = s as Record<string, unknown>;
    const type = obj.type;
    if (typeof type !== 'string' || !VALID_SERIES_TYPES.has(type)) {
      return {
        ok: false,
        reason: 'invalid-type',
        message: `series[${i}].type 非法或缺失（当前：${String(type)}）`,
      };
    }
    // pie 的 data 是对象数组，bar/line/scatter 是数值/坐标数组 —— 只校验是数组。
    if (!Array.isArray(obj.data)) {
      return {
        ok: false,
        reason: 'bad-series',
        message: `series[${i}] 缺少 data（数组）`,
      };
    }
  }
  return { ok: true, option };
}

/** 端到端：解析 + 安全校验 + 契约校验。 */
export function validateChartOptionString(raw: string): ValidationResult {
  const parsed = parseOption(raw);
  if (!parsed.ok) return parsed;
  return validateOption(parsed.option);
}

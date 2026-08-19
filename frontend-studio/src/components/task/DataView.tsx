/**
 * DataView — 智能通用数据渲染器。
 *
 * 把任意 JSON 可序列化值按类型递归渲染成「用户级」UI（而非裸 JSON），
 * 并对工作流场景的高频已知字段做语义增强：
 *   - agent 节点的 response  → 「回答」段落卡
 *   - tool 节点的 result     → 「执行结果」子区
 *   - human 节点的 decision  → 审批结论徽标（approve=绿 / reject=红 / skip=黄）
 *   - gateway 的 selected_branch / condition → 命中分支 / 条件表达式
 *   - agent_id / tool_name   → 带图标标签
 *   - options                → 选项标签组
 *   - timeout_ms             → 时长格式化（5 分钟）
 *   - error                  → 红色文本
 *   - 时间戳类 key           → MM-DD HH:mm:ss
 *
 * 设计要点：
 * - 入口按类型分派：null / 标量 / 数组 / 对象，递归 depth 封顶 MAX_DEPTH。
 * - matchKnownField：精确 key（不限 context）命中 → 语义渲染；否则走通用分派。
 * - 长文本折叠、超大对象折叠、祖先链循环引用检测；绝不以裸 JSON 作主展示。
 * - 「查看原始数据」折叠兜底（showRaw，默认开）仅供排障，4 个业务调用点都关闭。
 *
 * 主题：只写 dark 色 token（bg-[#09090b]/#18181b、text-[#fafafa]/#a1a1aa/#71717a、
 * border-[#27272a]），light 模式由 index.css 的 .theme-light 统一覆盖；彩色状态色
 * （#10B981/#EF4444/#8B5CF6/#F59E0B/#3B82F6/#06B6D4）两主题通用。
 */
import { useState, useContext, createContext, type ReactNode } from 'react'
import {
  ChevronRight, AlertTriangle, ExternalLink, Workflow as WorkflowIcon,
  Wrench, GitBranch, UserCheck, Clock,
} from 'lucide-react'
import { Tag } from '../ui'
import { Markdown } from '../Markdown'
import { NODE_TYPE_LABEL } from './task-flow-utils'

export type DataViewContext =
  | 'node_output' | 'event_data' | 'task_input' | 'approval_upstream' | 'generic'

export interface DataViewProps {
  value: unknown
  /** 数据来源语义角色，预留给后续更细的字段增强（当前主要靠 key 匹配） */
  context?: DataViewContext
  /** context='node_output' 时传 agent/tool/human/gateway/parallel/start/end */
  nodeType?: string
  /** 是否附「查看原始数据」折叠兜底，默认 true（业务调用处一般传 false） */
  showRaw?: boolean
  className?: string
}

/* ─── 渲染常量 ─── */

const MAX_DEPTH = 4
const LONG_TEXT_THRESHOLD = 200
const MAX_GRID_ENTRIES = 20
const MAX_TAGS = 12

/** 字段 key → 中文标签（键值对网格里展示 key 用） */
const FIELD_LABEL: Record<string, string> = {
  node_id: '节点 ID', node_type: '节点类型', node_label: '节点名称',
  output_summary: '输出摘要', output: '输出',
  response: '回答', result: '执行结果',
  usage: 'Token 用量', total_tokens: 'Token 消耗', input_tokens: '输入 Tokens', output_tokens: '输出 Tokens',
  llm_calls: 'LLM 调用', tool_calls: '工具调用',
  agent_id: 'Agent', tool_name: '工具名称', tool_id: '工具 ID',
  tool_description: '工具描述', instructions: '使用说明', params: '参数', note: '备注',
  decision: '审批结论', reason: '审批原因', approved_by: '审批人',
  selected_branch: '命中分支', condition: '条件表达式',
  options: '可选项', timeout_ms: '超时时长', timeout_action: '超时处理', timeout_deadline: '超时截止',
  error: '错误', error_message: '错误信息', error_code: '错误码',
  branches: '分支', join_strategy: '汇聚策略', join_count: '汇聚数', scope: '作用域', start_nodes: '起始节点',
  status: '状态', timestamp: '时间',
  created_at: '创建时间', updated_at: '更新时间', paused_at: '暂停时间', paused_at_node: '暂停节点',
  title: '标题', description: '描述',
  child_task_id: '子任务', workflow_id: '工作流', created_by: '创建者',
  resumed_from_checkpoint: '从检查点恢复',
}

/* ─── 数据增强上下文：把 agent_id 等 ID 解析为可读名称（由父级注入） ─── */

interface EnhanceContextValue {
  /** agent_id → Agent 名称 */
  agentNameMap?: Record<string, string>
}
const EnhanceContext = createContext<EnhanceContextValue>({})

/** 由父级（TaskDetailDrawer）注入 agentNameMap，让 DataView 把 agent_id 渲染成 Agent 名称 */
export function DataViewEnhanceProvider({
  agentNameMap,
  children,
}: {
  agentNameMap?: Record<string, string>
  children: ReactNode
}) {
  return <EnhanceContext.Provider value={{ agentNameMap }}>{children}</EnhanceContext.Provider>
}

/* ─── 主组件 ─── */

export function DataView({
  value,
  context = 'generic',
  nodeType,
  showRaw = true,
  className = '',
}: DataViewProps) {
  return (
    <div className={className}>
      <RenderNode
        value={value}
        fieldKey={undefined}
        context={context}
        nodeType={nodeType}
        depth={0}
        ancestors={[]}
      />
      {showRaw && <RawJsonFallback value={value} />}
    </div>
  )
}

/* ─── 递归渲染节点 ─── */

interface RenderProps {
  value: unknown
  fieldKey?: string
  context: DataViewContext
  nodeType?: string
  depth: number
  /** 当前递归祖先链（仅祖先，不含兄弟分支），用于循环引用检测 */
  ancestors: object[]
}

function RenderNode({ value, fieldKey, context, nodeType, depth, ancestors }: RenderProps): ReactNode {
  // 1. 深度保护：超出封顶，回退为折叠占位
  if (depth > MAX_DEPTH) return <DeepFallback value={value} />

  // 2. null / undefined
  if (value == null) return <EmptyState text={value === null ? 'null' : '—'} />

  // 3. 已知字段语义增强（优先于通用分派）
  const known = matchKnownField(fieldKey, value, context, depth)
  if (known !== undefined) return known

  // 4. 标量
  if (typeof value === 'boolean') return <BoolValue value={value} />
  if (typeof value === 'number') return <NumberValue value={value} />
  if (typeof value === 'string') return <StringValue value={value} depth={depth} />

  // 5. 数组
  if (Array.isArray(value)) {
    if (ancestors.includes(value)) return <span className="text-[#71717a] italic text-[11px]">[循环引用]</span>
    if (value.length === 0) return <EmptyState text="空数组" />
    // 同质原始值 → 标签组
    if (isHomogeneousPrimitiveArray(value)) {
      return <TagList items={value as (string | number | boolean)[]} />
    }
    // 对象 / 异构数组 → 带左竖线的卡片列表
    const nextAncestors = [...ancestors, value]
    return (
      <div className="space-y-1.5">
        {value.map((item, idx) => (
          <div key={idx} className="border-l-2 border-[#27272a] pl-2.5 py-0.5">
            <RenderNode
              value={item}
              context={context}
              nodeType={nodeType}
              depth={depth + 1}
              ancestors={nextAncestors}
            />
          </div>
        ))}
      </div>
    )
  }

  // 6. 对象
  if (isPlainObject(value)) {
    if (ancestors.includes(value)) return <span className="text-[#71717a] italic text-[11px]">[循环引用]</span>
    const entries = Object.entries(value)
    if (entries.length === 0) return <EmptyState text="空对象" />
    return (
      <KeyValueGrid
        entries={entries}
        context={context}
        nodeType={nodeType}
        depth={depth}
        ancestors={[...ancestors, value]}
      />
    )
  }

  // 7. 兜底（function / symbol / bigint 等罕见类型）
  return <span className="text-[#d4d4d8] break-all text-[11px]">{String(value)}</span>
}

/* ─── 子组件 ─── */

/** 对象 → 键值对网格，逐项递归；深层缩进 + 超长折叠 */
function KeyValueGrid({
  entries,
  context,
  nodeType,
  depth,
  ancestors,
}: {
  entries: [string, unknown][]
  context: DataViewContext
  nodeType?: string
  depth: number
  ancestors: object[]
}) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? entries : entries.slice(0, MAX_GRID_ENTRIES)
  const rest = entries.length - visible.length

  return (
    <div className={`space-y-1.5 ${depth > 0 ? 'pl-2.5 border-l-2 border-[#27272a] bg-[#18181b]/40 rounded-r' : ''}`}>
      {visible.map(([k, v]) => (
        <div key={k} className="flex flex-col gap-0.5 min-w-0">
          <span className="text-[10px] text-[#71717a] font-medium break-all">{FIELD_LABEL[k.toLowerCase()] ?? k}</span>
          <div className="min-w-0">
            <RenderNode
              value={v}
              fieldKey={k}
              context={context}
              nodeType={nodeType}
              depth={depth + 1}
              ancestors={ancestors}
            />
          </div>
        </div>
      ))}
      {rest > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="text-[10px] text-[#1E5EFF] hover:underline cursor-pointer"
        >
          显示全部 {entries.length} 项（还有 {rest} 项被折叠）
        </button>
      )}
    </div>
  )
}

function BoolValue({ value }: { value: boolean }) {
  return <Tag color={value ? 'green' : 'default'}>{value ? '是' : '否'}</Tag>
}

function NumberValue({ value }: { value: number }) {
  return <span className="font-mono text-[11px] text-[#d4d4d8]">{value}</span>
}

/** 字符串按形态分派：URL / 内嵌 JSON / 多行 / 长文本 / 短文本 */
function StringValue({ value, depth }: { value: string; depth: number }) {
  const kind = detectStringKind(value)

  if (kind === 'url') {
    return (
      <a
        href={value}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-[#1E5EFF] hover:underline min-w-0"
      >
        <span className="truncate max-w-[320px]">{value}</span>
        <ExternalLink size={11} className="shrink-0" />
      </a>
    )
  }

  // 字符串里塞了 JSON / Python repr：容错解析后递归渲染
  if (kind === 'json-like') {
    const parsed = tryParseJsonLike(value)
    if (parsed !== undefined) {
      return <RenderNode value={parsed} context="generic" depth={depth + 1} ancestors={[]} />
    }
  }

  if (kind === 'multiline' || kind === 'long') {
    return <LongText text={value} />
  }

  return <span className="text-[11px] text-[#d4d4d8] break-all">{value}</span>
}

/** 长文本 / 多行文本 → 卡片 + 默认折叠前 N 字 */
function LongText({ text, tone = 'default' }: { text: string; tone?: 'default' | 'danger' }) {
  const [open, setOpen] = useState(false)
  const isLong = text.length > LONG_TEXT_THRESHOLD
  const bodyColor = tone === 'danger' ? 'text-rose-400' : 'text-[#d4d4d8]'
  return (
    <div
      className={`rounded bg-[#09090b] border p-2 ${
        tone === 'danger' ? 'border-rose-500/30' : 'border-[#27272a]'
      }`}
    >
      <div className={`${bodyColor} text-[11px] leading-relaxed whitespace-pre-wrap break-all`}>
        {open || !isLong ? text : `${text.slice(0, LONG_TEXT_THRESHOLD)}…`}
      </div>
      {isLong && (
        <button
          onClick={() => setOpen((v) => !v)}
          className="mt-1 text-[10px] text-[#1E5EFF] hover:underline cursor-pointer"
        >
          {open ? '收起' : `展开（共 ${text.length} 字）`}
        </button>
      )}
    </div>
  )
}

/** 同质原始值数组 → 标签组（>12 折叠 +N） */
function TagList({ items }: { items: (string | number | boolean)[] }) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? items : items.slice(0, MAX_TAGS)
  const rest = items.length - visible.length
  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((it, idx) => (
        <Tag key={idx} color="default" className="!text-[10px]">{String(it)}</Tag>
      ))}
      {rest > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="text-[10px] text-[#1E5EFF] hover:underline cursor-pointer"
        >
          +{rest}
        </button>
      )}
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return <span className="text-[#71717a] italic text-[11px]">{text}</span>
}

/** 超出 MAX_DEPTH 的折叠占位 */
function DeepFallback({ value }: { value: unknown }) {
  const count = Array.isArray(value)
    ? value.length
    : isPlainObject(value)
      ? Object.keys(value).length
      : 0
  return <span className="text-[#71717a] italic text-[11px]">深度嵌套（{count} 项），已折叠</span>
}

/** Markdown 文本块（output_summary / response 等 LLM 文本）：复用聊天渲染栈，超长可滚动 */
function MarkdownBlock({ content }: { content: string }) {
  return (
    <div className="rounded bg-[#09090b] border border-[#27272a] p-2 max-h-64 overflow-y-auto scrollbar-custom prose-chat">
      <Markdown content={content} />
    </div>
  )
}

/** agent_id → Agent 名称标签（名称由 DataViewEnhanceProvider 注入；查不到回退原 ID） */
function AgentNameTag({ agentId }: { agentId: string }) {
  const { agentNameMap } = useContext(EnhanceContext)
  const name = agentNameMap?.[agentId]
  return (
    <Tag color="#3B82F6">
      <WorkflowIcon size={10} />
      <span title={name ? agentId : undefined} className="inline-block max-w-[200px] truncate align-bottom">
        {name ?? agentId}
      </span>
    </Tag>
  )
}

/** 「查看原始数据」折叠兜底 —— 唯一保留 JSON.stringify 的地方，默认收起、仅排障 */
function RawJsonFallback({ value }: { value: unknown }) {
  let str: string
  try {
    str = JSON.stringify(value, null, 2)
  } catch {
    str = String(value)
  }
  if (!str || str === '{}' || str === '[]' || str === 'null' || str === '""') return null
  return (
    <details className="mt-2 group">
      <summary className="cursor-pointer hover:text-[#1E5EFF] list-none flex items-center gap-1 text-[#71717a] text-[10px]">
        <ChevronRight className="w-2.5 h-2.5 group-open:hidden" />
        <ChevronRight className="w-2.5 h-2.5 hidden group-open:inline rotate-90" />
        查看原始数据
      </summary>
      <pre className="rounded p-2 mt-1 overflow-x-auto max-h-64 scrollbar-custom font-mono bg-[#09090b] text-[#a1a1aa] border border-[#27272a] text-[10px]">
        {str}
      </pre>
    </details>
  )
}

/* ─── 已知字段语义注册表 ─── */

const DECISION_COLOR: Record<string, string> = {
  approve: '#10B981', approved: '#10B981', accept: '#10B981', pass: '#10B981',
  reject: '#EF4444', rejected: '#EF4444', deny: '#EF4444',
  skip: '#F59E0B', skipped: '#F59E0B',
}

const TIMEOUT_ACTION_LABEL: Record<string, string> = {
  fail: '超时即失败', auto_fail: '超时即失败',
  auto_reject: '超时自动驳回', reject: '超时自动驳回',
  auto_approve: '超时自动通过', approve: '超时自动通过',
  auto_skip: '超时自动跳过', skip: '超时自动跳过',
  continue: '超时后继续', resume: '超时后继续',
}

/**
 * key → 语义渲染。返回 undefined 表示未命中（回通用分派）。
 * 每条规则都对值类型做守卫，类型不符则放行给通用分派。
 */
function matchKnownField(
  fieldKey: string | undefined,
  value: unknown,
  context: DataViewContext,
  depth: number,
): ReactNode | undefined {
  if (!fieldKey) return undefined
  const k = fieldKey.toLowerCase()

  // 时间戳类 key（所有 context）
  if (looksLikeTimestamp(k, value)) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-[#d4d4d8]" title={value}>
        <Clock size={11} className="text-[#71717a]" />
        {formatTimestamp(value)}
      </span>
    )
  }

  // error / error_message → 红色文本
  if (typeof value === 'string' && (k === 'error' || k === 'error_message')) {
    return (
      <div className="flex gap-1.5 items-start">
        <AlertTriangle size={11} className="text-rose-400 shrink-0 mt-1" />
        <div className="flex-1 min-w-0">
          <LongText text={value} tone="danger" />
        </div>
      </div>
    )
  }

  // timeout_ms → 时长格式化
  if (k === 'timeout_ms' && typeof value === 'number') {
    return <span className="text-[11px] text-[#d4d4d8]" title={`${value} ms`}>{formatDuration(value)}</span>
  }

  // timeout_action → 中文徽标
  if (k === 'timeout_action' && typeof value === 'string') {
    return <Tag color="orange">{TIMEOUT_ACTION_LABEL[value.toLowerCase()] ?? value}</Tag>
  }

  // response（agent 输出）→ Markdown 卡（LLM 回复天然含格式）。
  // 键值网格已在字段上方展示「回答」标签，这里不再包 LabeledCard，避免双重标签。
  if (k === 'response' && typeof value === 'string') {
    return <MarkdownBlock content={value} />
  }

  // result（tool 输出）→ 自带边框的文本/递归内容（网格已有「执行结果」标签）
  if (k === 'result') {
    if (typeof value === 'string') {
      return <LongText text={value} />
    }
    if (typeof value === 'object' && value !== null) {
      return (
        <div className="rounded bg-[#09090b] border border-[#27272a] p-2">
          <RenderNode value={value} context="generic" depth={depth + 1} ancestors={[]} />
        </div>
      )
    }
  }

  // output_summary → Markdown 渲染（agent 输出摘要常含列表/代码等格式）
  if (k === 'output_summary' && typeof value === 'string') {
    return <MarkdownBlock content={value} />
  }

  // decision（human 审批）→ 结论徽标
  if (k === 'decision' && typeof value === 'string') {
    return <Tag color={DECISION_COLOR[value.toLowerCase()] ?? '#71717a'}>{value}</Tag>
  }

  // reason → 原因文本
  if (k === 'reason' && typeof value === 'string' && value.length > 0) {
    return <LongText text={value} />
  }

  // approved_by → 带 图标行
  if (k === 'approved_by' && typeof value === 'string') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-[#d4d4d8]">
        <UserCheck size={11} className="text-[#10B981]" />
        {value}
      </span>
    )
  }

  // selected_branch（gateway）→ 命中分支
  if (k === 'selected_branch' && typeof value === 'string') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-[#d4d4d8]">
        <GitBranch size={11} className="text-[#3B82F6]" />
        <code className="font-mono text-[#1E5EFF] break-all">{value}</code>
      </span>
    )
  }

  // condition（gateway）→ 条件表达式
  if (k === 'condition' && typeof value === 'string') {
    return (
      <code className="font-mono text-[10px] text-[#a1a1aa] bg-[#09090b] border border-[#27272a] rounded px-1.5 py-0.5 break-all">
        {value}
      </code>
    )
  }

  // agent_id → 解析为 Agent 名称（映射由父级注入，查不到回退原 ID）
  if (k === 'agent_id' && typeof value === 'string') {
    return <AgentNameTag agentId={value} />
  }

  // tool_name / tool_id → 带图标标签
  if ((k === 'tool_name' || k === 'tool_id') && typeof value === 'string') {
    return (
      <Tag color="#06B6D4">
        <Wrench size={10} />
        {value}
      </Tag>
    )
  }

  // options（string[]）→ 选项标签组
  if (k === 'options' && Array.isArray(value) && value.every((v) => typeof v === 'string')) {
    return (
      <div className="flex flex-wrap gap-1">
        {value.map((opt, idx) => (
          <Tag key={idx} color="#a1a1aa">{opt}</Tag>
        ))}
      </div>
    )
  }

  // node_type → 中文节点类型标签
  if (k === 'node_type' && typeof value === 'string') {
    return <Tag color="blue">{NODE_TYPE_LABEL[value] ?? value}</Tag>
  }

  // node_id → 等宽小字（通常为 UUID）
  if (k === 'node_id' && typeof value === 'string') {
    return <code className="font-mono text-[10px] text-[#a1a1aa] break-all">{value}</code>
  }

  return undefined
}

/* ─── 纯函数工具 ─── */

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** 全是同一种原始类型（string/number/boolean，不含 null/object） */
function isHomogeneousPrimitiveArray(arr: unknown[]): boolean {
  if (arr.length === 0) return false
  let kind: string | undefined
  for (const v of arr) {
    if (v === null || typeof v === 'object') return false
    const t = typeof v
    if (kind === undefined) kind = t
    else if (t !== kind) return false
  }
  return true
}

/** 容错解析 JSON / Python repr 字符串：先标准 JSON.parse，失败再把单引号转双引号重试（兼容历史 str(dict) 数据） */
function tryParseJsonLike(s: string): unknown {
  const t = s.trim()
  try {
    return JSON.parse(t)
  } catch {
    /* 不是合法 JSON */
  }
  try {
    return JSON.parse(t.replace(/'/g, '"'))
  } catch {
    /* 单引号替换后仍解析失败 */
  }
  return undefined
}

function detectStringKind(s: string): 'url' | 'json-like' | 'multiline' | 'long' | 'short' {
  if (/^https?:\/\/\S+$/i.test(s)) return 'url'
  const t = s.trim()
  if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
    if (tryParseJsonLike(t) !== undefined) return 'json-like'
  }
  if (s.includes('\n')) return 'multiline'
  if (s.length > LONG_TEXT_THRESHOLD) return 'long'
  return 'short'
}

/** ms → 人类可读时长（5 分钟 / 1 时 20 分） */
function formatDuration(ms: number): string {
  if (!Number.isFinite(ms)) return String(ms)
  if (ms < 1000) return `${ms} 毫秒`
  const totalSec = Math.floor(ms / 1000)
  if (totalSec < 60) return `${totalSec} 秒`
  const totalMin = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  if (totalMin < 60) return sec ? `${totalMin} 分 ${sec} 秒` : `${totalMin} 分钟`
  const hr = Math.floor(totalMin / 60)
  const min = totalMin % 60
  return min ? `${hr} 时 ${min} 分` : `${hr} 小时`
}

/** ISO → MM-DD HH:mm:ss；解析失败原样返回 */
function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  } catch {
    return iso
  }
}

/** key 看起来是时间戳字段 且值能被 Date.parse 解析 */
function looksLikeTimestamp(key: string, value: unknown): value is string {
  if (typeof value !== 'string') return false
  const isTimeKey =
    key === 'timestamp' ||
    key.endsWith('_at') ||
    key === 'timeout_deadline' ||
    key === 'deadline' ||
    key === 'time'
  if (!isTimeKey) return false
  return !Number.isNaN(Date.parse(value))
}

export default DataView

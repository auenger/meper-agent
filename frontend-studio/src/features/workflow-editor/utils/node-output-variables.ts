/**
 * 每种节点类型的默认输出变量定义表。
 *
 * 用于 VariableSelector 展示上游节点的可用字段，
 * 用户点击字段 Tag 即可插入 `{{node_id.field}}` 模板变量。
 */

import type { WorkflowNode } from '../../../services/workflows-api'
import type { VariableDefinition } from './variable-types'

export interface NodeOutputField {
  /** 字段名（如 "response"、"agent_id"） */
  name: string
  /** 人类可读的标签 */
  label: string
  /** 字段类型描述 */
  type: 'string' | 'object' | 'any' | 'number' | 'boolean'
  /** 简短说明 */
  description: string
}

/**
 * 节点类型 → 输出字段列表
 *
 * agent 节点为 API 返回体模型：固定字段（status/response/agent_id/files/
 * usage/needed_info）由引擎恒定提供；response 的结构化字段由
 * config.response_schema 声明，见 getEffectiveOutputVariables 的合并逻辑。
 */
export const NODE_OUTPUT_VARIABLES: Record<string, NodeOutputField[]> = {
  start: [
    { name: 'input', label: '原始输入', type: 'any', description: '工作流的原始输入参数' },
  ],
  end: [
    { name: 'output_mapping', label: '输出映射', type: 'object', description: '输出字段映射结果' },
  ],
  agent: [
    { name: 'status', label: '状态', type: 'string', description: '"ok"（正常）| "insufficient"（信息不足信号，配合信息不足分支路由）' },
    { name: 'response', label: 'Agent 响应', type: 'any', description: '核心内容：默认文本；声明返回结构后为原生对象/数组（{{node.response.字段}} 直接取值）' },
    { name: 'agent_id', label: 'Agent ID', type: 'string', description: '执行的 Agent ID' },
    { name: 'files', label: '产出文件', type: 'object', description: '生成的文件列表（{{node.files.0.file_id}} 取第一个文件的 ID）' },
    { name: 'usage', label: 'Token 用量', type: 'object', description: '本次执行的 token 用量（{{node.usage.total_tokens}}）' },
    { name: 'needed_info', label: '待补充信息', type: 'string', description: '信息不足时 Agent 说明需要补充的内容，其余时候为空字符串' },
  ],
  tool: [
    { name: 'tool_name', label: '工具名称', type: 'string', description: '调用的工具名称' },
    { name: 'tool_description', label: '工具描述', type: 'string', description: '工具的描述信息' },
    { name: 'instructions', label: '工具指令', type: 'string', description: '工具的执行指令' },
    { name: 'result', label: '执行结果', type: 'any', description: 'MCP 工具执行返回的结果（JSON 文本时可 {{node.result.0.text.field}} 钻取）' },
    { name: 'tool_id', label: '工具 ID', type: 'string', description: '调用的工具 ID（MCP 工具）' },
  ],
  gateway: [
    { name: 'selected_branch', label: '选中分支', type: 'string', description: '匹配到的条件分支' },
    { name: 'condition', label: '条件表达式', type: 'string', description: '触发该分支的条件' },
  ],
  human: [
    { name: 'decision', label: '审批决定', type: 'string', description: 'approve / reject' },
    { name: 'comment', label: '审批意见', type: 'any', description: '审批意见（文本或结构化数据，JSON 模式下可用 {{node.comment.field}} 钻取）' },
    { name: 'approver', label: '审批人', type: 'string', description: '审批人用户 ID' },
    { name: 'decided_at', label: '审批时间', type: 'string', description: '审批完成时间' },
  ],
  parallel: [
    { name: 'branches', label: '分支列表', type: 'object', description: '所有分支的执行结果' },
    { name: 'join_strategy', label: '合并策略', type: 'string', description: '分支结果的合并策略' },
    { name: 'scope', label: '作用域', type: 'string', description: '并行执行的变量作用域' },
  ],
  subflow: [
    { name: 'child_task_id', label: '子任务 ID', type: 'string', description: '子工作流任务 ID' },
    { name: 'child_output', label: '子任务输出', type: 'any', description: '子工作流的执行结果' },
    { name: 'workflow_id', label: '工作流 ID', type: 'string', description: '子工作流的模板 ID' },
  ],
  kb_search: [
    { name: 'results', label: '检索结果', type: 'object', description: '命中的知识块列表（{{node.results.0.text}} 取第一个的内容）' },
    { name: 'query', label: '查询词', type: 'string', description: '实际使用的检索查询' },
  ],
}

/* ─── agent response 声明结构 → 平铺输出字段 ─── */

interface _ResponseFieldLike {
  name?: string
  type?: string
  is_list?: boolean
  description?: string
  fields?: _ResponseFieldLike[]
}

/** 把 response_schema 声明的字段递归平铺为 response.xxx 引用项。
 * 列表字段用示例下标 0（Jinja 数字下标语法，如 response.tags.0 / response.authors.0.name）。 */
function agentDeclaredResponseFields(schema: unknown): NodeOutputField[] {
  if (!schema || typeof schema !== 'object') return []
  const s = schema as { type?: string; fields?: _ResponseFieldLike[] }
  if (s.type !== 'object' && s.type !== 'array') return []
  // array：元素下标 0 作为示例（Jinja 数字下标语法）
  const prefix = s.type === 'array' ? 'response.0' : 'response'
  const toFieldType = (t?: string): NodeOutputField['type'] => {
    if (t === 'number' || t === 'boolean') return t
    if (t === 'object') return 'object'
    return 'string' // string / enum 都按 string 提示
  }
  const out: NodeOutputField[] = []
  const walk = (fields: _ResponseFieldLike[] | undefined, base: string) => {
    for (const f of fields ?? []) {
      if (!f?.name) continue
      // 列表字段 → response.x.0；对象列表 → 每元素结构提示
      const fieldPath = f.is_list ? `${base}.${f.name}.0` : `${base}.${f.name}`
      out.push({
        name: fieldPath,
        label: f.name,
        type: toFieldType(f.type),
        description: (f.is_list ? '列表字段，下标取值（如 .0）' : '') + (f.description ?? ''),
      })
      if (f.type === 'object') {
        walk(f.fields, fieldPath)
      }
    }
  }
  walk(s.fields, prefix)
  return out
}

/**
 * 获取指定节点类型的输出字段列表（静态表）
 */
export function getNodeOutputFields(nodeType: string): NodeOutputField[] {
  return NODE_OUTPUT_VARIABLES[nodeType] ?? []
}

/* ─── v2 兼容函数 ─── */

/**
 * 获取节点的有效输出变量列表。
 *
 * - agent 节点：固定字段（API 返回体承诺）∪ response 声明结构的平铺字段。
 *   忽略 config.output_variables——那是旧前端自动初始化的装饰值（后端从不
 *   消费），且会遮蔽固定字段。
 * - 其他节点：优先 config.output_variables（start 的输入参数定义等真实
 *   声明），否则回退静态表。
 */
export function getEffectiveOutputVariables(node: WorkflowNode): VariableDefinition[] | NodeOutputField[] {
  if (node.type === 'agent') {
    return [
      ...(NODE_OUTPUT_VARIABLES.agent ?? []),
      ...agentDeclaredResponseFields((node.config as Record<string, unknown> | undefined)?.response_schema),
    ]
  }
  const userDefined = node.config?.output_variables
  if (Array.isArray(userDefined) && userDefined.length > 0) {
    return userDefined as VariableDefinition[]
  }
  return getNodeOutputFields(node.type)
}

/**
 * 判断节点是否有用户自定义的输出变量
 */
export function hasUserDefinedOutputVariables(node: WorkflowNode): boolean {
  const vars = node.config?.output_variables
  return Array.isArray(vars) && vars.length > 0
}

/**
 * 每种节点类型的固定输入变量定义。
 * 用于 Config Panel 展示节点可以引用的上游输入变量。
 */
export interface NodeInputVariable {
  name: string
  label: string
  type: string
  description: string
  required: boolean
}

/**
 * 节点类型 → 输入变量列表
 * 有些节点定义输入变量（如 agent→user_query），有些则没有
 */
export const NODE_INPUT_VARIABLES: Record<string, NodeInputVariable[]> = {
  agent: [],
  tool: [],
  gateway: [],
  end: [],
  start: [],
  human: [],
  parallel: [],
}

/**
 * 获取指定节点类型的输入变量列表
 */
export function getNodeInputVariables(nodeType: string): NodeInputVariable[] {
  return NODE_INPUT_VARIABLES[nodeType] ?? []
}

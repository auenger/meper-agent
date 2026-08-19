/**
 * task-flow-utils — 执行流程可视化共享工具。
 *
 * TaskFlowTimeline（阶段时间线）与 TaskFlowGraph（xyflow 节点图）共用：
 * - 节点执行状态推导
 * - 节点类型 → 中文标签 / 颜色
 *
 * 节点 id（node_id）是 timeline 事件、variables、checkpoint 之间的 join key，
 * 与 WorkflowDesigner 的 WorkflowNode.node_id 同源。
 */
import type { TimelineEvent } from '../../services/tasks-api'

/** 单个节点的执行状态（用于徽标颜色 / 图标 / 节点高亮） */
export type NodeExecState = 'completed' | 'executing' | 'failed' | 'rejected' | 'waiting' | 'pending'

/** 执行状态 → 主色（与 TaskFlowTimeline 的 STATE_META 对齐） */
export const STATE_COLOR: Record<NodeExecState, string> = {
  completed: '#10B981',
  executing: '#3B82F6',
  failed: '#EF4444',
  rejected: '#EF4444',
  waiting: '#8B5CF6',
  pending: '#71717a',
}

/** 节点类型 → 中文标签（与 NODE_TYPE_LABEL 对齐） */
export const NODE_TYPE_LABEL: Record<string, string> = {
  start: '输入节点', end: '输出节点', agent: 'Agent 节点',
  tool: '工具节点', gateway: '网关节点', parallel: '并行节点', human: '人工审批节点',
}

/**
 * 任务级「拒绝」信号：timeline 中存在 reject 事件、或超时 auto_reject/fail。
 * 用于兜底不带 node_id 的存量审批/超时事件——事件无法归属到节点分组时，
 * 暂停节点（checkpoint.paused_at_node 命中且未清空）凭该信号判为已拒绝。
 */
export function hasTaskRejectSignal(timeline: TimelineEvent[]): boolean {
  return timeline.some((e) =>
    e.event_type === 'reject' ||
    (e.event_type === 'timeout' && (e.data?.timeout_action === 'auto_reject' || e.data?.timeout_action === 'fail')),
  )
}

/**
 * 从一个节点的相关 timeline 事件推导其执行状态。
 *
 * 规则（按优先级）：
 * 1. 有 node_failed → failed
 * 2. 审批被拒绝（reject 事件 / 超时 auto_reject·fail 事件 / decision='reject'）→ rejected
 * 3. pausedAtThisNode（checkpoint.paused_at_node 命中）→ waiting（人工审批中）；
 *    若存在任务级拒绝信号（存量事件无 node_id 无法归属节点）→ rejected
 * 4. 有 node_complete → completed
 * 5. 有 node_start 但无 complete/failed → executing
 * 6. 否则 → pending
 *
 * 注：human 节点在暂停前就写入了 node_complete（引擎恢复信号），因此通过/跳过后
 * 走规则 4 显示「已完成」；拒绝时 checkpoint 不会清空（reject / 超时 fail 均如此），
 * 必须靠规则 2、3（在 waiting 之前）压过 pausedAtThisNode。decision 参数取
 * variables[node_id].decision，taskRejected 取 hasTaskRejectSignal(全量 timeline)。
 */
export function getNodeExecState(
  events: TimelineEvent[],
  pausedAtThisNode: boolean,
  decision?: string,
  taskRejected?: boolean,
): NodeExecState {
  const types = new Set(events.map((e) => e.event_type))
  if (types.has('node_failed')) return 'failed'
  const rejectedByTimeout = events.some(
    (e) => e.event_type === 'timeout' && (e.data?.timeout_action === 'auto_reject' || e.data?.timeout_action === 'fail'),
  )
  if (types.has('reject') || rejectedByTimeout || decision === 'reject') return 'rejected'
  if (pausedAtThisNode) return taskRejected ? 'rejected' : 'waiting'
  if (types.has('node_complete')) return 'completed'
  if (types.has('node_start')) return 'executing'
  return 'pending'
}

/** 阶段时间线用：一个节点的聚合信息 */
export interface NodeStageInfo {
  nodeId: string
  nodeType: string
  state: NodeExecState
  events: TimelineEvent[]
  duration?: string
  /** 节点标签（可选，来自事件 data.node_label） */
  label?: string
  /** 节点 token 消耗（agent 节点 node_complete 事件 data.usage.total_tokens） */
  tokenTotal?: number
}

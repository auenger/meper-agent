/**
 * 各节点类型的默认配置和初始位置。
 */
import type { WorkflowNode, NextNodeRef } from '../../../services/workflows-api'

/** 新节点的默认间距 */
const SPACING_X = 220
const SPACING_Y = 100

/** 每种类型对应的默认 config */
const DEFAULT_CONFIGS: Record<string, Record<string, unknown>> = {
  start: {
    input_schema: { type: 'object', properties: {} },
    next_nodes: [] as NextNodeRef[],
  },
  end: {},
  agent: {
    agent_id: '',
    input_query: '',
    input_prompt: '',
    temperature: 0.7,
    max_retry: 3,
    timeout_ms: 300000,
    next_nodes: [] as NextNodeRef[],
  },
  tool: {
    tool_id: '',
    params: {},
    timeout_ms: 30000,
    next_nodes: [] as NextNodeRef[],
  },
  gateway: {
    conditions: [],
    default_branch: '',
  },
  parallel: {
    branches: [],
    join_strategy: 'all',
    scope: 'shared',
  },
  human: {
    title: '',
    description: '',
    options: ['approve', 'reject'] as string[],
    timeout_minutes: 60,
    timeout_action: 'fail',
    next_nodes: [] as NextNodeRef[],
  },
}

/**
 * 生成一个新节点的默认配置。
 */
export function getDefaultNodeConfig(type: string): Record<string, unknown> {
  return { ...(DEFAULT_CONFIGS[type] ?? {}) }
}

/**
 * 计算新节点的自动位置（基于现有节点排列）。
 */
export function computeDefaultPosition(
  existingNodes: WorkflowNode[],
  type: string,
): { x: number; y: number } {
  // 如果是 start 节点，放在最左边
  if (type === 'start') return { x: 50, y: 250 }

  const count = existingNodes.length
  if (count === 0) return { x: 50, y: 250 }

  // 基于最后一个节点的位置偏移
  const last = existingNodes[count - 1]
  return {
    x: Math.max(last.position.x + SPACING_X, 330),
    y: Math.max(last.position.y + (count % 2 === 0 ? SPACING_Y : -SPACING_Y), 50),
  }
}

/**
 * 生成新节点的唯一 ID。
 */
export function generateNodeId(): string {
  return `node_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`
}

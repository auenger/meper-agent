/**
 * Tasks API service — wraps backend /tasks endpoints.
 *
 * Uses the shared apiClient instance (auto auth header + 401 refresh).
 * Response fields are snake_case per backend contract.
 */
import { apiClient } from '../lib/api-client'

/* ─── Types (snake_case, matches backend schemas) ─── */

export type TaskStatusValue =
  | 'pending'
  | 'running'
  | 'waiting_human'
  | 'completed'
  | 'failed'
  | 'cancelled'

/**
 * 看板使用的有序状态列表（5 列顺序）。
 *
 * 注：pending（待执行）不作为看板列 - 定时任务统一在「定时任务」页面管理，
 * 手动 scheduled_at 任务的待执行态不在此展示。
 */
export const BOARD_STATUSES: TaskStatusValue[] = [
  'running',
  'waiting_human',
  'completed',
  'failed',
  'cancelled',
]

export interface TimelineEvent {
  timestamp: string
  event_type: string
  data: Record<string, unknown>
  actor: string
}

/**
 * 节点执行进度（看板卡片用）。
 * - completedCount：已结束（complete + failed）的去重节点数
 * - currentNodeId：当前正在执行的节点（最后一个 node_start 且未 complete 的）
 * - currentNodeType：当前节点的类型（node_start 事件 data 里携带）
 */
export interface NodeProgress {
  completedCount: number
  currentNodeId?: string
  currentNodeType?: string
}

/**
 * 扫描 timeline 解析节点执行进度。
 * - 收集 node_complete / node_failed 的 node_id 去重计数
 * - 找出最后一个 node_start 且未在结束集合中的节点作为 current
 */
export function parseNodeProgress(timeline?: TimelineEvent[]): NodeProgress | null {
  if (!timeline || timeline.length === 0) return null

  const startedNodes = new Map<string, string>() // node_id -> node_type
  const finishedNodes = new Set<string>()

  // 按时间顺序遍历（timeline 默认按时间升序）
  for (const evt of timeline) {
    const data = evt.data ?? {}
    const nodeId = typeof data.node_id === 'string' ? data.node_id : undefined
    if (!nodeId) continue

    if (evt.event_type === 'node_start') {
      const nodeType = typeof data.node_type === 'string' ? data.node_type : undefined
      startedNodes.set(nodeId, nodeType ?? '')
    } else if (evt.event_type === 'node_complete' || evt.event_type === 'node_failed') {
      finishedNodes.add(nodeId)
    }
  }

  if (startedNodes.size === 0 && finishedNodes.size === 0) return null

  // 找当前节点：已 start 但未 finish
  let currentNodeId: string | undefined
  let currentNodeType: string | undefined
  for (const [nid, ntype] of startedNodes) {
    if (!finishedNodes.has(nid)) {
      currentNodeId = nid
      currentNodeType = ntype || undefined
      break
    }
  }

  return {
    completedCount: finishedNodes.size,
    currentNodeId,
    currentNodeType,
  }
}

export interface TaskError {
  node_id?: string
  node_type?: string
  error_message: string
  error_code: string
  timestamp?: string
}

/**
 * Checkpoint — persisted when workflow pauses at a Human node.
 */
export interface Checkpoint {
  paused_at_node: string
  completed_nodes: string[]
  variable_snapshot: Record<string, unknown>
  paused_at: string
  human_context: {
    node_id?: string
    title?: string
    description?: string
    options?: string[]
    timeout_ms?: number
    timeout_action?: string
    view?: { sections?: unknown[] }
  }
  timeout_deadline?: string | null
  timeout_action: string
}

export interface TaskSummary {
  id: string
  workflow_id: string
  workflow_version: string
  status: TaskStatusValue
  input: Record<string, unknown>
  output?: Record<string, unknown> | null
  parent_task_id?: string | null
  created_by: string
  created_by_type: string
  version: number
  error?: TaskError | null
  checkpoint?: Checkpoint | null
  scheduled_at?: string | null
  trigger_id?: string
  /** 本次 task 所有 agent 节点累计 token 用量（后端 engine 写回） */
  total_tokens?: number
  created_at: string
  updated_at: string
}

export interface TaskDetail extends TaskSummary {
  variables: Record<string, unknown>
  call_chain: string[]
  timeline: TimelineEvent[]
}

export interface TaskListParams {
  page?: number
  page_size?: number
  status?: string
  created_by?: string
  workflow_id?: string
  /** 按触发器过滤：只返回该 trigger 触发产生的任务（source=trigger_scheduled）。 */
  trigger_id?: string
  /** 按来源过滤：manual / trigger / trigger_scheduled。 */
  source?: string
}

export interface TaskListResponse {
  items: TaskSummary[]
  total: number
  page: number
  page_size: number
}

export interface TaskStats {
  global_running: number
  global_pending: number
  global_max: number
  user_stats: Array<{ user_id: string; running: number }>
}

export interface TaskCreatePayload {
  workflow_id: string
  input: Record<string, unknown>
  scheduled_at?: string | null
}

/**
 * comment 支持三种形态（与后端 _normalize_comment 对齐）：
 * - string：纯文本（老用法，向后兼容）
 * - { type: 'text', value: string }：文本
 * - { type: 'json', value: unknown }：结构化数据，value 原样存入 variables，
 *   下游可用 {{node.comment.field}} 钻取
 */
export type CommentValue =
  | string
  | { type: 'text'; value: string }
  | { type: 'json'; value: unknown }

export interface TaskIntervenePayload {
  action: string
  reason?: string
  comment?: CommentValue
  version: number
}

export interface TaskInterveneResponse {
  task_id: string
  status: TaskStatusValue
  version: number
  message: string
}

export interface WorkflowRegistryEntry {
  _id: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  workflow_id: string
  has_human_node: boolean
  version: string
  tags: string[]
  published: boolean
}

/**
 * Task 输出文件（snake_case，与后端 FileRefResponse 对齐）。
 * Story 4-15-UI：前端查看/下载 Agent 节点产出到 file_library 的文件。
 */
export interface TaskOutputFile {
  id?: string
  _id: string
  owner_user_id: string
  storage_key: string
  name: string
  size: number
  mime_type: string
  sha256: string
  origin_kind: string
  origin_id: string
  status: string
  created_at: string
  updated_at: string
}

/**
 * Agent 节点执行详情（按需从 LangGraph checkpointer thread 读取）。
 * 对齐后端 NodeTimelineResponse / NodeTimelineEntry（snake_case）。
 */
export interface NodeTimelineEntry {
  type: 'thinking' | 'text' | 'tool_call' | 'tool_result' | 'tool' | 'user'
  content?: string
  tool_name?: string
  args?: Record<string, unknown>
  id?: string
}

export interface NodeTimelineResponse {
  task_id: string
  node_id: string
  thread_id: string
  timeline: NodeTimelineEntry[]
  message_count: number
}

/* ─── API methods ─── */

export const tasksApi = {
  /**
   * List tasks with optional filtering and pagination.
   * GET /api/v1/tasks
   */
  async list(params: TaskListParams = {}): Promise<TaskListResponse> {
    const res = await apiClient.get<TaskListResponse>('/api/v1/tasks', { params })
    return res.data
  },

  /**
   * Get a single task by ID.
   * GET /api/v1/tasks/{id}
   */
  async get(taskId: string): Promise<TaskDetail> {
    const res = await apiClient.get<TaskDetail>(`/api/v1/tasks/${encodeURIComponent(taskId)}`)
    return res.data
  },

  /**
   * Create a new task.
   * POST /api/v1/tasks
   */
  async create(data: TaskCreatePayload): Promise<TaskDetail> {
    const res = await apiClient.post<TaskDetail>('/api/v1/tasks', data)
    return res.data
  },

  /**
   * Intervene a running task (cancel, retry, approve, reject, etc.).
   * POST /api/v1/tasks/{id}/intervene
   */
  async intervene(taskId: string, data: TaskIntervenePayload): Promise<TaskInterveneResponse> {
    const res = await apiClient.post<TaskInterveneResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/intervene`,
      data,
    )
    return res.data
  },

  /**
   * Delete a terminal-state task.
   * DELETE /api/v1/tasks/{id}
   */
  async remove(taskId: string): Promise<void> {
    await apiClient.delete(`/api/v1/tasks/${encodeURIComponent(taskId)}`)
  },

  /**
   * List files registered to a task.
   * GET /api/v1/tasks/{id}/outputs
   * Story 4-15-UI: 前端查看/下载 Agent 节点产出到 file_library 的文件
   */
  async listOutputs(taskId: string): Promise<TaskOutputFile[]> {
    try {
      const res = await apiClient.get<TaskOutputFile[]>(
        `/api/v1/tasks/${encodeURIComponent(taskId)}/outputs`,
      )
      return res.data
    } catch (err) {
      // 404 = 该任务暂无产物文件（后端对无产物任务返回 404），语义化为空列表，不当作错误
      const status =
        (err as { statusCode?: number })?.statusCode ??
        (err as { response?: { status?: number } })?.response?.status
      if (status === 404) return []
      throw err
    }
  },

  /**
   * Get Agent node execution detail (thinking / tool_call / tool_result / text).
   * Read on demand from the LangGraph checkpointer thread.
   * GET /api/v1/tasks/{id}/nodes/{nodeId}/timeline
   */
  async getNodeTimeline(taskId: string, nodeId: string): Promise<NodeTimelineResponse> {
    const res = await apiClient.get<NodeTimelineResponse>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/timeline`,
    )
    return res.data
  },

  /**
   * Get task statistics (running/pending/limits).
   * GET /api/v1/tasks/stats
   */
  async getStats(): Promise<TaskStats> {
    const res = await apiClient.get<TaskStats>('/api/v1/tasks/stats')
    return res.data
  },

  /**
   * List audit logs for a task.
   * GET /api/v1/tasks/{id}/audit-logs
   */
  async getAuditLogs(taskId: string, limit = 50): Promise<{ items: unknown[]; total: number }> {
    const res = await apiClient.get(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/audit-logs`,
      { params: { limit } },
    )
    return res.data
  },

  /**
   * List published workflows (for task creation selector).
   * GET /api/v1/workflow-registry
   */
  async listWorkflows(search?: string): Promise<{ items: WorkflowRegistryEntry[]; total: number }> {
    const res = await apiClient.get('/api/v1/workflow-registry', {
      params: { page: 1, page_size: 100, ...(search ? { search } : {}) },
    })
    return res.data
  },

}

/* ─── Query key factory ─── */

export const taskKeys = {
  all: ['tasks'] as const,
  lists: () => [...taskKeys.all, 'list'] as const,
  list: (params: TaskListParams) => [...taskKeys.lists(), params] as const,
  details: () => [...taskKeys.all, 'detail'] as const,
  detail: (id: string) => [...taskKeys.details(), id] as const,
  logs: (id: string) => [...taskKeys.detail(id), 'logs'] as const,
  outputs: (id: string) => [...taskKeys.detail(id), 'outputs'] as const,
  nodeTimeline: (id: string, nodeId: string) => [...taskKeys.detail(id), 'nodeTimeline', nodeId] as const,
  stats: () => [...taskKeys.all, 'stats'] as const,
  workflows: () => [...taskKeys.all, 'workflows'] as const,
}

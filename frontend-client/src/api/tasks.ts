/**
 * Tasks API — 任务详情/干预/产物。
 *
 * 复用 ./client 的 apiRequest（自动带 token + 401 refresh + ApiError）。
 * 字段 snake_case，与后端 schema 对齐；类型精简自 frontend-studio 的 tasks-api.ts，
 * 只取 dispatch_workflow 卡片需要的部分。
 *
 * 鉴权模式分流：AUTH_MODE === 'apikey'（嵌入/访客）时全部走外部接口
 * /v1/ext/tasks/* 与 /v1/ext/workflows/*；否则走内部 /v1/tasks/* 与
 * /v1/workflows/*。内部接口只认 JWT，apikey 调用会 401，故必须分流。
 */
import { apiRequest, AUTH_MODE } from './client'

export type TaskStatusValue =
  | 'pending'
  | 'running'
  | 'waiting_human'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface TaskError {
  node_id?: string
  node_type?: string
  error_message: string
  error_code: string
  timestamp?: string
}

export interface Checkpoint {
  paused_at_node: string
  completed_nodes: string[]
  paused_at: string
  human_context: {
    node_id?: string
    title?: string
    description?: string
    options?: string[]
    timeout_ms?: number
    timeout_action?: string
  }
  timeout_deadline?: string | null
  timeout_action: string
}

export interface TimelineEvent {
  timestamp: string
  event_type: string
  data: Record<string, unknown>
  actor: string
}

export interface TaskOutputFile {
  id?: string
  _id: string
  name: string
  size: number
  mime_type: string
  origin_kind: string
  origin_id: string
  created_at: string
}

export interface TaskDetail {
  id: string
  workflow_id: string
  status: TaskStatusValue
  input: Record<string, unknown>
  output?: Record<string, unknown> | null
  created_by: string
  created_by_type: string
  version: number
  error?: TaskError | null
  checkpoint?: Checkpoint | null
  timeline: TimelineEvent[]
  total_tokens?: number
  created_at: string
  updated_at: string
}

export type CommentValue =
  | string
  | { type: 'text'; value: string }
  | { type: 'json'; value: unknown }

export interface TaskIntervenePayload {
  action: string
  comment?: CommentValue
  version: number
  /** rewind 专用：退回目标节点（须在 checkpoint.completed_nodes 中且非当前暂停节点）。 */
  target_node_id?: string
}

export interface TaskInterveneResponse {
  task_id: string
  status: TaskStatusValue
  version: number
  message: string
}

/** 工作流节点定义（取自 GET /v1/workflows/{id} 的 nodes[]，用于 node_id → label 映射）。 */
export interface WorkflowNode {
  node_id: string
  type: string
  label: string
}

export interface WorkflowDetail {
  id: string
  name: string
  nodes: WorkflowNode[]
}

/** Agent 节点执行 trace（按需从 checkpointer thread 读取，GET /tasks/{id}/nodes/{nid}/timeline）。 */
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
  timeline: NodeTimelineEntry[]
  message_count: number
}

const PATH = (id: string) => `/v1/tasks/${encodeURIComponent(id)}`
const EXT_TASK_PATH = (id: string) => `/v1/ext/tasks/${encodeURIComponent(id)}`
const EXT_WORKFLOW_PATH = (id: string) => `/v1/ext/workflows/${encodeURIComponent(id)}`

/**
 * apikey 模式下任务详情走外部接口 /v1/ext/tasks/{id}（返回 ExtTaskResponse，
 * 字段是 TaskDetail 的子集：缺 timeline/version/checkpoint/created_by 等）。
 * 这里补上缺省值，让调用方拿到的对象形状与 TaskDetail 一致，避免 undefined 崩溃。
 * （其余子接口 intervene/outputs/node-timeline/workflow 在 apikey 模式下也走 ext，
 * 见 tasksApi 各方法。）
 */
async function getTaskDetail(taskId: string): Promise<TaskDetail> {
  if (AUTH_MODE === 'apikey') {
    const ext = await apiRequest<Partial<TaskDetail>>(
      `/v1/ext/tasks/${encodeURIComponent(taskId)}`,
    )
    return {
      id: ext.id ?? taskId,
      workflow_id: ext.workflow_id ?? '',
      status: ext.status ?? 'pending',
      input: ext.input ?? {},
      output: ext.output ?? null,
      created_by: ext.created_by ?? '',
      created_by_type: ext.created_by_type ?? '',
      version: ext.version ?? 1,
      error: ext.error ?? null,
      checkpoint: ext.checkpoint ?? null,
      timeline: ext.timeline ?? [],
      created_at: ext.created_at ?? '',
      updated_at: ext.updated_at ?? '',
    }
  }
  return apiRequest<TaskDetail>(PATH(taskId))
}

export const tasksApi = {
  /** GET 任务详情 — jwt 模式走 /v1/tasks/{id}（完整），apikey 模式走 /v1/ext/tasks/{id}（精简）。 */
  get(taskId: string): Promise<TaskDetail> {
    return getTaskDetail(taskId)
  },

  /**
   * POST intervene — approve/reject/skip/retry/resume/cancel，带 version 乐观锁。
   * jwt 模式走 /v1/tasks/{id}/intervene；apikey 模式走 /v1/ext/tasks/{id}/intervene。
   */
  intervene(taskId: string, body: TaskIntervenePayload): Promise<TaskInterveneResponse> {
    const path = AUTH_MODE === 'apikey' ? `${EXT_TASK_PATH(taskId)}/intervene` : `${PATH(taskId)}/intervene`
    return apiRequest<TaskInterveneResponse>(path, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  /**
   * GET 产物文件列表（无产物返回 404，语义化为空列表）。
   * jwt 模式走 /v1/tasks/{id}/outputs；apikey 模式走 /v1/ext/tasks/{id}/outputs。
   */
  async listOutputs(taskId: string): Promise<TaskOutputFile[]> {
    const path = AUTH_MODE === 'apikey' ? `${EXT_TASK_PATH(taskId)}/outputs` : `${PATH(taskId)}/outputs`
    try {
      return await apiRequest<TaskOutputFile[]>(path)
    } catch (err) {
      if ((err as { status?: number })?.status === 404) return []
      throw err
    }
  },

  /**
   * GET 工作流定义（取 nodes 建 node_id→label 映射）。无权限时抛错，调用方降级。
   * jwt 模式走 /v1/workflows/{id}；apikey 模式走 /v1/ext/workflows/{id}
   * （ExtWorkflowDetailResponse.nodes 含 node_id/type/label，结构兼容 WorkflowDetail）。
   */
  getWorkflow(workflowId: string): Promise<WorkflowDetail> {
    const path = AUTH_MODE === 'apikey' ? EXT_WORKFLOW_PATH(workflowId) : `/v1/workflows/${encodeURIComponent(workflowId)}`
    return apiRequest<WorkflowDetail>(path)
  },

  /**
   * GET Agent 节点 REACT trace（thinking/tool_call/text）。
   * jwt 模式走 /v1/tasks/{id}/nodes/{nid}/timeline；apikey 模式走 /v1/ext/tasks/{id}/nodes/{nid}/timeline。
   */
  getNodeTimeline(taskId: string, nodeId: string): Promise<NodeTimelineResponse> {
    const base = AUTH_MODE === 'apikey' ? EXT_TASK_PATH(taskId) : PATH(taskId)
    return apiRequest<NodeTimelineResponse>(
      `${base}/nodes/${encodeURIComponent(nodeId)}/timeline`,
    )
  },
}

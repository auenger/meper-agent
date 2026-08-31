/**
 * Agent API service — wraps backend /agents endpoints.
 *
 * Uses the shared apiClient instance (auto auth header + 401 refresh).
 * Response fields are snake_case per backend contract.
 */
import { apiClient, type NormalizedApiError } from '../lib/api-client'
import { ENV } from '../config/env'
import { REFRESH_TOKEN_KEY, useAuthStore } from '../stores/auth-store'
import { authApi } from './auth-api'
import type { TokenUsage } from '../types'

/* ─── Types (snake_case, matches backend schemas) ─── */

export type AgentStatus = 'draft' | 'published' | 'archived'

export interface Agent {
  id: string
  name: string
  description: string
  avatar: string
  welcome_message: string
  recommended_items: { label: string; prompt: string }[]
  prompt_slots: Record<string, string>
  /** Skill tool IDs (source=markdown) */
  skill_ids: string[]
  /** MCP connection IDs (tools loaded from remote MCP servers) */
  mcp_connection_ids: string[]
  /** Built-in tool whitelist (bash/read/write) */
  builtin_config: string[]
  workflow_ids: string[]
  knowledge_base_ids: string[]
  default_model: string
  /** Whether this Agent can be used by the realtime voice entry. */
  voice_enabled: boolean
  max_retry: number
  /** Session token budget (0 = use global DEFAULT_SESSION_MAX_TOKENS). */
  max_tokens: number
  status: AgentStatus
  created_at: string
  updated_at: string
}

export interface AgentCreateInput {
  /** Agent 名称（唯一必填） */
  name: string
  /** Agent 简要描述 */
  description?: string
}

export interface AgentUpdateInput {
  name: string
  description?: string
  /** 头像 URL 路径；空串=清除回默认 logo */
  avatar?: string
  /** 终端用户首屏欢迎词（Markdown） */
  welcome_message?: string
  /** 终端用户首屏推荐问题/操作 */
  recommended_items?: { label: string; prompt: string }[]
  /** 提示词卡槽内容 */
  prompt_slots?: Record<string, string>
  /** Skill tool IDs (source=markdown) */
  skill_ids?: string[]
  /** MCP connection IDs */
  mcp_connection_ids?: string[]
  /** Built-in tool whitelist (bash/read/write) */
  builtin_config?: string[]
  workflow_ids?: string[]
  knowledge_base_ids?: string[]
  default_model?: string
  /** Enable realtime voice conversations for this Agent. */
  voice_enabled?: boolean
  max_retry?: number
  /** Session token budget (0 = use global default). */
  max_tokens?: number
}

/** Model config update payload — kept for backward compat type exports */
export interface ModelConfigUpdateInput {
  default_model: string
  max_retry: number
}

export interface AgentListParams {
  page?: number
  page_size?: number
  name?: string
  /** Agent status, or "all" to return every status. Defaults to published. */
  status?: AgentStatus | 'all'
}

export interface AgentListResponse {
  items: Agent[]
  total: number
  page: number
  page_size: number
}

/* ─── Execution types (invoke / stream) ─── */

export interface ExecutionRequest {
  input: string
  session_id?: string
  enable_thinking?: boolean
  file_paths?: string[]
  file_ids?: string[]
}

export interface ExecutionResponse {
  output: string
  execution_path: string
  request_id: string
  agent_id: string
  session_id: string
  step_count: number
}

/* ─── Preview / Dry-run types ─── */

export interface PreviewRequest {
  input?: string
  enable_thinking?: boolean
}

export interface ToolPreview {
  name: string
  type: 'skill' | 'mcp' | 'builtin' | 'workflow'
  description: string
  source: string
  input_schema: Record<string, unknown>
}

export interface PreviewResponse {
  agent_id: string
  agent_name: string
  model: string
  system_prompt: string
  messages: { role: string; content: string }[]
  tools: ToolPreview[]
  tool_summary: {
    total: number
    skill: number
    mcp: number
    builtin: number
    workflow: number
  }
}

/* ─── SSE structured event types ─── */

/** LLM native reasoning (e.g. Claude extended thinking) — consolidated */
export interface ThinkingEvent {
  type: 'thinking'
  content: string
}

/** Streaming delta of LLM reasoning (incremental) */
export interface ThinkingDeltaEvent {
  type: 'thinking_delta'
  content: string
}

/** AI decided to call a tool */
export interface ToolCallEvent {
  type: 'tool_call'
  tool_name: string
  args: Record<string, unknown>
  /** LLM-assigned call id — links this tool_call to its later tool_result.
   *  Required for pairing parallel same-name calls (e.g. two kb_search). */
  id: string
}

/** AI started generating a tool call (args not yet complete) */
export interface ToolCallStartEvent {
  type: 'tool_call_start'
  /** Tool name if already streamed in the first chunk (may be empty —
   *  some models deliver the name in a later chunk; the subsequent
   *  tool_call event always carries the full name). */
  tool_name: string
}

/** Tool returned a result */
export interface ToolResultEvent {
  type: 'tool_result'
  tool_name: string
  content: string
  status?: 'success' | 'error'
  /** The LLM-assigned id linking this result to its tool_call. Empty for
   *  results produced before this field existed (fall back to tool_name). */
  tool_call_id: string
}

/** Incremental text delta streamed from the LLM */
export interface TextDeltaEvent {
  type: 'text_delta'
  content: string
}

/** Complete text block from the AI — consolidated */
export interface TextEvent {
  type: 'text'
  content: string
}

/** Error during execution */
export interface ErrorEvent {
  type: 'error'
  content: string
  source?: 'llm' | 'tool' | 'graph'
}

/** Agent paused via interrupt, awaiting user answer.
 *  kind discriminates clarification (ask_clarification) vs workflow
 *  confirmation (confirm_workflow); the studio interrupt handler keys off
 *  the tool_call's toolName rather than kind, but the fields are kept for
 *  parity with the backend InterruptEvent. */
export interface InterruptEvent {
  type: 'interrupt'
  kind?: 'clarification' | 'workflow_confirmation'
  // clarification fields (ask_clarification)
  question: string
  clarification_type: string
  context?: string | null
  options?: string[] | null
  /** Structured form fields — when non-empty, host renders a multi-field form
   *  instead of a single question card (each dict mirrors ClarificationField). */
  fields?: Array<Record<string, unknown>> | null
  // workflow_confirmation fields (confirm_workflow)
  workflow_name?: string
  workflow_description?: string
  input_preview?: Record<string, unknown>
  interrupt_id: string
}

/** Execution finished */
export interface StreamDoneEvent {
  done: true
  request_id: string
  session_id: string
  /** Token usage for the completed run (backend includes this on the done event). */
  usage?: TokenUsage
}

/** Union of all SSE event types */
export type StreamEvent =
  | ThinkingEvent
  | ThinkingDeltaEvent
  | ToolCallStartEvent
  | ToolCallEvent
  | ToolResultEvent
  | TextDeltaEvent
  | TextEvent
  | ErrorEvent
  | InterruptEvent
  | StreamDoneEvent

/* ─── API methods ─── */

export const agentApi = {
  /**
   * List agents with optional pagination, name search, status filter.
   * GET /api/v1/agents
   */
  async list(params: AgentListParams = {}): Promise<AgentListResponse> {
    const res = await apiClient.get<AgentListResponse>('/api/v1/agents', {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        ...(params.name ? { name: params.name } : {}),
        ...(params.status ? { status: params.status } : {}),
      },
    })
    return res.data
  },

  /**
   * Get a single agent by ID.
   * GET /api/v1/agents/{id}
   */
  async get(agentId: string): Promise<Agent> {
    const res = await apiClient.get<Agent>(`/api/v1/agents/${encodeURIComponent(agentId)}`)
    return res.data
  },

  /**
   * Create a new agent.
   * POST /api/v1/agents — returns 201 on success.
   */
  async create(input: AgentCreateInput): Promise<Agent> {
    const res = await apiClient.post<Agent>('/api/v1/agents', input)
    return res.data
  },

  /**
   * Update an agent (full PUT).
   * Published agents are immutable — returns 409 if agent is published.
   * PUT /api/v1/agents/{id}
   */
  async update(agentId: string, input: AgentUpdateInput): Promise<Agent> {
    const res = await apiClient.put<Agent>(`/api/v1/agents/${encodeURIComponent(agentId)}`, input)
    return res.data
  },

  /**
   * Upload an agent avatar image (already cropped to PNG client-side).
   * POST /api/v1/agents/{id}/avatar — multipart 'file'. Returns the avatar URL.
   */
  async uploadAvatar(agentId: string, file: Blob): Promise<string> {
    const form = new FormData()
    form.append('file', file, 'avatar.png')
    const res = await apiClient.post<{ avatar: string }>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/avatar`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    return res.data.avatar
  },

  /**
   * Delete an agent. Returns 204 No Content on success.
   * DELETE /api/v1/agents/{id}
   */
  async remove(agentId: string): Promise<void> {
    await apiClient.delete(`/api/v1/agents/${encodeURIComponent(agentId)}`)
  },

  /**
   * Update only the model configuration (PATCH).
   * PATCH /api/v1/agents/{id}/model-config
   */
  async updateModelConfig(agentId: string, input: ModelConfigUpdateInput): Promise<Agent> {
    const res = await apiClient.patch<Agent>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/model-config`,
      input,
    )
    return res.data
  },

  /**
   * Publish an agent (draft/archived → published).
   * POST /api/v1/agents/{id}/publish
   */
  async publish(agentId: string): Promise<Agent> {
    const res = await apiClient.post<Agent>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/publish`,
    )
    return res.data
  },

  /**
   * Archive an agent (published → archived).
   * POST /api/v1/agents/{id}/archive
   */
  async archive(agentId: string): Promise<Agent> {
    const res = await apiClient.post<Agent>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/archive`,
    )
    return res.data
  },

  /**
   * Duplicate an agent. Creates a new draft agent with copied config.
   * POST /api/v1/agents/{id}/duplicate — returns 201.
   */
  async duplicate(agentId: string): Promise<Agent> {
    const res = await apiClient.post<Agent>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/duplicate`,
    )
    return res.data
  },

  /**
   * Invoke an agent synchronously (non-streaming).
   * POST /api/v1/agents/{id}/invoke
   */
  async invoke(agentId: string, body: ExecutionRequest): Promise<ExecutionResponse> {
    const res = await apiClient.post<ExecutionResponse>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/invoke`,
      body,
    )
    return res.data
  },

  /**
   * Preview assembled prompt & tools without invoking LLM (dry-run).
   * POST /api/v1/agents/{id}/preview
   */
  async preview(agentId: string, body: PreviewRequest = {}): Promise<PreviewResponse> {
    const res = await apiClient.post<PreviewResponse>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/preview`,
      body,
    )
    return res.data
  },

  /**
   * Stream an agent execution via SSE (POST + ReadableStream).
   *
   * Uses raw fetch instead of axios because axios does not support
   * streaming response bodies. Returns the raw Response so the caller
   * can read the body as a ReadableStream and parse SSE events.
   */
  async stream(agentId: string, body: ExecutionRequest, signal?: AbortSignal): Promise<Response> {
    const url = `${ENV.API_BASE_URL}/api/v1/agents/${encodeURIComponent(agentId)}/stream`

    return this._streamWithRetry(url, body, signal)
  },

  /**
   * Resume an interrupted agent execution (after ask_clarification) via SSE.
   * Same streaming contract as stream(): returns the raw Response so the caller
   * consumes SSE. The user's answer is fed back so the agent continues instead
   * of re-asking the same question. POST /api/v1/agents/{id}/resume
   */
  async resume(
    agentId: string,
    body: { session_id: string; answer: string; enable_thinking?: boolean },
    signal?: AbortSignal,
  ): Promise<Response> {
    const url = `${ENV.API_BASE_URL}/api/v1/agents/${encodeURIComponent(agentId)}/resume`
    return this._streamWithRetry(url, body, signal)
  },

  /**
   * Dismiss the pending clarification card (ask_clarification) without
   * answering — for when the user doesn't want to fill the card and prefers
   * to re-enter freely (plain text or files). The backend persists a
   * synthetic tool_result so the card stays dismissed after refresh; the
   * next message goes through the normal stream path (fresh turn — the
   * suspended interrupt is dropped on new input).
   * POST /api/v1/agents/{id}/interrupt/dismiss
   */
  async dismissInterrupt(agentId: string, sessionId: string): Promise<boolean> {
    const res = await apiClient.post<{ dismissed: boolean }>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/interrupt/dismiss`,
      { session_id: sessionId },
    )
    return res.data.dismissed
  },

  /**
   * Stop the agent's latest in-flight streaming run (mid-stream abort).
   *
   * Server-side task.cancel() immediately interrupts the in-flight LLM call /
   * tool execution; the partial reply is NOT persisted and the next message
   * starts from a clean checkpoint. No request_id needed — the backend targets
   * the caller's latest active run on this agent.
   *
   * Fire-and-forget: 409 (run already finished) and network errors are
   * swallowed — the local abort is the user-visible path.
   */
  async stop(agentId: string): Promise<void> {
    const url = `${ENV.API_BASE_URL}/api/v1/agents/${encodeURIComponent(agentId)}/stop`
    try {
      // 复用 _streamWithRetry 的 401 刷新重试：token 过期时先静默刷新再
      // 重试，否则带过期 token 的 stop 401 会被静默吞掉、服务端运行
      // 无法中止。body 传空对象（stop 无需 payload）。
      await this._streamWithRetry(url, {})
    } catch {
      // 网络错误不打断本地停止流程
    }
  },

  /**
   * Internal: POST to SSE stream URL with fetch().  If the response is 401
   * TOKEN_EXPIRED, attempt a silent refresh once and retry.
   *
   * NOTE: standard Axios interceptors do NOT apply to fetch(), so this
   * method duplicates the minimal refresh logic seen in api-client.ts.
   */
  async _streamWithRetry(
    url: string,
    body: Record<string, unknown>,
    signal?: AbortSignal,
    _retried = false,
  ): Promise<Response> {
    const accessToken = useAuthStore.getState().accessToken

    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    })

    // 401 + token expired → refresh once and retry
    if (res.status === 401 && !_retried) {
      const errBody: { error?: { code?: string } } = {}
      try { Object.assign(errBody, await res.clone().json()) } catch { /* ignore parse errors */ }

      if (errBody.error?.code === 'TOKEN_EXPIRED') {
        const newToken = await this._refreshToken()
        if (newToken) {
          useAuthStore.getState().setAccessToken(newToken)
          return this._streamWithRetry(url, body, signal, true)
        }
        // Refresh failed → redirect to login
        useAuthStore.getState().clearAuth()
        if (window.location.pathname !== '/login') {
          const redirect = encodeURIComponent(window.location.pathname + window.location.search)
          window.location.href = `/login?redirect=${redirect}`
        }
      }
    }

    return res
  },

  /**
   * Attempt to refresh the JWT access token using the stored refresh token.
   * Returns the new access token or null on failure.
   */
  async _refreshToken(): Promise<string | null> {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
    if (!refreshToken) return null

    try {
      const res = await authApi.refresh(refreshToken)
      const { access_token, refresh_token } = res.data
      localStorage.setItem(REFRESH_TOKEN_KEY, refresh_token)
      return access_token
    } catch {
      return null
    }
  },

}

/* ─── Query key factory ─── */

export const agentKeys = {
  all: ['agents'] as const,
  lists: () => [...agentKeys.all, 'list'] as const,
  list: (params: AgentListParams) => [...agentKeys.lists(), params] as const,
  details: () => [...agentKeys.all, 'detail'] as const,
  detail: (id: string) => [...agentKeys.details(), id] as const,
}

/* ─── Error helpers ─── */

export function isAgentError(
  err: unknown,
): err is NormalizedApiError {
  return (
    typeof err === 'object' &&
    err !== null &&
    'message' in err &&
    typeof (err as { message: unknown }).message === 'string'
  )
}

/**
 * MCP Connection API service — wraps backend /mcp/connections endpoints.
 *
 * Uses the shared apiClient instance (auto auth header + 401 refresh).
 * Response fields are snake_case per backend contract.
 */
import { apiClient } from '../lib/api-client'

/* ─── Types (snake_case, matches backend schemas) ─── */

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error'
export type McpAuthType = 'none' | 'api_key' | 'bearer_token' | 'basic'

export interface McpConnection {
  id: string
  name: string
  description: string
  category_id: string
  url: string
  protocol: string
  auth_type: McpAuthType
  auth_config: Record<string, string>
  timeout: number
  default_params: Record<string, unknown>
  status: ConnectionStatus
  status_message: string
  last_connected_at: string
  tool_count: number
  created_at: string
  updated_at: string
}

export interface McpConnectionCreateInput {
  name: string
  description?: string
  category_id?: string
  url: string
  protocol?: string
  auth_type?: McpAuthType
  auth_config?: Record<string, string>
  timeout?: number
  default_params?: Record<string, unknown>
}

export type McpConnectionUpdateInput = McpConnectionCreateInput

export interface McpConnectionListParams {
  page?: number
  page_size?: number
  name?: string
  status?: ConnectionStatus
  category_id?: string
}

export interface McpConnectionListResponse {
  items: McpConnection[]
  total: number
  page: number
  page_size: number
}

export interface McpTestResult {
  success: boolean
  server_info: Record<string, string>
  tool_count: number
  error: string
}

export interface McpDiscoverResult {
  connection_id: string
  discovered: number
  created: number
  updated: number
  deactivated: number
  tools: string[]
  error: string
}

/* ─── API methods ─── */

export const mcpApi = {
  /**
   * List MCP connections with optional pagination, name search, status filter.
   * GET /api/v1/mcp/connections
   */
  async list(params: McpConnectionListParams = {}): Promise<McpConnectionListResponse> {
    const res = await apiClient.get<McpConnectionListResponse>('/api/v1/mcp/connections', {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        ...(params.name ? { name: params.name } : {}),
        ...(params.status ? { status: params.status } : {}),
        ...(params.category_id ? { category_id: params.category_id } : {}),
      },
    })
    return res.data
  },

  /**
   * Get a single MCP connection by ID.
   * GET /api/v1/mcp/connections/{id}
   */
  async get(connectionId: string): Promise<McpConnection> {
    const res = await apiClient.get<McpConnection>(
      `/api/v1/mcp/connections/${encodeURIComponent(connectionId)}`,
    )
    return res.data
  },

  /**
   * Create a new MCP connection.
   * POST /api/v1/mcp/connections — returns 201 on success.
   */
  async create(input: McpConnectionCreateInput): Promise<McpConnection> {
    const res = await apiClient.post<McpConnection>('/api/v1/mcp/connections', input)
    return res.data
  },

  /**
   * Update an MCP connection (full PUT).
   * PUT /api/v1/mcp/connections/{id}
   */
  async update(connectionId: string, input: McpConnectionUpdateInput): Promise<McpConnection> {
    const res = await apiClient.put<McpConnection>(
      `/api/v1/mcp/connections/${encodeURIComponent(connectionId)}`,
      input,
    )
    return res.data
  },

  /**
   * Delete an MCP connection and cascade-remove its MCP tools.
   * DELETE /api/v1/mcp/connections/{id} — returns 204.
   */
  async remove(connectionId: string): Promise<void> {
    await apiClient.delete(`/api/v1/mcp/connections/${encodeURIComponent(connectionId)}`)
  },

  /**
   * Test an MCP connection.
   * POST /api/v1/mcp/connections/{id}/test
   */
  async test(connectionId: string): Promise<McpTestResult> {
    const res = await apiClient.post<McpTestResult>(
      `/api/v1/mcp/connections/${encodeURIComponent(connectionId)}/test`,
    )
    return res.data
  },

  /**
   * Discover tools from an MCP server.
   * POST /api/v1/mcp/connections/{id}/discover
   */
  async discover(connectionId: string): Promise<McpDiscoverResult> {
    const res = await apiClient.post<McpDiscoverResult>(
      `/api/v1/mcp/connections/${encodeURIComponent(connectionId)}/discover`,
    )
    return res.data
  },
}

/* ─── Query key factory ─── */

export const mcpKeys = {
  all: ['mcp-connections'] as const,
  lists: () => [...mcpKeys.all, 'list'] as const,
  list: (params: McpConnectionListParams) => [...mcpKeys.lists(), params] as const,
  details: () => [...mcpKeys.all, 'detail'] as const,
  detail: (id: string) => [...mcpKeys.details(), id] as const,
}

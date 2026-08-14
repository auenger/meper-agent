/**
 * MCP Category API service — wraps backend /mcp/categories endpoints.
 *
 * A category groups related MCP connections so that, when binding tools
 * to an agent, users can select an entire group or pick individual
 * connections within it.
 *
 * Uses the shared apiClient instance (auto auth header + 401 refresh).
 * Response fields are snake_case per backend contract.
 */
import { apiClient } from '../lib/api-client'

/* ─── Types (snake_case, matches backend schemas) ─── */

export interface McpCategory {
  id: string
  name: string
  description: string
  sort: number
  created_at: string
  updated_at: string
}

export interface McpCategoryCreateInput {
  name: string
  description?: string
  sort?: number
}

export type McpCategoryUpdateInput = McpCategoryCreateInput

export interface McpCategoryListResponse {
  items: McpCategory[]
  total: number
}

/* ─── API methods ─── */

export const mcpCategoryApi = {
  /**
   * List all MCP categories (ordered by sort asc).
   * GET /api/v1/mcp/categories
   */
  async list(): Promise<McpCategoryListResponse> {
    const res = await apiClient.get<McpCategoryListResponse>('/api/v1/mcp/categories')
    return res.data
  },

  /**
   * Get a single MCP category by ID.
   * GET /api/v1/mcp/categories/{id}
   */
  async get(categoryId: string): Promise<McpCategory> {
    const res = await apiClient.get<McpCategory>(
      `/api/v1/mcp/categories/${encodeURIComponent(categoryId)}`,
    )
    return res.data
  },

  /**
   * Create a new MCP category.
   * POST /api/v1/mcp/categories — returns 201 on success.
   */
  async create(input: McpCategoryCreateInput): Promise<McpCategory> {
    const res = await apiClient.post<McpCategory>('/api/v1/mcp/categories', input)
    return res.data
  },

  /**
   * Update an MCP category (full PUT).
   * PUT /api/v1/mcp/categories/{id}
   */
  async update(categoryId: string, input: McpCategoryUpdateInput): Promise<McpCategory> {
    const res = await apiClient.put<McpCategory>(
      `/api/v1/mcp/categories/${encodeURIComponent(categoryId)}`,
      input,
    )
    return res.data
  },

  /**
   * Delete an MCP category. Refuses if any connection still references it.
   * DELETE /api/v1/mcp/categories/{id} — returns 204.
   */
  async remove(categoryId: string): Promise<void> {
    await apiClient.delete(`/api/v1/mcp/categories/${encodeURIComponent(categoryId)}`)
  },
}

/* ─── Query key factory ─── */

export const mcpCategoryKeys = {
  all: ['mcp-categories'] as const,
  lists: () => [...mcpCategoryKeys.all, 'list'] as const,
  details: () => [...mcpCategoryKeys.all, 'detail'] as const,
  detail: (id: string) => [...mcpCategoryKeys.details(), id] as const,
}

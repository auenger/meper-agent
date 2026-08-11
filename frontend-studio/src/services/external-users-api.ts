/**
 * External Users service — wraps backend /mcp-tokens endpoints.
 *
 * 管理终端用户的通用 token（meper_xxx）+ 各 MCP 绑定凭证。
 * 详见 docs/planning-artifacts/mcp-credential-broker-design.md。
 */
import { apiClient } from '../lib/api-client'

/* ─── Types (snake_case, matches backend schemas) ─── */

export type McpTokenStatus = 'active' | 'disabled'
export type McpCredentialType = 'token' | 'password'
export type McpAuthType = 'none' | 'api_key' | 'bearer_token' | 'basic'

export interface McpBindingMasked {
  credential_type: McpCredentialType
  auth_type: McpAuthType
  token?: string | null
  username?: string | null
  password?: string | null
  header_name?: string | null
}

export interface McpTokenRecord {
  id: string
  name: string
  token: string
  api_key_id: string
  status: McpTokenStatus
  mcp_bindings: Record<string, McpBindingMasked>
  created_at: string
  updated_at: string
  created_by: string
}

export interface McpTokenCreated extends McpTokenRecord {
  token_plaintext: string
}

export interface McpTokenRotated {
  id: string
  token_plaintext: string
}

export interface McpBindingInput {
  credential_type: McpCredentialType
  auth_type?: McpAuthType
  token?: string
  username?: string
  password?: string
  header_name?: string
}

export interface McpTokenCreateInput {
  name: string
  api_key_id?: string
  mcp_bindings?: Record<string, McpBindingInput>
}

export interface McpTokenUpdateInput {
  name?: string
  status?: McpTokenStatus
  mcp_bindings?: Record<string, McpBindingInput>
}

export interface McpTokenListResponse {
  items: McpTokenRecord[]
  total: number
}

/* ─── API methods ─── */

export const externalUsersApi = {
  async list(): Promise<McpTokenListResponse> {
    const res = await apiClient.get<McpTokenListResponse>('/api/v1/mcp-tokens', {
      params: { page_size: 100 },
    })
    return res.data
  },

  async create(input: McpTokenCreateInput): Promise<McpTokenCreated> {
    const res = await apiClient.post<McpTokenCreated>('/api/v1/mcp-tokens', input)
    return res.data
  },

  async update(id: string, input: McpTokenUpdateInput): Promise<McpTokenRecord> {
    const res = await apiClient.put<McpTokenRecord>(`/api/v1/mcp-tokens/${encodeURIComponent(id)}`, input)
    return res.data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/api/v1/mcp-tokens/${encodeURIComponent(id)}`)
  },

  async rotate(id: string): Promise<McpTokenRotated> {
    const res = await apiClient.post<McpTokenRotated>(`/api/v1/mcp-tokens/${encodeURIComponent(id)}/rotate`)
    return res.data
  },

  async reveal(id: string): Promise<{ id: string; token: string }> {
    const res = await apiClient.get<{ id: string; token: string }>(`/api/v1/mcp-tokens/${encodeURIComponent(id)}/reveal`)
    return res.data
  },
}

/* ─── Query key factory ─── */

export const externalUsersKeys = {
  all: ['external-users'] as const,
  list: () => [...externalUsersKeys.all, 'list'] as const,
}

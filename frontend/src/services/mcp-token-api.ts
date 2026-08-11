/**
 * MCP Token management service — wraps backend /mcp-tokens endpoints.
 *
 * 终端用户的通用 token + 各 MCP 绑定凭证。admin 创建后把通用 token 下发给
 * 终端用户，用户用它通过 client 调 agent；agent 调 MCP 时平台兑换绑定凭证。
 *
 * 详见 docs/planning-artifacts/mcp-credential-broker-design.md。
 */
import { apiClient } from './api-client'

/* ─── Types (snake_case, matches backend schemas) ─── */

export type McpTokenStatus = 'active' | 'disabled'

/** 单个 MCP 绑定的凭证形态（脱敏后的响应）。 */
export type McpCredentialType = 'token' | 'password'
export type McpAuthType = 'none' | 'api_key' | 'bearer_token' | 'basic'

export interface McpBindingMasked {
  credential_type: McpCredentialType
  auth_type: McpAuthType
  /** token 型：脱敏后的 token；账密型通常为空 */
  token?: string | null
  /** 账密型的用户名（通常不脱敏） */
  username?: string | null
  /** 账密型：脱敏后的密码 */
  password?: string | null
  /** api_key 型的自定义 header 名 */
  header_name?: string | null
}

export interface McpToken {
  id: string
  name: string
  /** 脱敏后的通用 token（如 mepe_****abcd） */
  token: string
  api_key_id: string
  status: McpTokenStatus
  /** key = mcp_connection_id，value = 脱敏绑定 */
  mcp_bindings: Record<string, McpBindingMasked>
  created_at: string
  updated_at: string
  created_by: string
}

/** 创建/轮换时一次性返回的明文通用 token。 */
export interface McpTokenCreated extends Omit<McpToken, 'updated_at'> {
  /** 明文通用 token，仅创建/轮换时返回，请妥善保存 */
  token_plaintext: string
}

export interface McpTokenRotated {
  id: string
  token_plaintext: string
}

/** 创建/更新时单个绑定的输入（明文凭证）。 */
export interface McpBindingInput {
  credential_type: McpCredentialType
  auth_type?: McpAuthType
  /** token 型必填 */
  token?: string
  /** 账密型必填 */
  username?: string
  password?: string
  /** api_key 型可选 */
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
  /** 传入则全量替换；不传 = 不改 */
  mcp_bindings?: Record<string, McpBindingInput>
}

export interface McpTokenListParams {
  page?: number
  page_size?: number
  name?: string
  status?: McpTokenStatus
}

export interface McpTokenListResponse {
  items: McpToken[]
  total: number
}

/* ─── API ─── */

export const mcpTokenApi = {
  async list(params: McpTokenListParams = {}): Promise<McpTokenListResponse> {
    const res = await apiClient.get<McpTokenListResponse>('/api/v1/mcp-tokens', {
      params: {
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
        ...(params.name ? { name: params.name } : {}),
        ...(params.status ? { status: params.status } : {}),
      },
    })
    return res.data
  },

  async get(id: string): Promise<McpToken> {
    const res = await apiClient.get<McpToken>(`/api/v1/mcp-tokens/${encodeURIComponent(id)}`)
    return res.data
  },

  async create(input: McpTokenCreateInput): Promise<McpTokenCreated> {
    const res = await apiClient.post<McpTokenCreated>('/api/v1/mcp-tokens', input)
    return res.data
  },

  async update(id: string, input: McpTokenUpdateInput): Promise<McpToken> {
    const res = await apiClient.put<McpToken>(`/api/v1/mcp-tokens/${encodeURIComponent(id)}`, input)
    return res.data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/api/v1/mcp-tokens/${encodeURIComponent(id)}`)
  },

  async rotate(id: string): Promise<McpTokenRotated> {
    const res = await apiClient.post<McpTokenRotated>(
      `/api/v1/mcp-tokens/${encodeURIComponent(id)}/rotate`,
    )
    return res.data
  },

  async reveal(id: string): Promise<{ id: string; token: string }> {
    const res = await apiClient.get<{ id: string; token: string }>(
      `/api/v1/mcp-tokens/${encodeURIComponent(id)}/reveal`,
    )
    return res.data
  },
}

/* ─── Query key factory ─── */

export const mcpTokenKeys = {
  all: ['mcp-tokens'] as const,
  lists: () => [...mcpTokenKeys.all, 'list'] as const,
  list: (params: McpTokenListParams) => [...mcpTokenKeys.lists(), params] as const,
  details: () => [...mcpTokenKeys.all, 'detail'] as const,
  detail: (id: string) => [...mcpTokenKeys.details(), id] as const,
}

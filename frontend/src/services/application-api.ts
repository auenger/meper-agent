/**
 * Application API — 应用（授权边界）管理。
 *
 * 应用 = 外部系统抽象，admin 创建并绑定 MCP 资源，用户对应用授权。
 * 对接后端 /api/v1/applications。
 */
import { apiClient } from './api-client'

/* ─── Types ─── */

/** 账密验证配置 */
export interface AppLoginConfig {
  login_url?: string
  method?: string
  username_field?: string
  password_field?: string
  token_jsonpath?: string
  session_ttl?: number
}

export interface Application {
  id: string
  name: string
  description: string
  mcp_connection_ids: string[]
  login_config: AppLoginConfig | Record<string, never>
  created_at: string
  updated_at: string
}

export interface ApplicationCreateInput {
  name: string
  description?: string
  mcp_connection_ids?: string[]
  login_config?: AppLoginConfig
}

export type ApplicationUpdateInput = ApplicationCreateInput

export interface ApplicationListResponse {
  items: Application[]
  total: number
}

/* ─── API methods ─── */

export const applicationApi = {
  async list(): Promise<ApplicationListResponse> {
    const res = await apiClient.get<ApplicationListResponse>('/api/v1/applications')
    return res.data
  },

  async get(id: string): Promise<Application> {
    const res = await apiClient.get<Application>(
      `/api/v1/applications/${encodeURIComponent(id)}`,
    )
    return res.data
  },

  async create(input: ApplicationCreateInput): Promise<Application> {
    const res = await apiClient.post<Application>('/api/v1/applications', input)
    return res.data
  },

  async update(id: string, input: ApplicationUpdateInput): Promise<Application> {
    const res = await apiClient.put<Application>(
      `/api/v1/applications/${encodeURIComponent(id)}`,
      input,
    )
    return res.data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/api/v1/applications/${encodeURIComponent(id)}`)
  },
}

/* ─── Query keys ─── */

export const applicationKeys = {
  all: ['applications'] as const,
  lists: () => [...applicationKeys.all, 'list'] as const,
  details: () => [...applicationKeys.all, 'detail'] as const,
  detail: (id: string) => [...applicationKeys.details(), id] as const,
}

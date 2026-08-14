/**
 * My app authorizations API — 用户自服务应用授权。
 *
 * 对接后端 /api/v1/my-app-authorizations 端点。
 */
import { apiClient } from '../lib/api-client'

/* ─── Types ─── */

export interface AppBinding {
  app_id: string
  app_name: string
  username: string
  password_masked: string
  bound: boolean
}

export interface MyAuthorizationsResponse {
  platform_user_id: string
  bindings: AppBinding[]
  updated_at: string
}

export interface AvailableApp {
  id: string
  name: string
  description: string
  mcp_count: number
}

export interface AvailableAppsResponse {
  items: AvailableApp[]
}

export interface AuthorizeAppInput {
  username: string
  password: string
}

/* ─── API methods ─── */

export const myAppAuthorizationsApi = {
  /** 查看当前用户的应用授权列表（凭证脱敏） */
  async get(): Promise<MyAuthorizationsResponse> {
    const res = await apiClient.get<MyAuthorizationsResponse>('/api/v1/my-app-authorizations')
    return res.data
  },

  /** 查看可授权的应用列表 */
  async availableApps(): Promise<AvailableAppsResponse> {
    const res = await apiClient.get<AvailableAppsResponse>('/api/v1/my-app-authorizations/available-apps')
    return res.data
  },

  /** 授权应用（后端调 login_url 验证账密 + 建立身份映射） */
  async authorize(appId: string, input: AuthorizeAppInput): Promise<MyAuthorizationsResponse> {
    const res = await apiClient.put<MyAuthorizationsResponse>(
      `/api/v1/my-app-authorizations/${encodeURIComponent(appId)}`,
      input,
    )
    return res.data
  },

  /** 取消授权 */
  async revoke(appId: string): Promise<MyAuthorizationsResponse> {
    const res = await apiClient.delete<MyAuthorizationsResponse>(
      `/api/v1/my-app-authorizations/${encodeURIComponent(appId)}`,
    )
    return res.data
  },
}

/* ─── Query keys ─── */

export const myAppAuthorizationKeys = {
  all: ['my-app-authorizations'] as const,
  detail: () => [...myAppAuthorizationKeys.all, 'detail'] as const,
  availableApps: () => [...myAppAuthorizationKeys.all, 'available-apps'] as const,
}

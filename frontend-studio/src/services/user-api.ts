/**
 * User management API service — admin CRUD for users.
 */
import { apiClient } from '../lib/api-client'

export interface User {
  id: string
  username: string
  email: string
  role: string
  status: 'active' | 'disabled'
  created_at: string
  updated_at: string
  last_login_at: string | null
  permissions: string[]
}

export interface UserListResponse {
  items: User[]
  total: number
  page: number
  page_size: number
}

export interface CreateUserPayload {
  username: string
  email: string
  password: string
  role: string
}

export interface UpdateUserPayload {
  role?: string
  status?: 'active' | 'disabled'
}

export interface ResetPasswordPayload {
  new_password: string
}

export const userApi = {
  list: (params?: {
    page?: number
    page_size?: number
    username?: string
    role?: string
    status?: string
    /** 用户名/邮箱联合子串搜索（后端 $or 匹配） */
    search?: string
  }) => apiClient.get<UserListResponse>('/api/v1/users', { params }),

  create: (data: CreateUserPayload) =>
    apiClient.post<User>('/api/v1/users', data),

  update: (userId: string, data: UpdateUserPayload) =>
    apiClient.patch<User>(`/api/v1/users/${userId}`, data),

  delete: (userId: string) =>
    apiClient.delete(`/api/v1/users/${userId}`),

  resetPassword: (userId: string, data: ResetPasswordPayload) =>
    apiClient.post(`/api/v1/users/${userId}/reset-password`, data),
}

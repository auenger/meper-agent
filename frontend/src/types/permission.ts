/**
 * Permission type definitions — aligned with backend ROLE_PERMISSIONS.
 */

export type RoleType = 'system' | 'custom'

export interface Role {
  id: string
  name: string
  display_name: string
  description: string
  role_type: RoleType
  permissions: string[]
  created_at: string
  updated_at: string
}

/**
 * All available permission keys, grouped by module (for the checkbox UI).
 * Keys must match backend ALL_PERMISSION_KEYS.
 */
export const PERMISSION_GROUPS: Record<string, string[]> = {
  '用户管理': ['user:read', 'user:write'],
  'Agent 管理': ['agent:read', 'agent:write', 'agent:invoke'],
  '工作流': ['workflow:read', 'workflow:write'],
  '工具': ['tool:read', 'tool:write'],
  '应用': ['application:read', 'application:write'],
  'MCP': ['mcp:read', 'mcp:write'],
  'Skill': ['skill:read', 'skill:write'],
  '任务': ['task:read', 'task:write', 'task:invoke'],
  '知识库': ['knowledge:read', 'knowledge:write'],
  '执行日志': ['execution:read:all', 'execution:read:own'],
  '系统': ['apikey:manage', 'settings:manage', 'model:read', 'model:write'],
}

/** Flat list of all permission keys */
export const ALL_PERMISSIONS: string[] = Object.values(PERMISSION_GROUPS).flat()

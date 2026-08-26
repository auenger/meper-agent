/**
 * Permission check hook — reads from auth store's user.permissions.
 *
 * 权限驱动 UI 的统一入口：页面显示与操作一律判权限键（如
 * "agent:write"），禁止按角色名判断——角色只是权限的打包，自定义
 * 角色与系统角色在此处等价。
 */
import { useAuthStore } from '../stores/auth-store'

const EMPTY_PERMISSIONS: string[] = []

/**
 * Check if the current user has a specific permission.
 *
 * @param permission - Permission key to check (e.g. "agent:write")
 * @returns true if the user has the permission
 */
export function usePermission(permission: string): boolean {
  const permissions = useAuthStore((s) => s.user?.permissions ?? EMPTY_PERMISSIONS)
  return permissions.includes(permission)
}

/**
 * Check if the current user has any of the specified permissions.
 */
export function useAnyPermission(permissions: string[]): boolean {
  const userPerms = useAuthStore((s) => s.user?.permissions ?? EMPTY_PERMISSIONS)
  return permissions.some((p) => userPerms.includes(p))
}

/**
 * Check if the current user has all of the specified permissions.
 */
export function useAllPermissions(permissions: string[]): boolean {
  const userPerms = useAuthStore((s) => s.user?.permissions ?? EMPTY_PERMISSIONS)
  return permissions.every((p) => userPerms.includes(p))
}

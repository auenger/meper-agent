/**
 * PermissionRoute — 权限守卫（§7.6 权限模型原则）。
 *
 * 导航隐藏 ≠ 路由不可达：权限门控的路由若只靠导航过滤，直接访问 URL
 * （浏览器恢复位置/后退/手输）仍会渲染页面、API 403。本守卫在路由层
 * 校验 user.permissions，无权限重定向到仪表盘。
 *
 * 用法（routes/index.tsx）：
 *   { path: '/roles', element: wrapPermission('user:read', <RolesPage />) }
 */
import type { ReactElement } from 'react'
import { Navigate } from 'react-router-dom'

import { useAuthStore } from '../stores/auth-store'
import { PATHS } from './paths'

export function PermissionRoute({ perm, children }: { perm: string; children: ReactElement }) {
  const permissions = useAuthStore((s) => s.user?.permissions ?? [])

  if (!permissions.includes(perm)) {
    return <Navigate to={PATHS.DASHBOARD} replace />
  }

  return children
}

/**
 * 近实时权限感知 — 定期 / 窗口聚焦时拉取 GET /auth/me，把最新的
 * role 与 permissions 同步进 auth store。
 *
 * 管理员调整角色/权限后，已登录用户无需重登或等 token 过期：导航、
 * 路由守卫、按钮门控（全部订阅 user.permissions）在下次拉取时自动
 * 更新。401（用户被禁用/删除）由 apiClient 拦截器统一走刷新/登出。
 */
import { useEffect } from 'react'
import { useAuthStore } from '../stores/auth-store'
import { authApi } from '../services/auth-api'

/** 轮询间隔：权限变更的感知窗口上限。 */
const SYNC_INTERVAL_MS = 60_000

export function usePermissionSync() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const setUser = useAuthStore((s) => s.setUser)

  useEffect(() => {
    if (!isAuthenticated) return

    let cancelled = false

    const sync = async () => {
      try {
        const res = await authApi.me()
        if (cancelled) return
        const u = res.data
        // 只同步鉴权相关字段，保持 store 的 AuthUser 形状不变。
        if (u?.id) {
          setUser({
            id: u.id,
            username: u.username,
            role: u.role,
            permissions: u.permissions ?? [],
            isSuperAdmin: Boolean(u.is_super_admin),
          })
        }
      } catch {
        // 静默：401 由 apiClient 拦截器处理（刷新失败会 clearAuth），
        // 网络/服务端瞬时错误等下一个周期重试。
      }
    }

    void sync()
    const timer = window.setInterval(sync, SYNC_INTERVAL_MS)
    window.addEventListener('focus', sync)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.removeEventListener('focus', sync)
    }
  }, [isAuthenticated, setUser])
}

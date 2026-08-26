/**
 * AuthInitializer — restores auth state on page refresh.
 *
 * On mount, checks localStorage for a refresh_token; if present, calls the
 * refresh API and either restores the session (setAuth) or clears stale data
 * (clearAuth). While `isInitializing` is true, a full-screen spinner is shown
 * so the Login page never flashes for an already-authenticated user.
 *
 * This closes the gap left by feat-studio-infra: the App previously flipped
 * `isInitializing` to false unconditionally without attempting a refresh, so
 * every page load bounced back to Login even with a valid refresh_token.
 *
 * Mirrors frontend/src/features/auth/auth-initializer.tsx, adapted to the
 * studio's native-Tailwind UI (no antd Spin) and relative import layout.
 */
import { type ReactNode, useEffect } from 'react'
import { Loader2 } from 'lucide-react'

import { REFRESH_TOKEN_KEY, useAuthStore } from '../stores/auth-store'
import { authApi } from '../services/auth-api'
import { usePermissionSync } from '../hooks/use-permission-sync'

export function AuthInitializer({ children }: { children: ReactNode }) {
  const isInitializing = useAuthStore((s) => s.isInitializing)
  const setInitializing = useAuthStore((s) => s.setInitializing)
  const setAuth = useAuthStore((s) => s.setAuth)
  const clearAuth = useAuthStore((s) => s.clearAuth)

  // 登录态存续期间持续同步 /auth/me：角色/权限变更 ≤60s 或窗口聚焦即生效。
  usePermissionSync()

  useEffect(() => {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)

    // No refresh token → nothing to restore; fall straight through to Login.
    if (!refreshToken) {
      setInitializing(false)
      return
    }

    let cancelled = false

    authApi
      .refresh(refreshToken)
      .then((res) => {
        if (cancelled) return
        const { access_token, refresh_token, user } = res.data

        // Backend should always return user on refresh; treat its absence as
        // an invalid session rather than logging in a faceless user.
        if (!user) {
          clearAuth()
          return
        }

        setAuth(access_token, {
          id: user.id,
          username: user.username,
          role: user.role,
          permissions: user.permissions ?? [],
          isSuperAdmin: Boolean(user.is_super_admin),
        })
        localStorage.setItem(REFRESH_TOKEN_KEY, refresh_token)
      })
      .catch(() => {
        if (cancelled) return
        clearAuth()
      })
      .finally(() => {
        if (!cancelled) {
          setInitializing(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [setInitializing, setAuth, clearAuth])

  // Full-screen spinner during the refresh-check window. Avoids a white flash
  // of the Login page for authenticated users. Reads the persisted theme so the
  // splash matches the resolved palette (dark → #09090b, light → slate-50),
  // mirroring how Login.tsx resolves its own theme.
  if (isInitializing) {
    const theme =
      (localStorage.getItem('agentflow_theme') as 'dark' | 'light') || 'dark'
    const isDark = theme === 'dark'
    return (
      <div
        className={`theme-${theme} flex items-center justify-center h-screen ${
          isDark ? 'bg-[#09090b]' : 'bg-slate-50'
        }`}
      >
        <Loader2 className="w-6 h-6 text-indigo-400 animate-spin" />
      </div>
    )
  }

  return <>{children}</>
}

/**
 * AuthInitializer — app startup auth recovery.
 *
 * On mount, checks localStorage for a refresh_token. If found, silently
 * refreshes the access token to restore the session. Shows a fullscreen
 * spinner during initialization to prevent layout flash.
 */
import { useEffect, type ReactNode } from 'react'
import { Spin } from 'antd'
import { useAuthStore } from '../stores/auth-store'
import { authApi } from '../services/auth-api'
import { decodeAccessToken } from '../lib/jwt'
import { usePermissionSync } from '../hooks/use-permission-sync'

export function AuthInitializer({ children }: { children: ReactNode }) {
  const { isInitializing, setInitializing, setAuth, clearAuth } = useAuthStore()

  // 登录态存续期间持续同步 /auth/me：角色/权限变更 ≤60s 或窗口聚焦即生效。
  usePermissionSync()

  useEffect(() => {
    const refreshToken = localStorage.getItem('agentflow_refresh_token')
    if (!refreshToken) {
      setInitializing(false)
      return
    }

    authApi
      .refresh(refreshToken)
      .then((res) => {
        const { access_token, refresh_token, user } = res.data
        if (user) {
          // Use user info from refresh response (includes permissions)
          setAuth(access_token, {
            id: user.id,
            username: user.username,
            role: user.role,
            permissions: user.permissions,
          })
        } else {
          // Fallback: decode JWT (no permissions — nav permission filtering won't work)
          const payload = decodeAccessToken(access_token)
          if (payload) {
            setAuth(access_token, {
              id: payload.sub,
              username: payload.username,
              role: payload.role,
              permissions: [],
            })
          } else {
            clearAuth()
            return
          }
        }
        // refresh token rotation — store the new one
        localStorage.setItem('agentflow_refresh_token', refresh_token)
      })
      .catch(() => {
        clearAuth()
      })
      .finally(() => {
        setInitializing(false)
      })
    // clearAuth/setAuth/setInitializing are stable zustand store setters
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (isInitializing) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-white">
        <Spin size="large" />
      </div>
    )
  }

  return <>{children}</>
}

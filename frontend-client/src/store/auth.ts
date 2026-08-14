import { create } from 'zustand'

import type { AuthUser, TokenResponse } from '../types'

export const REFRESH_TOKEN_KEY = 'meper_client_refresh_token'
export const THEME_KEY = 'meper_client_theme'

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  user: AuthUser | null
  /** apikey 模式下从 /ext/userinfo 获取的终端用户名 */
  extUserName: string
  initialized: boolean
  theme: 'light' | 'dark'
  /** apikey 模式下的身份验证错误类型（401 时设置） */
  authError: string | null
  setSession: (bundle: TokenResponse) => void
  setInitialized: (value: boolean) => void
  setExtUserName: (name: string) => void
  setAuthError: (code: string | null) => void
  clear: () => void
  toggleTheme: () => void
}

const storedTheme = (): 'light' | 'dark' => {
  const value = localStorage.getItem(THEME_KEY)
  if (value === 'light' || value === 'dark') return value
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: null,
  refreshToken: localStorage.getItem(REFRESH_TOKEN_KEY),
  user: null,
  extUserName: '',
  initialized: false,
  theme: storedTheme(),
  authError: null,
  setSession: (bundle) => {
    localStorage.setItem(REFRESH_TOKEN_KEY, bundle.refresh_token)
    set({
      accessToken: bundle.access_token,
      refreshToken: bundle.refresh_token,
      user: bundle.user,
    })
  },
  setInitialized: (value) => set({ initialized: value }),
  setExtUserName: (name) => set({ extUserName: name }),
  setAuthError: (code) => set({ authError: code }),
  clear: () => {
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    set({
      accessToken: null,
      refreshToken: null,
      user: null,
    })
  },
  toggleTheme: () => {
    const next = get().theme === 'dark' ? 'light' : 'dark'
    localStorage.setItem(THEME_KEY, next)
    set({ theme: next })
  },
}))

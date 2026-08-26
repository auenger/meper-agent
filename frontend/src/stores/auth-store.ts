/**
 * Auth store — manages authentication state in memory (Zustand).
 *
 * Design:
 * - accessToken lives only in memory (lost on page close → XSS-safe)
 * - refreshToken lives in localStorage (survives refresh)
 * - isInitializing guards the initial refresh-check window
 */
import { create } from 'zustand'

export const REFRESH_TOKEN_KEY = 'agentflow_refresh_token'

export interface AuthUser {
  id: string
  username: string
  role: string
  permissions: string[]
}

interface AuthState {
  accessToken: string | null
  user: AuthUser | null
  isAuthenticated: boolean
  isInitializing: boolean

  setAuth: (accessToken: string, user: AuthUser) => void
  setAccessToken: (token: string) => void
  setUser: (user: AuthUser) => void
  setUserPermissions: (permissions: string[]) => void
  clearAuth: () => void
  setInitializing: (v: boolean) => void
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  user: null,
  isAuthenticated: false,
  isInitializing: true,

  setAuth: (accessToken, user) => {
    set({ accessToken, user, isAuthenticated: true })
  },

  setAccessToken: (token) => {
    set({ accessToken: token })
  },

  setUser: (user) => {
    set({ user })
  },

  setUserPermissions: (permissions) => {
    set((state) => ({
      user: state.user ? { ...state.user, permissions } : null,
    }))
  },

  clearAuth: () => {
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    set({ accessToken: null, user: null, isAuthenticated: false })
  },

  setInitializing: (v) => {
    set({ isInitializing: v })
  },
}))

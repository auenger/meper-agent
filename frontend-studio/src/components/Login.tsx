/**
 * Login page — AgentForge Studio.
 *
 * Calls POST /auth/login via authApi; on success stores tokens (access_token
 * in the zustand auth-store, refresh_token in localStorage) and flips the App
 * gate. Surfaces backend error codes including ACCOUNT_LOCKED (5 attempts /
 * 15 min lockout), and reads ?reason= (set by api-client's 401 redirect) to
 * explain why an existing session was terminated (e.g. ACCOUNT_DISABLED).
 *
 * Theme-aware: reads the same `agentflow_theme` localStorage key the App shell
 * uses, so the login screen matches the user's chosen dark/light palette. The
 * surface classes below (bg-[#09090b], bg-[#121214], border-[#27272a],
 * text-[#fafafa] …) are the same literal tokens used across the app, which the
 * `.theme-light` override block in index.css transforms automatically —
 * so a single source of truth drives both the shell and this page.
 */
import { useEffect, useState, type FormEvent } from 'react'
import { Lock, User, Loader2, AlertCircle, KeyRound, Sun, Moon } from 'lucide-react'
import { authApi } from '../services/auth-api'
import { REFRESH_TOKEN_KEY, useAuthStore, type AuthUser } from '../stores/auth-store'
import type { NormalizedApiError } from '../lib/api-client'

function isNormalizedError(err: unknown): err is NormalizedApiError {
  return typeof err === 'object' && err !== null && 'message' in err
}

/** Map ?reason= (api-client 401 redirect) to a human message. */
const REASON_MESSAGES: Record<string, string> = {
  ACCOUNT_DISABLED: '账户已被停用，请联系管理员',
  ACCOUNT_LOCKED: '账号已被锁定，请在 15 分钟后重试',
  TOKEN_INVALID: '登录凭证已失效，请重新登录',
  TOKEN_REVOKED: '登录已注销，请重新登录',
}

const THEME_KEY = 'agentflow_theme'

export default function Login() {
  const setAuth = useAuthStore((s) => s.setAuth)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(() => {
    const reason = new URLSearchParams(window.location.search).get('reason')
    if (!reason) return null
    return REASON_MESSAGES[reason] ?? '登录已失效，请重新登录'
  })

  // Strip ?reason= from the URL once consumed so a refresh doesn't re-show
  // a stale message (keep ?redirect= — it's still needed after login).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.has('reason')) {
      params.delete('reason')
      const qs = params.toString()
      window.history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname)
    }
  }, [])
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem(THEME_KEY) as 'dark' | 'light') || 'dark',
  )

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    localStorage.setItem(THEME_KEY, next)
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (loading) return
    setLoading(true)
    setError(null)
    try {
      const res = await authApi.login(username.trim(), password)
      const { access_token, refresh_token, user } = res.data
      localStorage.setItem(REFRESH_TOKEN_KEY, refresh_token)
      const authUser: AuthUser = user
        ? {
            id: user.id,
            username: user.username,
            role: user.role,
            permissions: user.permissions ?? [],
            isSuperAdmin: Boolean(user.is_super_admin),
          }
        : { id: '', username: username.trim(), role: '', permissions: [] }
      setAuth(access_token, authUser)
    } catch (err) {
      const msg = isNormalizedError(err) ? err.message : '登录失败，请检查网络或凭据'
      const code = isNormalizedError(err) ? err.code : undefined
      if (code === 'ACCOUNT_LOCKED') {
        setError('账号已被锁定，请在 15 分钟后重试')
      } else {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }

  const isDark = theme === 'dark'

  return (
    <div
      className={`relative h-screen w-screen flex items-center justify-center overflow-hidden theme-${theme} ${
        isDark ? 'bg-[#09090b] text-[#fafafa]' : 'bg-slate-50 text-slate-800'
      } transition-colors duration-200`}
    >
      {/* ambient: dotted grid (same utility the canvas maps use) */}
      <div className="absolute inset-0 bg-grid-dots opacity-40 pointer-events-none" />
      {/* ambient: brand glow — indigo halo behind the card for depth */}
      <div
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[640px] h-[640px] rounded-full pointer-events-none blur-2xl"
        style={{
          background:
            'radial-gradient(circle at center, rgba(99,102,241,0.18), transparent 65%)',
        }}
      />

      {/* theme switch (mirrors the App shell's sun/moon toggle) */}
      <button
        type="button"
        onClick={toggleTheme}
        aria-label="切换主题"
        className={`absolute top-5 right-5 z-10 w-9 h-9 rounded-lg border flex items-center justify-center transition-colors cursor-pointer ${
          isDark
            ? 'bg-[#18181b] border-[#27272a] text-[#a1a1aa] hover:text-white hover:bg-[#1c1c1f]'
            : 'bg-white border-slate-200 text-slate-500 hover:bg-slate-100 hover:text-slate-900'
        }`}
      >
        {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
      </button>

      <div className="relative w-full max-w-sm mx-4">
        {/* brand — real MEPER Agent mark (AFLogo.png) instead of a letter tile */}
        <div className="flex flex-col items-center mb-7">
          <img
            src="/AFLogo.png"
            alt="MEPER Agent"
            className="w-14 h-14 mb-3 object-contain select-none drop-shadow-[0_4px_12px_rgba(99,102,241,0.35)]"
            draggable={false}
          />
          <h1 className="text-xl font-semibold tracking-tight">MEPER Agent</h1>
          <p className="text-[11px] text-[#71717a] uppercase tracking-widest font-bold mt-1">
            MEPER Agent Studio
          </p>
        </div>

        {/* card */}
        <form
          onSubmit={handleSubmit}
          className="bg-[#121214] border border-[#27272a] rounded-2xl shadow-2xl p-6 space-y-4"
        >
          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-[#71717a]">
              用户名
            </label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#52525b]" />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                placeholder="admin"
                className="w-full bg-[#09090b] border border-[#27272a] rounded-xl pl-9 pr-3 py-2.5 text-sm text-[#fafafa] placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30 transition"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-[11px] font-bold uppercase tracking-wider text-[#71717a]">
              密码
            </label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#52525b]" />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                placeholder="••••••••"
                className="w-full bg-[#09090b] border border-[#27272a] rounded-xl pl-9 pr-3 py-2.5 text-sm text-[#fafafa] placeholder:text-[#52525b] focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30 transition"
              />
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 p-2.5 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-xs animate-fade-in">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span className="leading-relaxed">{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !username.trim() || !password}
            className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl py-2.5 text-sm font-semibold transition shadow-lg shadow-indigo-600/20"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                登录中…
              </>
            ) : (
              <>
                <KeyRound className="w-4 h-4" />
                登录
              </>
            )}
          </button>
        </form>

        <p className="text-center text-[11px] text-[#71717a] mt-4">
          连续 5 次失败将锁定账号 15 分钟
        </p>
      </div>
    </div>
  )
}

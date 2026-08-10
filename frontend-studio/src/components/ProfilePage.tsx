/**
 * ProfilePage — 个人设置页。
 *
 * 后端目前只提供 /auth/change-password 这一个个人可改能力，因此：
 * - 用户信息（用户名 / 角色 / 权限）只读展示，来自 auth-store
 * - 修改密码表单对接 POST /auth/change-password，成功后引导重新登录
 *
 * 后续若后端新增 /users/me（拿 email 等完整资料）或个人资料更新端点，
 * 可在此页面扩展资料编辑区块。
 */
import { useState, type FormEvent } from 'react'
import { User, Shield, KeyRound, Loader2, CheckCircle2, AlertCircle } from 'lucide-react'
import { useMutation } from '@tanstack/react-query'
import { authApi } from '../services/auth-api'
import { REFRESH_TOKEN_KEY, useAuthStore } from '../stores/auth-store'
import { getErrorMessage } from '../lib/api-client'
import { Input } from './ui'

export function ProfilePage({ theme = 'dark' }: { theme?: 'light' | 'dark' }) {
  const authUser = useAuthStore((s) => s.user)
  const clearAuth = useAuthStore((s) => s.clearAuth)

  // 修改密码表单
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const changePassword = useMutation({
    mutationFn: (data: { current_password: string; new_password: string }) =>
      authApi.changePassword(data).then((res) => res.data),
    onSuccess: () => {
      setDone(true)
      setFormError(null)
      // 后端会失效所有 refresh token，3 秒后清除本地登录态跳转登录
      setTimeout(() => {
        clearAuth()
        window.location.href = '/login'
      }, 3000)
    },
    onError: (err: unknown) => setFormError(getErrorMessage(err, '修改密码失败')),
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    if (!currentPassword || !newPassword || !confirmPassword) {
      setFormError('请填写完整的密码信息')
      return
    }
    if (newPassword.length < 8) {
      setFormError('新密码至少 8 位')
      return
    }
    if (newPassword !== confirmPassword) {
      setFormError('两次输入的新密码不一致')
      return
    }
    if (newPassword === currentPassword) {
      setFormError('新密码不能与当前密码相同')
      return
    }
    changePassword.mutate({ current_password: currentPassword, new_password: newPassword })
  }

  const cardCls = theme === 'dark'
    ? 'bg-[#18181b] border-[#27272a]'
    : 'bg-white border-slate-200'
  const labelCls = theme === 'dark' ? 'text-[#71717a]' : 'text-slate-500'
  const valueCls = theme === 'dark' ? 'text-[#fafafa]' : 'text-slate-800'
  const subCls = theme === 'dark' ? 'text-[#a1a1aa]' : 'text-slate-600'
  const headingCls = theme === 'dark' ? 'text-[#fafafa]' : 'text-slate-900'

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* 页面标题 */}
      <div>
        <h1 className={`text-lg font-bold ${headingCls} flex items-center gap-2`}>
          <User className="w-5 h-5 text-indigo-400" />
          个人设置
        </h1>
        <p className={`text-xs ${subCls} mt-1`}>管理你的账户信息与登录密码</p>
      </div>

      {/* 只读：账户信息 */}
      <section className={`rounded-xl border p-5 space-y-4 ${cardCls}`}>
        <h2 className={`text-sm font-bold ${headingCls} flex items-center gap-1.5`}>
          <Shield className="w-4 h-4 text-emerald-400" />
          账户信息
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <InfoField label="用户名" value={authUser?.username ?? '—'} valueCls={valueCls} labelCls={labelCls} />
          <InfoField label="角色" value={authUser?.role ?? '—'} valueCls={valueCls} labelCls={labelCls} />
          <InfoField label="用户 ID" value={authUser?.id ?? '—'} mono valueCls={valueCls} labelCls={labelCls} />
          <InfoField
            label="权限数量"
            value={`${authUser?.permissions?.length ?? 0} 项`}
            valueCls={valueCls}
            labelCls={labelCls}
          />
        </div>
        {authUser?.permissions && authUser.permissions.length > 0 && (
          <div>
            <div className={`text-[11px] ${labelCls} mb-1.5`}>已授权权限</div>
            <div className="flex flex-wrap gap-1.5">
              {authUser.permissions.map((p) => (
                <span
                  key={p}
                  className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                    theme === 'dark' ? 'bg-[#121214] text-[#a1a1aa] border border-[#27272a]' : 'bg-slate-100 text-slate-600 border border-slate-200'
                  }`}
                >
                  {p}
                </span>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* 可改：修改密码 */}
      <section className={`rounded-xl border p-5 space-y-4 ${cardCls}`}>
        <h2 className={`text-sm font-bold ${headingCls} flex items-center gap-1.5`}>
          <KeyRound className="w-4 h-4 text-amber-400" />
          修改密码
        </h2>

        {done ? (
          <div className="flex items-start gap-2 p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5">
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
            <div className="text-xs text-[#d4d4d8]">
              密码已修改成功，即将跳转到登录页重新登录…
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className={`text-xs font-medium ${labelCls}`}>当前密码</label>
              <Input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                placeholder="输入当前密码"
                autoComplete="current-password"
              />
            </div>
            <div className="space-y-1.5">
              <label className={`text-xs font-medium ${labelCls}`}>新密码</label>
              <Input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="至少 8 位"
                autoComplete="new-password"
              />
            </div>
            <div className="space-y-1.5">
              <label className={`text-xs font-medium ${labelCls}`}>确认新密码</label>
              <Input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="再次输入新密码"
                autoComplete="new-password"
              />
            </div>

            {formError && (
              <div className="flex items-center gap-2 p-2.5 rounded-lg border border-rose-500/30 bg-rose-500/5 text-rose-400 text-xs">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                {formError}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => {
                  setCurrentPassword('')
                  setNewPassword('')
                  setConfirmPassword('')
                  setFormError(null)
                }}
                className="inline-flex items-center justify-center h-8 px-3 text-xs font-medium rounded-md border border-[#27272a] bg-[#18181b] text-[#fafafa] hover:border-[#1E5EFF] hover:text-[#1E5EFF] transition-colors cursor-pointer"
              >
                重置
              </button>
              <button
                type="submit"
                disabled={changePassword.isPending || !currentPassword || !newPassword || !confirmPassword}
                className="inline-flex items-center justify-center gap-1.5 h-8 px-4 text-xs font-medium rounded-md bg-[#1E5EFF] border border-[#1E5EFF] text-white hover:bg-[#1a4fd6] transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {changePassword.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                确认修改
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  )
}

function InfoField({
  label,
  value,
  mono,
  valueCls,
  labelCls,
}: {
  label: string
  value: string
  mono?: boolean
  valueCls: string
  labelCls: string
}) {
  return (
    <div>
      <div className={`text-[11px] ${labelCls} mb-1`}>{label}</div>
      <div className={`text-xs ${valueCls} ${mono ? 'font-mono' : ''} truncate`}>{value}</div>
    </div>
  )
}

export default ProfilePage

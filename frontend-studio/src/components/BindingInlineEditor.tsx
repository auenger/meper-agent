/**
 * BindingInlineEditor — MCP 绑定的内联编辑器。
 *
 * 独立组件，用局部 state 管理表单，避免父组件重渲染导致输入框失焦。
 */

import { useState } from 'react'
import { X, Loader2 } from 'lucide-react'
import { Select } from './ui'
import type { McpBindingMasked, McpBindingInput, McpCredentialType, McpAuthType } from '../services/external-users-api'

const AUTH_TYPE_OPTIONS: { label: string; value: McpAuthType }[] = [
  { label: 'Bearer Token', value: 'bearer_token' },
  { label: 'API Key', value: 'api_key' },
  { label: 'Basic', value: 'basic' },
]
const CREDENTIAL_TYPE_OPTIONS: { label: string; value: McpCredentialType }[] = [
  { label: 'Token（直传）', value: 'token' },
  { label: '用户名密码', value: 'password' },
]

const inputCls = "w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-indigo-500 transition"

interface Props {
  mode: 'add' | 'edit'
  connId: string
  existing?: McpBindingMasked
  connections: Array<{ id: string; name: string }>
  usedConnIds: string[]
  saving: boolean
  onSave: (connId: string, binding: McpBindingInput) => void
  onCancel: () => void
}

export function BindingInlineEditor({ mode, connId: initialConnId, existing, connections, usedConnIds, saving, onSave, onCancel }: Props) {
  const [connId, setConnId] = useState(initialConnId)
  const [credentialType, setCredentialType] = useState<McpCredentialType>(existing?.credential_type ?? 'token')
  const [authType, setAuthType] = useState<McpAuthType>(existing?.auth_type ?? 'bearer_token')
  const [token, setToken] = useState('')
  const [username, setUsername] = useState(existing?.username ?? '')
  const [password, setPassword] = useState('')

  const available = connections.filter(c => c.id === initialConnId || !usedConnIds.includes(c.id))

  const handleSave = () => {
    if (!connId) return
    const b: McpBindingInput = { credential_type: credentialType, auth_type: authType }
    if (credentialType === 'token') {
      b.token = token.trim()
    } else {
      b.username = username.trim()
      b.password = password
    }
    onSave(connId, b)
  }

  return (
    <div className="rounded-lg border border-indigo-500/30 bg-[#121214] p-3 space-y-3">
      {mode === 'add' && (
        <Select value={connId || ''} onChange={(v: string) => setConnId(v)}
          placeholder="选择 MCP 连接" options={available.map(c => ({ label: c.name, value: c.id }))} />
      )}
      <div className="grid grid-cols-2 gap-2">
        <Select value={credentialType} onChange={(v) => setCredentialType(v as McpCredentialType)} options={CREDENTIAL_TYPE_OPTIONS} />
        <Select value={authType} onChange={(v) => setAuthType(v as McpAuthType)} options={AUTH_TYPE_OPTIONS} />
      </div>
      {credentialType === 'token' ? (
        <input type="password" value={token} onChange={e => setToken(e.target.value)}
          placeholder={mode === 'edit' ? '新 token（留空 = 不修改）' : '目标 MCP 的 token'} className={inputCls} />
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <input value={username} onChange={e => setUsername(e.target.value)} placeholder="用户名" className={inputCls} />
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
            placeholder={mode === 'edit' ? '新密码（留空=不改）' : '密码'} className={inputCls} />
        </div>
      )}
      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className="px-2 py-1 text-xs text-slate-500 hover:text-slate-300 transition">取消</button>
        <button onClick={handleSave} disabled={saving}
          className="px-3 py-1 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded transition-colors">
          {saving ? '保存中...' : '保存'}
        </button>
      </div>
    </div>
  )
}

/**
 * ExternalUsersPage — 外部用户管理（通用 token + MCP 绑定）。
 *
 * 列表 + 编辑 Modal（Modal 内绑定原地展开编辑，不套子 Modal）。
 */
import { useState, useRef, Fragment } from 'react'
import { BindingInlineEditor } from './BindingInlineEditor'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Users, Plus, Copy, Trash2, Pencil, RefreshCw,
  Link2, Loader2, X,
} from 'lucide-react'
import {
  externalUsersApi, externalUsersKeys,
  type McpTokenRecord, type McpBindingInput, type McpBindingMasked,
  type McpCredentialType, type McpAuthType,
} from '../services/external-users-api'
import { mcpApi } from '../services/mcp-api'
import { Select } from './ui'
import { confirmDialog } from './ui/confirm'
import { toast } from './ui/toast'
import { getErrorMessage } from '../lib/api-client'
import { copyToClipboard } from '../lib/clipboard'

const AUTH_TYPE_OPTIONS: { label: string; value: McpAuthType }[] = [
  { label: 'Bearer Token', value: 'bearer_token' },
  { label: 'API Key', value: 'api_key' },
  { label: 'Basic', value: 'basic' },
]
const CREDENTIAL_TYPE_OPTIONS: { label: string; value: McpCredentialType }[] = [
  { label: 'Token（直传）', value: 'token' },
  { label: '用户名密码', value: 'password' },
]

interface BindingFormState {
  credential_type: McpCredentialType
  auth_type: McpAuthType
  token: string
  username: string
  password: string
  header_name: string
}

export function ExternalUsersPage() {
  const queryClient = useQueryClient()
  const invalidate = () => queryClient.invalidateQueries({ queryKey: externalUsersKeys.list() })

  const { data, isLoading } = useQuery({ queryKey: externalUsersKeys.list(), queryFn: externalUsersApi.list })
  const tokens = data?.items ?? []

  const { data: connsData } = useQuery({
    queryKey: ['mcp-connections', 'for-external-users'],
    queryFn: () => mcpApi.list({ page_size: 100 }),
  })
  const connections = connsData?.items ?? []
  const connMap = new Map(connections.map(c => [c.id, c.name]))

  /* ═══ 创建用户 ═══ */
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [revealedToken, setRevealedToken] = useState<{ name: string; token: string } | null>(null)

  const createMutation = useMutation({
    mutationFn: (name: string) => externalUsersApi.create({ name }),
    onSuccess: (result) => { toast.success('用户创建成功'); invalidate(); setCreateOpen(false); setCreateName(''); setRevealedToken({ name: result.name, token: result.token_plaintext }) },
    onError: (err) => toast.error(getErrorMessage(err, '创建失败')),
  })

  /* ═══ 复制/轮换/删除 ═══ */
  const [copyingId, setCopyingId] = useState<string | null>(null)
  const handleCopy = async (tok: McpTokenRecord) => {
    setCopyingId(tok.id)
    try {
      const r = await externalUsersApi.reveal(tok.id)
      copyToClipboard(r.token)
    } catch { toast.error('复制失败') } finally { setCopyingId(null) }
  }
  const rotateMutation = useMutation({
    mutationFn: (id: string) => externalUsersApi.rotate(id),
    onSuccess: (r, id) => { toast.success('已轮换'); invalidate(); const tok = tokens.find(t => t.id === id); setRevealedToken({ name: tok?.name ?? '', token: r.token_plaintext }) },
    onError: (err) => toast.error(getErrorMessage(err, '轮换失败')),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => externalUsersApi.remove(id),
    onSuccess: () => { toast.success('已删除'); invalidate() },
    onError: (err) => toast.error(getErrorMessage(err, '删除失败')),
  })

  /* ═══ 编辑 Modal + 内联绑定编辑 ═══ */
  const [editTarget, setEditTarget] = useState<McpTokenRecord | null>(null)
  const [inlineEditor, setInlineEditor] = useState<{ mode: 'add' | 'edit'; connId: string; existing?: McpBindingMasked } | null>(null)
  const [bindingSaving, setBindingSaving] = useState(false)

  const openAddInline = () => setInlineEditor({ mode: 'add', connId: '' })
  const openEditInline = (connId: string, b: McpBindingMasked) => setInlineEditor({ mode: 'edit', connId, existing: b })

  const handleSaveBinding = async (connId: string, binding: McpBindingInput) => {
    if (!editTarget) return
    const baseBindings: Record<string, McpBindingInput> = {}
    for (const [cid, b] of Object.entries(editTarget.mcp_bindings || {})) { baseBindings[cid] = { credential_type: b.credential_type, auth_type: b.auth_type } }
    baseBindings[connId] = binding
    setBindingSaving(true)
    try { const updated = await externalUsersApi.update(editTarget.id, { mcp_bindings: baseBindings }); toast.success('已保存'); invalidate(); setEditTarget(updated); setInlineEditor(null) }
    catch (err) { toast.error(getErrorMessage(err, '保存失败')) } finally { setBindingSaving(false) }
  }

  const removeBinding = async (connId: string) => {
    if (!editTarget) return
    const confirmed = await confirmDialog({ title: '移除绑定', description: `确定移除「${connMap.get(connId) ?? connId}」？`, okText: '移除', danger: true })
    if (!confirmed) return
    const baseBindings: Record<string, McpBindingInput> = {}
    for (const [cid, b] of Object.entries(editTarget.mcp_bindings || {})) { if (cid !== connId) baseBindings[cid] = { credential_type: b.credential_type, auth_type: b.auth_type } }
    try { const updated = await externalUsersApi.update(editTarget.id, { mcp_bindings: baseBindings }); toast.success('已移除'); invalidate(); setEditTarget(updated) }
    catch (err) { toast.error(getErrorMessage(err, '移除失败')) }
  }

  const copyText = (text: string) => {
    copyToClipboard(text)
    toast.success('已复制')
  }
  const inputCls = "w-full px-3 py-2 bg-[#121214] border border-[#27272a] rounded-lg text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:border-indigo-500 transition"
  const labelCls = "block text-xs font-medium text-slate-400 mb-1.5"

  return (
    <div className="space-y-6">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-indigo-500/10 flex items-center justify-center"><Users className="w-5 h-5 text-indigo-400" /></div>
          <div><h2 className="text-lg font-semibold text-slate-100">外部用户</h2><p className="text-xs text-slate-500">管理终端用户的 Token 和 MCP 凭证绑定</p></div>
        </div>
        <button onClick={() => { setCreateName(''); setCreateOpen(true) }} className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium rounded-lg transition-colors"><Plus className="w-4 h-4" /> 创建用户</button>
      </div>

      {/* 统计 */}
      <div className="grid grid-cols-3 gap-3">
        {[{ label: '用户总数', value: tokens.length, color: 'text-slate-300' }, { label: '活跃', value: tokens.filter(t => t.status === 'active').length, color: 'text-emerald-400' }, { label: '已绑定 MCP', value: tokens.reduce((s, t) => s + Object.keys(t.mcp_bindings || {}).length, 0), color: 'text-sky-400' }].map(s => (
          <div key={s.label} className="bg-[#18181b] border border-[#27272a] rounded-xl p-4"><div className={`text-2xl font-bold ${s.color}`}>{s.value}</div><div className="text-xs text-slate-500 mt-0.5">{s.label}</div></div>
        ))}
      </div>

      {/* 列表 */}
      <div className="bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 text-slate-500 animate-spin" /></div>
        ) : tokens.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-slate-600"><Users className="w-8 h-8 mb-2" /><span className="text-sm">暂无外部用户</span></div>
        ) : (
          <table className="w-full text-sm">
            <thead><tr className="border-b border-[#27272a] text-xs text-slate-500">
              <th className="text-left font-semibold px-4 py-3">用户</th><th className="text-left font-semibold px-4 py-3">Token</th>
              <th className="text-left font-semibold px-4 py-3">MCP 绑定</th><th className="text-left font-semibold px-4 py-3">创建时间</th><th className="text-right font-semibold px-4 py-3">操作</th>
            </tr></thead>
            <tbody>
              {tokens.map(tok => {
                const bindingCount = Object.keys(tok.mcp_bindings || {}).length
                return (
                  <tr key={tok.id} className="border-b border-[#27272a]/50 hover:bg-[#121214]/60 transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-8 h-8 rounded-lg bg-indigo-500/10 flex items-center justify-center shrink-0"><Users className="w-4 h-4 text-indigo-400" /></div>
                        <div><div className="text-slate-200 font-medium">{tok.name}</div><div className="text-xs text-slate-600">{tok.status === 'active' ? '活跃' : '已禁用'}</div></div>
                      </div>
                    </td>
                    <td className="px-4 py-3"><code className="text-xs font-mono text-slate-400 bg-[#121214] px-2 py-0.5 rounded border border-[#27272a]">{tok.token}</code></td>
                    <td className="px-4 py-3">
                      {bindingCount > 0 ? <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-sky-500/10 text-sky-400 text-xs font-medium"><Link2 className="w-3 h-3" />{bindingCount} 个 MCP</span> : <span className="text-xs text-slate-600">未绑定</span>}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">{tok.created_at?.slice(0, 10)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button onClick={() => handleCopy(tok)} disabled={copyingId === tok.id} className="p-1.5 text-slate-500 hover:text-slate-200 hover:bg-[#27272a] rounded-md transition-colors" title="复制 Token">
                          {copyingId === tok.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Copy className="w-4 h-4" />}
                        </button>
                        <button onClick={() => { setEditTarget(tok); setInlineEditor(null) }} className="p-1.5 text-slate-500 hover:text-indigo-400 hover:bg-[#27272a] rounded-md transition-colors" title="编辑"><Pencil className="w-4 h-4" /></button>
                        <button onClick={() => rotateMutation.mutate(tok.id)} className="p-1.5 text-slate-500 hover:text-amber-400 hover:bg-[#27272a] rounded-md transition-colors" title="轮换 Token"><RefreshCw className="w-4 h-4" /></button>
                        <button onClick={async () => { const confirmed = await confirmDialog({ title: '删除用户', description: `删除「${tok.name}」后 token 立即失效。`, okText: '删除', danger: true }); if (confirmed) deleteMutation.mutate(tok.id) }} className="p-1.5 text-slate-500 hover:text-rose-400 hover:bg-[#27272a] rounded-md transition-colors" title="删除"><Trash2 className="w-4 h-4" /></button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ═══ 创建用户 Modal ═══ */}
      {createOpen && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in" onClick={() => setCreateOpen(false)}>
          <div className="w-full max-w-md bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="p-4 border-b border-[#27272a] flex items-center justify-between"><h3 className="text-sm font-semibold text-slate-100">创建外部用户</h3><button onClick={() => setCreateOpen(false)} className="text-slate-500 hover:text-slate-200"><X className="w-4 h-4" /></button></div>
            <div className="p-4 space-y-4">
              <div>
                <label className={labelCls}>用户名称 *</label>
                <input value={createName} onChange={e => setCreateName(e.target.value)} placeholder="例如：张三 / 部门A-李四" maxLength={100} autoFocus onKeyDown={e => { if (e.key === 'Enter' && createName.trim()) createMutation.mutate(createName.trim()) }} className={inputCls} />
                <div className="flex items-center justify-between mt-1.5"><p className="text-xs text-slate-600">创建后可在编辑里查看 Token、添加 MCP 绑定</p><span className="text-xs text-slate-700">{createName.length}/100</span></div>
              </div>
            </div>
            <div className="p-4 border-t border-[#27272a] flex justify-end gap-2">
              <button onClick={() => setCreateOpen(false)} className="px-3 py-1.5 text-sm text-slate-400 hover:text-slate-200 transition">取消</button>
              <button onClick={() => createMutation.mutate(createName.trim())} disabled={!createName.trim() || createMutation.isPending} className="px-4 py-1.5 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded-lg transition-colors">{createMutation.isPending ? '创建中...' : '创建'}</button>
            </div>
          </div>
        </div>
      )}

      {/* ═══ Token 展示 Modal ═══ */}
      {revealedToken && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in" onClick={() => setRevealedToken(null)}>
          <div className="w-full max-w-md bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="p-4 border-b border-[#27272a] flex items-center justify-between"><h3 className="text-sm font-semibold text-slate-100">Token</h3><button onClick={() => setRevealedToken(null)} className="text-slate-500 hover:text-slate-200"><X className="w-4 h-4" /></button></div>
            <div className="p-4 space-y-3">
              <p className="text-xs text-slate-500">用户：{revealedToken.name}</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs font-mono text-slate-300 bg-[#121214] px-3 py-2 rounded-lg border border-[#27272a] break-all select-all">{revealedToken.token}</code>
                <button onClick={() => copyText(revealedToken.token)} className="p-2 text-slate-500 hover:text-slate-200 bg-[#121214] border border-[#27272a] rounded-lg transition-colors"><Copy className="w-4 h-4" /></button>
              </div>
            </div>
            <div className="p-4 border-t border-[#27272a] flex justify-end"><button onClick={() => setRevealedToken(null)} className="px-4 py-1.5 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors">关闭</button></div>
          </div>
        </div>
      )}

      {/* ═══ 编辑用户 Modal（MCP 绑定列表 + 内联编辑，不套子 Modal）═══ */}
      {editTarget && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 animate-fade-in" onClick={() => { setEditTarget(null); setInlineEditor(null) }}>
          <div className="w-full max-w-xl bg-[#18181b] border border-[#27272a] rounded-xl overflow-hidden shadow-2xl" onClick={e => e.stopPropagation()}>
            <div className="p-4 border-b border-[#27272a] flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-100">编辑 — {editTarget.name}</h3>
              <button onClick={() => { setEditTarget(null); setInlineEditor(null) }} className="text-slate-500 hover:text-slate-200"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3 max-h-[60vh] overflow-y-auto">
              {/* MCP 绑定列表 */}
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-slate-400">MCP 绑定 ({Object.keys(editTarget.mcp_bindings || {}).length})</span>
                {!inlineEditor && <button onClick={openAddInline} className="flex items-center gap-1 px-2 py-1 text-xs text-indigo-400 hover:text-indigo-300 border border-[#27272a] hover:border-indigo-500/30 rounded-md transition-colors"><Plus className="w-3 h-3" /> 添加</button>}
              </div>

              <div className="space-y-2">
                {Object.entries(editTarget.mcp_bindings || {}).map(([connId, b]) => {
                  const isEditing = inlineEditor?.mode === 'edit' && inlineEditor.connId === connId
                  if (isEditing) return (
                    <BindingInlineEditor key={connId} mode="edit" connId={connId} existing={b}
                      connections={connections} usedConnIds={Object.keys(editTarget.mcp_bindings || {})}
                      saving={bindingSaving} onSave={handleSaveBinding} onCancel={() => setInlineEditor(null)} />
                  )
                  return (
                    <div key={connId} className="flex items-center justify-between py-2 px-3 bg-[#121214] border border-[#27272a] rounded-lg">
                      <div className="flex items-center gap-2 min-w-0">
                        <Link2 className="w-3.5 h-3.5 text-sky-400 shrink-0" />
                        <span className="text-xs font-medium text-slate-200 truncate">{connMap.get(connId) ?? connId.slice(0, 12)}</span>
                        <span className="text-xs px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-400 shrink-0">{b.credential_type === 'token' ? 'Token' : '账密'}</span>
                        {b.username && <span className="text-xs text-slate-500 truncate">用户: {b.username}</span>}
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button onClick={() => openEditInline(connId, b)} className="p-1 text-slate-500 hover:text-slate-200 rounded transition-colors"><Pencil className="w-3.5 h-3.5" /></button>
                        <button onClick={() => removeBinding(connId)} className="p-1 text-slate-500 hover:text-rose-400 rounded transition-colors"><Trash2 className="w-3.5 h-3.5" /></button>
                      </div>
                    </div>
                  )
                })}

                {/* 添加新绑定（内联表单） */}
                {inlineEditor?.mode === 'add' && (
                  <BindingInlineEditor mode="add" connId="" connections={connections}
                    usedConnIds={Object.keys(editTarget.mcp_bindings || {})}
                    saving={bindingSaving} onSave={handleSaveBinding} onCancel={() => setInlineEditor(null)} />
                )}

                {/* 空状态 */}
                {Object.keys(editTarget.mcp_bindings || {}).length === 0 && !inlineEditor && (
                  <div className="text-xs text-slate-600 py-4 text-center bg-[#121214] rounded-lg border border-[#27272a]">暂未绑定任何 MCP，点上方"添加"</div>
                )}
              </div>
            </div>
            <div className="p-4 border-t border-[#27272a] flex justify-end">
              <button onClick={() => { setEditTarget(null); setInlineEditor(null) }} className="px-4 py-1.5 text-sm text-slate-400 hover:text-slate-200 transition">关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

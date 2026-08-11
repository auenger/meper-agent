/**
 * 外部用户管理页面 — admin 为终端用户创建通用 token + 绑定各 MCP 凭证。
 *
 * 交互：
 * 1. 列表：卡片列表，每行右侧有 复制token / 轮换 / 删除 按钮，点击行展开
 * 2. 展开：MCP 绑定管理（行内增删改，不弹二级 Modal）
 * 3. 创建：只填名称
 *
 * 详见 docs/planning-artifacts/mcp-credential-broker-design.md。
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Modal, Select, Spin, Tag, Tooltip, message } from 'antd'
import {
  PlusOutlined, UserOutlined, CopyOutlined, DeleteOutlined, EditOutlined,
  ExclamationCircleOutlined, ReloadOutlined, LinkOutlined,
  DownOutlined, UpOutlined,
} from '@ant-design/icons'
import { useTheme } from '../contexts/ThemeContext'
import {
  mcpTokenApi, mcpTokenKeys,
  type McpToken, type McpBindingInput, type McpBindingMasked,
  type McpCredentialType, type McpAuthType,
} from '../services/mcp-token-api'
import { mcpApi } from '../services/mcp-api'

const STATUS_STYLES: Record<string, { label: string; color: string; bg: string }> = {
  active: { label: '活跃', color: '#10B981', bg: '#D1FAE5' },
  disabled: { label: '已禁用', color: '#94A3B8', bg: '#F1F5F9' },
}
const AUTH_TYPE_OPTIONS: { label: string; value: McpAuthType }[] = [
  { label: 'Bearer Token', value: 'bearer_token' },
  { label: 'API Key', value: 'api_key' },
  { label: 'Basic', value: 'basic' },
]
const CREDENTIAL_TYPE_OPTIONS: { label: string; value: McpCredentialType }[] = [
  { label: 'Token（直传）', value: 'token' },
  { label: '用户名密码', value: 'password' },
]

/** 行内绑定编辑器表单 */
interface BindingForm {
  credential_type: McpCredentialType
  auth_type: McpAuthType
  token: string
  username: string
  password: string
  header_name: string
}
const emptyForm = (): BindingForm => ({
  credential_type: 'token', auth_type: 'bearer_token',
  token: '', username: '', password: '', header_name: '',
})

export default function McpTokensPage() {
  const { t } = useTheme()
  const queryClient = useQueryClient()

  const { data: tokensData, isLoading: tokensLoading } = useQuery({
    queryKey: mcpTokenKeys.list({}),
    queryFn: () => mcpTokenApi.list({}),
  })
  const tokens = tokensData?.items ?? []

  const { data: connsData } = useQuery({
    queryKey: ['mcp-connections', 'all-for-tokens'],
    queryFn: () => mcpApi.list({ page_size: 200 }),
  })
  const connections = connsData?.items ?? []
  const connMap = new Map(connections.map(c => [c.id, c.name]))

  /* ═══ 创建用户 ═══ */
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [creating, setCreating] = useState(false)
  const handleCreate = async () => {
    if (!createName.trim()) { message.warning('请输入用户名称'); return }
    setCreating(true)
    try {
      await mcpTokenApi.create({ name: createName.trim() })
      message.success('用户创建成功')
      queryClient.invalidateQueries({ queryKey: mcpTokenKeys.lists() })
      setCreateOpen(false); setCreateName('')
    } catch (err: unknown) {
      message.error(err && typeof err === 'object' && 'message' in err ? (err as { message: string }).message : '创建失败')
    } finally { setCreating(false) }
  }

  /* ═══ 展开 ═══ */
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const expandedToken = tokens.find(x => x.id === expandedId) ?? null

  /* ═══ 复制 token（调 reveal 拿明文） ═══ */
  const [copyingId, setCopyingId] = useState<string | null>(null)
  const handleCopyToken = async (tok: McpToken) => {
    setCopyingId(tok.id)
    try {
      const r = await mcpTokenApi.reveal(tok.id)
      await navigator.clipboard.writeText(r.token)
      message.success('Token 已复制')
    } catch { message.error('复制失败') }
    finally { setCopyingId(null) }
  }

  /* ═══ 轮换 ═══ */
  const handleRotate = (tok: McpToken) => {
    Modal.confirm({
      title: '确认轮换 token', icon: <ExclamationCircleOutlined />,
      content: `轮换后「${tok.name}」的旧 token 立即失效，用户需用新 token 重新登录。`,
      okText: '轮换', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        await mcpTokenApi.rotate(tok.id)
        message.success('已轮换')
        queryClient.invalidateQueries({ queryKey: mcpTokenKeys.lists() })
      },
    })
  }

  /* ═══ 删除 ═══ */
  const handleDelete = (tok: McpToken) => {
    Modal.confirm({
      title: '确认删除', icon: <ExclamationCircleOutlined />,
      content: `删除「${tok.name}」后该 token 立即失效，用户将无法登录。`,
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        await mcpTokenApi.remove(tok.id)
        message.success('已删除')
        if (expandedId === tok.id) setExpandedId(null)
        queryClient.invalidateQueries({ queryKey: mcpTokenKeys.lists() })
      },
    })
  }

  /* ═══ MCP 绑定：行内编辑 ═══ */
  const [inlineEditor, setInlineEditor] = useState<{ mode: 'add' | 'edit'; connId: string; form: BindingForm; existing: McpBindingMasked | null } | null>(null)
  const [bindingSaving, setBindingSaving] = useState(false)

  const openAddInline = () => setInlineEditor({ mode: 'add', connId: '', form: emptyForm(), existing: null })
  const openEditInline = (connId: string, b: McpBindingMasked) => setInlineEditor({
    mode: 'edit', connId,
    form: { credential_type: b.credential_type, auth_type: b.auth_type, token: '', username: b.username ?? '', password: '', header_name: b.header_name ?? '' },
    existing: b,
  })

  const saveInlineBinding = async () => {
    if (!expandedToken || !inlineEditor) return
    const ed = inlineEditor
    if (!ed.connId) { message.warning('请选择 MCP 连接'); return }
    if (ed.form.credential_type === 'token' && ed.mode === 'add' && !ed.form.token.trim()) { message.warning('请填写 token'); return }
    if (ed.form.credential_type === 'password') {
      if (!ed.form.username.trim()) { message.warning('请填写用户名'); return }
      if (ed.mode === 'add' && !ed.form.password) { message.warning('请填写密码'); return }
    }
    // 构造全量 bindings（未修改的只传 credential_type/auth_type，后端合并保留原凭证）
    const baseBindings: Record<string, McpBindingInput> = {}
    for (const [cid, b] of Object.entries(expandedToken.mcp_bindings || {})) {
      baseBindings[cid] = { credential_type: b.credential_type, auth_type: b.auth_type }
    }
    const b: McpBindingInput = { credential_type: ed.form.credential_type, auth_type: ed.form.auth_type }
    if (ed.form.credential_type === 'token') {
      b.token = ed.form.token.trim()
      if (ed.form.auth_type === 'api_key' && ed.form.header_name.trim()) b.header_name = ed.form.header_name.trim()
    } else {
      b.username = ed.form.username.trim()
      // 编辑态密码留空 = 不修改（后端字段级合并保留原值）
      b.password = ed.form.password
    }
    baseBindings[ed.connId] = b

    setBindingSaving(true)
    try {
      await mcpTokenApi.update(expandedToken.id, { mcp_bindings: baseBindings })
      message.success('已保存')
      queryClient.invalidateQueries({ queryKey: mcpTokenKeys.lists() })
      setInlineEditor(null)
    } catch (err: unknown) {
      message.error(err && typeof err === 'object' && 'message' in err ? (err as { message: string }).message : '保存失败')
    } finally { setBindingSaving(false) }
  }

  const removeBinding = (connId: string) => {
    if (!expandedToken) return
    Modal.confirm({
      title: '移除绑定', icon: <ExclamationCircleOutlined />,
      content: `确定移除「${connMap.get(connId) ?? connId}」？`,
      okText: '移除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        const baseBindings: Record<string, McpBindingInput> = {}
        for (const [cid, b] of Object.entries(expandedToken.mcp_bindings || {})) {
          if (cid === connId) continue
          baseBindings[cid] = { credential_type: b.credential_type, auth_type: b.auth_type,
            ...(b.token ? { token: b.token } : {}), ...(b.username ? { username: b.username } : {}),
            ...(b.password ? { password: b.password } : {}), ...(b.header_name ? { header_name: b.header_name } : {}) }
        }
        await mcpTokenApi.update(expandedToken.id, { mcp_bindings: baseBindings })
        message.success('已移除')
        queryClient.invalidateQueries({ queryKey: mcpTokenKeys.lists() })
      },
    })
  }

  const availableConns = (currentConnId?: string) => {
    if (!expandedToken) return connections
    const used = new Set(Object.keys(expandedToken.mcp_bindings || {}))
    return connections.filter(c => c.id === currentConnId || !used.has(c.id))
  }

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      {/* 统计 */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        {[
          { label: '外部用户', value: (tokensData?.total ?? 0).toString() },
          { label: '活跃用户', value: tokens.filter(x => x.status === 'active').length.toString() },
          { label: '已绑定 MCP', value: tokens.reduce((s, x) => s + Object.keys(x.mcp_bindings || {}).length, 0).toString() },
        ].map(s => (
          <div key={s.label} className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="text-2xl font-semibold text-[#0F172A] mb-0.5">{s.value}</div>
            <div className="text-xs text-[#64748B]">{s.label}</div>
          </div>
        ))}
      </div>

      {/* 标题栏 */}
      <div className="flex items-center justify-between gap-4 mb-4">
        <div className="flex items-center gap-2">
          <UserOutlined style={{ color: t.primary }} />
          <span className="font-semibold text-sm text-[#0F172A]">外部用户管理</span>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setCreateName(''); setCreateOpen(true) }}>创建用户</Button>
      </div>

      {/* 列表 */}
      <div className="mb-6">
        {tokensLoading ? (
          <div className="flex items-center justify-center py-12"><Spin /></div>
        ) : tokens.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-[#94A3B8] rounded-lg border border-gray-200 bg-white">
            <UserOutlined style={{ fontSize: 32, marginBottom: 8 }} />
            <span className="text-sm">暂无外部用户</span>
          </div>
        ) : tokens.map((tok, i) => {
          const style = STATUS_STYLES[tok.status] ?? STATUS_STYLES.active
          const bindingCount = Object.keys(tok.mcp_bindings || {}).length
          const expanded = expandedId === tok.id
          return (
            <div key={tok.id} className={`bg-white border rounded-lg transition-all duration-200 ${expanded ? 'border-gray-300 shadow-sm' : 'border-gray-200 hover:border-gray-300 hover:shadow-sm'} ${i > 0 ? 'mt-3' : ''}`}>
              {/* 用户行 */}
              <div className="px-4 py-3.5 flex items-center justify-between rounded-lg">
                <div className="flex items-center gap-3 min-w-0 flex-1 cursor-pointer"
                  onClick={() => { setExpandedId(expanded ? null : tok.id); setInlineEditor(null) }}>
                  <div className="w-9 h-9 rounded-lg flex items-center justify-center text-base shrink-0" style={{ background: t.bg, color: t.primary }}>
                    <UserOutlined />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[#0F172A]">{tok.name}</span>
                      <Tag className="!m-0 !px-1.5 !py-0 !text-[10px] !rounded" style={{ color: '#64748B', background: '#F1F5F9', borderColor: 'transparent' }}>
                        {bindingCount} 个 MCP
                      </Tag>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-[#64748B] mt-0.5">
                      <span className="font-mono bg-[#F8FAFC] px-2 py-0.5 rounded border border-gray-100">{tok.token}</span>
                      <span>{tok.created_at?.slice(0, 10)}</span>
                    </div>
                  </div>
                </div>
                {/* 右侧操作按钮（始终可见） */}
                <div className="flex items-center gap-1 ml-3 shrink-0">
                  <Tag className="!m-0 !mr-2 !px-2 !py-0.5 !text-xs !rounded" style={{ color: style.color, background: style.bg, borderColor: 'transparent' }}>
                    {style.label}
                  </Tag>
                  <Tooltip title="复制 token">
                    <button onClick={() => handleCopyToken(tok)} disabled={copyingId === tok.id}
                      className="border-0 bg-transparent w-8 h-8 flex items-center justify-center rounded-md text-[#64748B] hover:text-[#0F172A] hover:bg-gray-100 transition-colors cursor-pointer disabled:opacity-50">
                      {copyingId === tok.id ? <Spin size="small" /> : <CopyOutlined />}
                    </button>
                  </Tooltip>
                  <Tooltip title="轮换 token">
                    <button onClick={() => handleRotate(tok)}
                      className="border-0 bg-transparent w-8 h-8 flex items-center justify-center rounded-md text-[#64748B] hover:text-[#D97706] hover:bg-gray-100 transition-colors cursor-pointer">
                      <ReloadOutlined />
                    </button>
                  </Tooltip>
                  <Tooltip title="删除">
                    <button onClick={() => handleDelete(tok)}
                      className="border-0 bg-transparent w-8 h-8 flex items-center justify-center rounded-md text-[#64748B] hover:text-[#EF4444] hover:bg-gray-100 transition-colors cursor-pointer">
                      <DeleteOutlined />
                    </button>
                  </Tooltip>
                  <span className="text-[#94A3B8] text-xs ml-1 cursor-pointer"
                    onClick={() => { setExpandedId(expanded ? null : tok.id); setInlineEditor(null) }}>
                    {expanded ? <UpOutlined /> : <DownOutlined />}
                  </span>
                </div>
              </div>

              {/* 展开详情：MCP 绑定管理 */}
              {expanded && expandedToken && (
                <div className="px-4 pb-4 pt-1 mx-4 mb-3 rounded-lg bg-[#F8FAFC] border border-gray-100">
                  <div className="flex items-center justify-between pt-3 mb-3">
                    <span className="text-xs font-medium text-[#374151]">MCP 绑定</span>
                    <Tooltip title="添加绑定">
                      <button onClick={openAddInline}
                        className="flex items-center justify-center w-7 h-7 text-white border-0 cursor-pointer"
                        style={{ background: t.primary, borderRadius: 6 }}>
                        <PlusOutlined style={{ fontSize: 13 }} />
                      </button>
                    </Tooltip>
                  </div>

                  {/* 绑定列表 */}
                  {bindingCount === 0 && (
                    <div className="text-xs text-[#94A3B8] py-3 text-center">暂未绑定任何 MCP</div>
                  )}
                  {Object.entries(expandedToken.mcp_bindings || {}).map(([connId, b]) => (
                    <div key={connId} className="flex items-center justify-between py-2.5 px-3 mb-1.5 rounded-lg bg-white border border-gray-100">
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        <div className="w-7 h-7 rounded flex items-center justify-center shrink-0" style={{ background: '#E0F2FE' }}>
                          <LinkOutlined className="text-xs" style={{ color: '#0369A1' }} />
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-medium text-[#0F172A]">{connMap.get(connId) ?? connId.slice(0, 12)}</span>
                            <Tag className="!m-0 !px-1.5 !py-0 !text-[10px] !rounded" style={{ color: b.credential_type === 'token' ? '#0369A1' : '#7C3AED', background: b.credential_type === 'token' ? '#E0F2FE' : '#F3E8FF', borderColor: 'transparent' }}>
                              {b.credential_type === 'token' ? 'Token' : '账密'}
                            </Tag>
                            <span className="text-[10px] text-[#94A3B8]">{b.auth_type}</span>
                          </div>
                          {b.credential_type === 'password' && b.username && (
                            <div className="text-[10px] text-[#94A3B8] mt-0.5">用户：{b.username}</div>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <Tooltip title="编辑">
                          <button onClick={() => openEditInline(connId, b)} className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#64748B] hover:text-[#0F172A] hover:bg-gray-100 cursor-pointer">
                            <EditOutlined style={{ fontSize: 12 }} />
                          </button>
                        </Tooltip>
                        <Tooltip title="移除">
                          <button onClick={() => removeBinding(connId)} className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#64748B] hover:text-[#EF4444] hover:bg-gray-100 cursor-pointer">
                            <DeleteOutlined style={{ fontSize: 12 }} />
                          </button>
                        </Tooltip>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* ═══ 创建用户 Modal ═══ */}
      <Modal title="创建外部用户" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={handleCreate}
        confirmLoading={creating} okText="创建" cancelText="取消" width={420}>
        <div className="py-2">
          <label className="block text-xs font-medium text-[#374151] mb-1">用户名称 *</label>
          <Input value={createName} onChange={e => setCreateName(e.target.value)} placeholder="例如：张三 / 部门A-李四" maxLength={100} onPressEnter={handleCreate} />
          <div className="text-[10px] text-[#94A3B8] mt-2">创建后生成通用 token，点列表右侧「复制」按钮可复制明文 token 下发给用户。</div>
        </div>
      </Modal>

      {/* ═══ 添加/编辑 MCP 绑定 Modal ═══ */}
      <Modal
        title={inlineEditor?.mode === 'edit' ? '编辑 MCP 绑定' : '添加 MCP 绑定'}
        open={!!inlineEditor}
        onCancel={() => setInlineEditor(null)}
        onOk={saveInlineBinding}
        confirmLoading={bindingSaving}
        okText="保存"
        cancelText="取消"
        width={480}
        destroyOnClose
      >
        {inlineEditor && (() => {
          const ed = inlineEditor
          const isAdd = ed.mode === 'add'
          return (
            <div className="flex flex-col gap-4 py-2">
              {/* MCP 连接选择（仅新增） */}
              {isAdd && (
                <div>
                  <label className="block text-xs font-medium text-[#374151] mb-1">MCP 连接 *</label>
                  <Select
                    className="w-full"
                    value={ed.connId || undefined}
                    onChange={(v: string) => setInlineEditor({ ...ed, connId: v })}
                    placeholder="选择要绑定的 MCP 连接"
                    options={availableConns().map(c => ({ label: c.name, value: c.id }))}
                    showSearch optionFilterProp="label"
                  />
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-[#374151] mb-1">凭证类型</label>
                  <Select className="w-full" value={ed.form.credential_type}
                    onChange={(v: McpCredentialType) => setInlineEditor({ ...ed, form: { ...ed.form, credential_type: v } })}
                    options={CREDENTIAL_TYPE_OPTIONS} />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#374151] mb-1">注入方式</label>
                  <Select className="w-full" value={ed.form.auth_type}
                    onChange={(v: McpAuthType) => setInlineEditor({ ...ed, form: { ...ed.form, auth_type: v } })}
                    options={AUTH_TYPE_OPTIONS} />
                </div>
              </div>

              {ed.form.credential_type === 'token' ? (
                <div>
                  <label className="block text-xs font-medium text-[#374151] mb-1">
                    Token {isAdd ? ' *' : '（留空 = 不修改）'}
                  </label>
                  <Input.Password value={ed.form.token}
                    onChange={e => setInlineEditor({ ...ed, form: { ...ed.form, token: e.target.value } })}
                    placeholder={ed.existing?.token ? `当前：${ed.existing.token}` : '目标 MCP 的 token'} />
                  {ed.form.auth_type === 'api_key' && (
                    <div className="mt-3">
                      <label className="block text-xs font-medium text-[#374151] mb-1">Header 名（可选）</label>
                      <Input value={ed.form.header_name}
                        onChange={e => setInlineEditor({ ...ed, form: { ...ed.form, header_name: e.target.value } })}
                        placeholder="X-API-Key（默认）" />
                    </div>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-[#374151] mb-1">用户名 *</label>
                    <Input value={ed.form.username}
                      onChange={e => setInlineEditor({ ...ed, form: { ...ed.form, username: e.target.value } })}
                      placeholder="用户名" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-[#374151] mb-1">
                      密码 {isAdd ? ' *' : '（留空 = 不修改）'}
                    </label>
                    <Input.Password value={ed.form.password}
                      onChange={e => setInlineEditor({ ...ed, form: { ...ed.form, password: e.target.value } })}
                      placeholder={ed.existing?.password ? `当前：${ed.existing.password}` : '密码'} />
                  </div>
                </div>
              )}
            </div>
          )
        })()}
      </Modal>
    </div>
  )
}

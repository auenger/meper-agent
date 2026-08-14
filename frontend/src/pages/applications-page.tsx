/**
 * ApplicationsPage — 应用（授权边界）管理。
 *
 * admin 创建应用（外部系统抽象）→ 配置登录验证 → 绑定 MCP 资源。
 * 用户在"外部授权"页对应用授权后，Agent 可代表用户访问应用下的 MCP。
 *
 * MCP 绑定编辑器：按分组折叠 + 整组添加/移除 + 单个勾选。
 */
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Modal, Spin, message, Tag, Tooltip, Input, InputNumber, Select } from 'antd'
import {
  AppstoreOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ApiOutlined,
  FolderOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { useTheme } from '../contexts/ThemeContext'
import {
  applicationApi,
  applicationKeys,
  type Application,
  type ApplicationCreateInput,
} from '../services/application-api'
import { mcpApi, mcpKeys } from '../services/mcp-api'
import { mcpCategoryApi, mcpCategoryKeys } from '../services/mcp-category-api'

export default function ApplicationsPage() {
  const { t } = useTheme()
  const queryClient = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editingApp, setEditingApp] = useState<Application | null>(null)

  /* ─── Form state ─── */
  const [formName, setFormName] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formMcpIds, setFormMcpIds] = useState<string[]>([])
  const [loginConfigOpen, setLoginConfigOpen] = useState(true)
  const [formLoginUrl, setFormLoginUrl] = useState('')
  const [formLoginMethod, setFormLoginMethod] = useState('POST')
  const [formUsernameField, setFormUsernameField] = useState('username')
  const [formPasswordField, setFormPasswordField] = useState('password')
  const [formTokenJsonpath, setFormTokenJsonpath] = useState('data.token')
  const [formSessionTtl, setFormSessionTtl] = useState(3600)

  /* ─── Queries ─── */
  const { data: appData, isLoading } = useQuery({
    queryKey: applicationKeys.lists(),
    queryFn: () => applicationApi.list(),
  })
  const applications = appData?.items ?? []

  /* ─── Mutations ─── */
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: applicationKeys.all })
  }

  const saveM = useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: ApplicationCreateInput }) =>
      id ? applicationApi.update(id, input) : applicationApi.create(input),
    onSuccess: (_, { id }) => {
      message.success(id ? '应用已更新' : '应用创建成功')
      invalidate()
      closeModal()
    },
    onError: (e: unknown) => {
      const msg = e && typeof e === 'object' && 'response' in e
        ? (((e as { response?: { data?: { message?: string } } }).response?.data?.message) ?? '保存失败')
        : '保存失败'
      message.error(msg)
    },
  })

  const deleteM = useMutation({
    mutationFn: applicationApi.remove,
    onSuccess: () => {
      message.success('应用已删除')
      invalidate()
    },
    onError: (e: unknown) => {
      const msg = e && typeof e === 'object' && 'response' in e
        ? (((e as { response?: { data?: { message?: string } } }).response?.data?.message) ?? '删除失败')
        : '删除失败'
      message.error(msg)
    },
  })

  /* ─── Actions ─── */
  const openCreate = () => {
    setEditingApp(null)
    setFormName(''); setFormDescription(''); setFormMcpIds([])
    setLoginConfigOpen(true)
    setFormLoginUrl(''); setFormLoginMethod('POST')
    setFormUsernameField('username'); setFormPasswordField('password')
    setFormTokenJsonpath('data.token'); setFormSessionTtl(3600)
    setModalOpen(true)
  }

  const openEdit = (app: Application) => {
    setEditingApp(app)
    setFormName(app.name); setFormDescription(app.description || '')
    setFormMcpIds(app.mcp_connection_ids ?? [])
    const lc = (app.login_config ?? {}) as Record<string, unknown>
    setLoginConfigOpen(true)
    setFormLoginUrl(lc.login_url as string ?? '')
    setFormLoginMethod(lc.method as string ?? 'POST')
    setFormUsernameField(lc.username_field as string ?? 'username')
    setFormPasswordField(lc.password_field as string ?? 'password')
    setFormTokenJsonpath(lc.token_jsonpath as string ?? 'data.token')
    setFormSessionTtl(lc.session_ttl as number ?? 3600)
    setModalOpen(true)
  }

  const closeModal = () => {
    setModalOpen(false)
    setEditingApp(null)
  }

  const handleSubmit = () => {
    if (!formName.trim()) {
      message.warning('请填写应用名称')
      return
    }
    const input: ApplicationCreateInput = {
      name: formName.trim(),
      description: formDescription.trim(),
      mcp_connection_ids: formMcpIds,
      login_config: formLoginUrl.trim() ? {
        login_url: formLoginUrl.trim(),
        method: formLoginMethod || 'POST',
        username_field: formUsernameField.trim() || 'username',
        password_field: formPasswordField.trim() || 'password',
        token_jsonpath: formTokenJsonpath.trim() || 'data.token',
        session_ttl: formSessionTtl || 3600,
      } : {},
    }
    saveM.mutate({ id: editingApp?.id ?? null, input })
  }

  const handleDelete = (app: Application) => {
    Modal.confirm({
      title: '确认删除应用',
      content: `确定要删除「${app.name}」吗？若应用仍绑定了 MCP 将无法删除。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => deleteM.mutate(app.id),
    })
  }

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-[#0F172A]">应用管理</h2>
          <p className="text-xs text-[#64748B] mt-1">
            应用是外部系统的授权边界。创建应用并绑定 MCP 资源后，用户在外部授权页对应用授权即可访问。
          </p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          创建应用
        </Button>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20"><Spin size="large" /></div>
      )}

      {/* Empty */}
      {!isLoading && applications.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-[#94A3B8]">
          <AppstoreOutlined className="text-4xl mb-3" />
          <p className="text-sm">暂无应用</p>
          <p className="text-xs mt-1">点击右上角「创建应用」开始</p>
        </div>
      )}

      {/* App list */}
      {!isLoading && applications.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white">
          <div className="grid grid-cols-[1.2fr_2fr_120px_110px_100px] gap-4 px-5 py-3 bg-[#F8FAFC] border-b border-gray-100 text-xs font-medium text-[#64748B] items-center">
            <span>应用名称</span>
            <span>MCP 资源</span>
            <span>登录验证</span>
            <span>创建时间</span>
            <span>操作</span>
          </div>
          {applications.map((app, i) => (
            <div
              key={app.id}
              className={`grid grid-cols-[1.2fr_2fr_120px_110px_100px] gap-4 px-5 py-3.5 items-center hover:bg-[#F8FAFC] transition-colors ${i > 0 ? 'border-t border-gray-50' : ''}`}
            >
              <div className="flex items-center gap-2 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0" style={{ background: t.bg, color: t.primary }}>
                  <AppstoreOutlined />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-[#0F172A] truncate">{app.name}</div>
                  {app.description && <div className="text-xs text-[#94A3B8] truncate">{app.description}</div>}
                </div>
              </div>
              <span className="text-sm text-[#0F172A]">{app.mcp_connection_ids?.length ?? 0} 个 MCP</span>
              {(app.login_config as Record<string, unknown>)?.login_url ? (
                <Tag className="!m-0 !rounded" color="green">已配置</Tag>
              ) : (
                <Tag className="!m-0 !rounded" color="orange">未配置</Tag>
              )}
              <span className="text-xs text-[#64748B]">{app.created_at?.slice(0, 10) || '-'}</span>
              <div className="flex items-center gap-1">
                <Tooltip title="编辑">
                  <button onClick={() => openEdit(app)} className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#94A3B8] hover:text-[#0F172A] hover:bg-gray-50 transition-colors text-xs">
                    <EditOutlined />
                  </button>
                </Tooltip>
                <Tooltip title="删除">
                  <button onClick={() => handleDelete(app)} disabled={deleteM.isPending} className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#94A3B8] hover:text-[#EF4444] hover:bg-gray-50 transition-colors text-xs disabled:opacity-40">
                    <DeleteOutlined />
                  </button>
                </Tooltip>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create/Edit Modal */}
      <Modal
        title={editingApp ? `编辑应用 — ${editingApp.name}` : '创建应用'}
        open={modalOpen}
        onCancel={closeModal}
        onOk={handleSubmit}
        okText={editingApp ? '保存' : '创建'}
        cancelText="取消"
        confirmLoading={saveM.isPending}
        okButtonProps={{ disabled: !formName.trim() }}
        destroyOnClose
        width={680}
      >
        <div className="flex flex-col gap-3.5 pt-2">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-[#374151] mb-1">应用名称 *</label>
              <Input value={formName} onChange={(e) => setFormName(e.target.value)} placeholder="如：OA 系统" maxLength={100} />
            </div>
            <div>
              <label className="block text-xs font-medium text-[#374151] mb-1">描述</label>
              <Input value={formDescription} onChange={(e) => setFormDescription(e.target.value)} placeholder="应用描述（可选）" maxLength={500} />
            </div>
          </div>

          {/* MCP 绑定编辑器 */}
          <div>
            <label className="block text-xs font-medium text-[#374151] mb-1.5">
              绑定 MCP 资源（已选 {formMcpIds.length} 个）
            </label>
            <McpBindingEditor selected={formMcpIds} onChange={setFormMcpIds} />
          </div>

          {/* 登录验证配置 */}
          <details className="rounded-lg border border-gray-200 px-3 py-2" open={loginConfigOpen} onToggle={(e) => setLoginConfigOpen((e.target as HTMLDetailsElement).open)}>
            <summary className="text-[11px] font-medium text-[#64748B] cursor-pointer select-none">
              登录验证配置（配置后用户可在外部授权页授权此应用）
            </summary>
            <div className="flex flex-col gap-2 mt-2">
              <div className="grid grid-cols-[1fr_80px] gap-2">
                <div>
                  <label className="block text-[10px] text-[#94A3B8] mb-0.5">登录端点 URL</label>
                  <Input value={formLoginUrl} onChange={(e) => setFormLoginUrl(e.target.value)} size="small" placeholder="https://example.com/api/login" />
                </div>
                <div>
                  <label className="block text-[10px] text-[#94A3B8] mb-0.5">方法</label>
                  <Select value={formLoginMethod} onChange={setFormLoginMethod} className="w-full" size="small"
                    options={[{ value: 'POST', label: 'POST' }, { value: 'GET', label: 'GET' }]} />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-[10px] text-[#94A3B8] mb-0.5">用户名字段名</label>
                  <Input value={formUsernameField} onChange={(e) => setFormUsernameField(e.target.value)} size="small" placeholder="username" />
                </div>
                <div>
                  <label className="block text-[10px] text-[#94A3B8] mb-0.5">密码字段名</label>
                  <Input value={formPasswordField} onChange={(e) => setFormPasswordField(e.target.value)} size="small" placeholder="password" />
                </div>
                <div>
                  <label className="block text-[10px] text-[#94A3B8] mb-0.5">Token JSONPath</label>
                  <Input value={formTokenJsonpath} onChange={(e) => setFormTokenJsonpath(e.target.value)} size="small" placeholder="data.token" />
                </div>
              </div>
              <div>
                <label className="block text-[10px] text-[#94A3B8] mb-0.5">Session 缓存秒数</label>
                <InputNumber value={formSessionTtl} onChange={(v) => setFormSessionTtl(v ?? 3600)} min={60} className="w-full" size="small" />
              </div>
            </div>
          </details>
        </div>
      </Modal>
    </div>
  )
}

/* ─── MCP 绑定编辑器：分组折叠 + 整组添加/移除 + 单个勾选 ─── */

function McpBindingEditor({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const { data: mcpData } = useQuery({
    queryKey: mcpKeys.list({ page: 1, page_size: 200 }),
    queryFn: () => mcpApi.list({ page: 1, page_size: 200 }),
  })
  const { data: catData } = useQuery({
    queryKey: mcpCategoryKeys.lists(),
    queryFn: () => mcpCategoryApi.list(),
  })

  const connections = useMemo(() => mcpData?.items ?? [], [mcpData])
  const categories = useMemo(() => catData?.items ?? [], [catData])

  /* 按分组分桶，未分组排最后 */
  const buckets = useMemo(() => {
    const byCat = new Map<string, typeof connections>()
    for (const c of connections) {
      const key = c.category_id || '__ungrouped__'
      if (!byCat.has(key)) byCat.set(key, [])
      byCat.get(key)!.push(c)
    }
    const result: { key: string; name: string; conns: typeof connections }[] = []
    for (const cat of categories) {
      if (byCat.has(cat.id)) {
        result.push({ key: cat.id, name: cat.name, conns: byCat.get(cat.id)! })
      }
    }
    if (byCat.has('__ungrouped__')) {
      result.push({ key: '__ungrouped__', name: '未分组', conns: byCat.get('__ungrouped__')! })
    }
    return result
  }, [connections, categories])

  const selectedSet = useMemo(() => new Set(selected), [selected])

  const toggleGroup = (ids: string[], check: boolean) => {
    if (check) {
      onChange(Array.from(new Set([...selected, ...ids])))
    } else {
      onChange(selected.filter((id) => !ids.includes(id)))
    }
  }

  const toggleOne = (id: string, check: boolean) => {
    if (check) onChange([...selected, id])
    else onChange(selected.filter((x) => x !== id))
  }

  if (connections.length === 0) {
    return <div className="text-xs text-[#94A3B8] py-3 text-center border border-gray-100 rounded-lg">暂无 MCP 连接</div>
  }

  return (
    <div className="border border-[#E2E8F0] rounded-lg max-h-[260px] overflow-y-auto flex flex-col">
      {buckets.map((bucket) => {
        const isCollapsed = collapsed[bucket.key]
        const groupIds = bucket.conns.map((c) => c.id)
        const selectedCount = groupIds.filter((id) => selectedSet.has(id)).length
        const allSelected = selectedCount === groupIds.length
        return (
          <div key={bucket.key} className="flex flex-col border-b border-[#E2E8F0] last:border-b-0">
            <div className="flex items-center justify-between px-2.5 py-1.5 bg-[#F8FAFC]">
              <button
                type="button"
                onClick={() => setCollapsed((s) => ({ ...s, [bucket.key]: !s[bucket.key] }))}
                className="flex items-center gap-1.5 text-[#0F172A] hover:text-[#2563EB] transition-colors"
              >
                {isCollapsed ? <RightOutlined className="text-[10px]" /> : <DownOutlined className="text-[10px]" />}
                <FolderOutlined className="text-[#3B82F6] text-xs" />
                <span className="text-xs font-medium">{bucket.name}</span>
                <span className="text-[10px] text-[#94A3B8]">（{selectedCount}/{groupIds.length}）</span>
              </button>
              <button
                type="button"
                onClick={() => toggleGroup(groupIds, !allSelected)}
                className="text-[10px] text-[#2563EB] hover:underline"
              >
                {allSelected ? '移除整组' : '添加整组'}
              </button>
            </div>
            {!isCollapsed && (
              <div className="divide-y divide-[#F1F5F9]">
                {bucket.conns.map((c) => {
                  const checked = selectedSet.has(c.id)
                  return (
                    <label key={c.id} className="flex items-center justify-between px-3 py-2 hover:bg-[#F8FAFC] transition-colors cursor-pointer">
                      <div className="flex items-center gap-2 min-w-0">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => toggleOne(c.id, e.target.checked)}
                          className="accent-blue-600"
                        />
                        <ApiOutlined className="text-[#10B981] text-xs" />
                        <span className="text-sm text-[#0F172A] truncate">{c.name}</span>
                      </div>
                      <span className={`text-[11px] shrink-0 ${
                        c.status === 'connected' ? 'text-[#10B981]' :
                        c.status === 'error' ? 'text-[#EF4444]' : 'text-[#94A3B8]'
                      }`}>
                        {c.status === 'connected' ? '已连接' : c.status === 'error' ? '异常' : '已断开'}
                      </span>
                    </label>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * 外部授权页面 — 应用授权 + 应用管理（按权限）。
 *
 * 所有登录用户：查看可授权的应用卡片，点击授权 → 弹窗填 username/password
 *   → 后端调 login_url 验证 + 自动建立身份映射。
 * 有 application:write 权限的用户：额外的应用管理区（创建/编辑/删除应用、
 *   绑定 MCP 资源、配置登录验证）。
 */
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Modal, Input, Spin, message, Button, Tag, Tooltip, InputNumber, Select } from 'antd'
import {
  ApiOutlined,
  AppstoreOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  FolderOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons'
import {
  myAppAuthorizationsApi,
  myAppAuthorizationKeys,
  type AvailableApp,
} from '../services/my-app-authorizations-api'
import {
  applicationApi,
  applicationKeys,
  type Application,
  type ApplicationCreateInput,
} from '../services/application-api'
import { mcpApi, mcpKeys } from '../services/mcp-api'
import { mcpCategoryApi, mcpCategoryKeys } from '../services/mcp-category-api'
import { usePermission } from '../hooks/use-permission'

export default function ExternalAuthPage() {
  const queryClient = useQueryClient()
  const canManage = usePermission('application:write')

  /* ─── 授权弹窗 state ─── */
  const [bindModalApp, setBindModalApp] = useState<AvailableApp | null>(null)
  const [formUsername, setFormUsername] = useState('')
  const [formPassword, setFormPassword] = useState('')

  /* ─── Queries ─── */
  const { data: credData, isLoading: credLoading } = useQuery({
    queryKey: myAppAuthorizationKeys.detail(),
    queryFn: () => myAppAuthorizationsApi.get(),
  })

  const { data: appsData, isLoading: appsLoading } = useQuery({
    queryKey: myAppAuthorizationKeys.availableApps(),
    queryFn: () => myAppAuthorizationsApi.availableApps(),
  })

  const availableApps = appsData?.items ?? []
  const bindings = credData?.bindings ?? []
  const bindingMap = new Map(bindings.map((b) => [b.app_id, b]))

  /* ─── Mutations ─── */
  const bindMutation = useMutation({
    mutationFn: ({ appId, input }: { appId: string; input: { username: string; password: string } }) =>
      myAppAuthorizationsApi.authorize(appId, input),
    onSuccess: () => {
      message.success('授权成功')
      queryClient.invalidateQueries({ queryKey: myAppAuthorizationKeys.all })
      setBindModalApp(null)
      setFormUsername('')
      setFormPassword('')
    },
    onError: (e: unknown) => {
      const msg = e && typeof e === 'object' && 'response' in e
        ? (((e as { response?: { data?: { message?: string } } }).response?.data?.message) ?? '授权失败')
        : '授权失败'
      message.error(msg)
    },
  })

  const revokeMutation = useMutation({
    mutationFn: (appId: string) => myAppAuthorizationsApi.revoke(appId),
    onSuccess: () => {
      message.success('已取消授权')
      queryClient.invalidateQueries({ queryKey: myAppAuthorizationKeys.all })
    },
    onError: (e: unknown) => {
      const msg = e && typeof e === 'object' && 'response' in e
        ? (((e as { response?: { data?: { message?: string } } }).response?.data?.message) ?? '取消授权失败')
        : '取消授权失败'
      message.error(msg)
    },
  })

  /* ─── Actions ─── */
  const openBindModal = (app: AvailableApp) => {
    setBindModalApp(app)
    const existing = bindingMap.get(app.id)
    setFormUsername(existing?.username ?? '')
    setFormPassword('')
  }

  const closeBindModal = () => {
    setBindModalApp(null)
    setFormUsername('')
    setFormPassword('')
  }

  const handleAuthorize = () => {
    if (!formUsername.trim() || !formPassword.trim()) {
      message.warning('请填写用户名和密码')
      return
    }
    if (bindModalApp) {
      bindMutation.mutate({
        appId: bindModalApp.id,
        input: { username: formUsername.trim(), password: formPassword },
      })
    }
  }

  const handleRevoke = (app: AvailableApp) => {
    Modal.confirm({
      title: '确认取消授权',
      content: `确定要取消「${app.name}」的授权吗？取消后相关 MCP 将无法访问。`,
      okText: '取消授权',
      okButtonProps: { danger: true },
      cancelText: '返回',
      onOk: () => revokeMutation.mutate(app.id),
    })
  }

  const isLoading = credLoading || appsLoading

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      {/* 应用管理区（有权限才显示） */}
      {canManage && <AppManagementSection />}

      {/* 说明栏 */}
      <div className={`rounded-lg border border-blue-200 bg-blue-50 p-3.5 mb-5 ${canManage ? 'mt-6' : ''}`}>
        <div className="flex items-start gap-2.5">
          <div className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 bg-blue-600 text-white text-xs font-bold">
            i
          </div>
          <div>
            <strong className="block text-xs text-[#1e293b] mb-0.5">连接你的外部应用</strong>
            <span className="text-[11px] text-[#475569] leading-relaxed block">
              授权后，Agent 可以在你的业务权限范围内访问应用下的 MCP 资源。每个应用仍使用自己的账号和权限，凭证加密保存。
            </span>
          </div>
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Spin size="large" tip="加载中..." />
        </div>
      )}

      {/* Empty */}
      {!isLoading && availableApps.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-[#94A3B8]">
          <ApiOutlined className="text-4xl mb-3" />
          <p className="text-sm">暂无可授权的应用</p>
          <p className="text-xs mt-1">
            {canManage ? '在上方「应用管理」创建应用并配置登录验证' : '请联系管理员配置应用'}
          </p>
        </div>
      )}

      {/* App cards */}
      {!isLoading && availableApps.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
          {availableApps.map((app) => {
            const binding = bindingMap.get(app.id)
            const bound = !!binding
            return (
              <div
                key={app.id}
                className="min-w-0 p-4 border border-[#dbe3ed] rounded-[10px] bg-white transition-all duration-150 hover:border-blue-300 hover:shadow-sm"
              >
                {/* Header */}
                <div className="flex justify-between items-start gap-2 mb-3">
                  <div className="flex gap-2.5 items-center min-w-0">
                    <span className="flex-shrink-0 w-10 h-10 grid place-items-center rounded-[9px] text-[#1d4ed8] bg-[#dbeafe] text-[10px] font-bold">
                      <ApiOutlined style={{ fontSize: 18 }} />
                    </span>
                    <div className="min-w-0">
                      <strong className="block text-[13px] text-[#172033] truncate">{app.name}</strong>
                      {app.description && (
                        <span className="block mt-0.5 text-[10px] text-[#64748b] truncate">{app.description}</span>
                      )}
                    </div>
                  </div>
                  <span
                    className={`flex-shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold ${
                      bound ? 'text-[#047857] bg-[#d1fae5]' : 'text-[#9a3412] bg-[#ffedd5]'
                    }`}
                  >
                    {bound ? '已授权' : '未授权'}
                  </span>
                </div>

                {/* Details */}
                <div className="grid gap-1.5 my-3 p-2.5 rounded-lg bg-[#f8fafc]">
                  <div className="grid grid-cols-[64px_1fr] gap-2 text-[10px] leading-relaxed">
                    <span className="text-[#64748b]">包含 MCP</span>
                    <strong className="text-[#172033] font-medium">{app.mcp_count} 个服务</strong>
                  </div>
                  <div className="grid grid-cols-[64px_1fr] gap-2 text-[10px] leading-relaxed">
                    <span className="text-[#64748b]">外部账号</span>
                    <strong className="text-[#172033] font-medium overflow-hidden text-ellipsis">
                      {bound ? (binding!.username || '—') : '尚未绑定'}
                    </strong>
                  </div>
                  <div className="grid grid-cols-[64px_1fr] gap-2 text-[10px] leading-relaxed">
                    <span className="text-[#64748b]">凭证状态</span>
                    <strong className={`font-medium ${bound ? 'text-[#047857]' : 'text-[#94a3b8]'}`}>
                      {bound ? (binding!.password_masked || '已加密') : '—'}
                    </strong>
                  </div>
                </div>

                {/* Action */}
                <button
                  onClick={() => openBindModal(app)}
                  disabled={revokeMutation.isPending}
                  className={`w-full min-h-[36px] rounded-lg text-[11px] font-bold transition-all duration-150 disabled:opacity-40 ${
                    bound
                      ? 'border border-[#bfdbfe] text-[#1d4ed8] bg-white hover:bg-[#eff6ff] hover:border-[#60a5fa]'
                      : 'border border-blue-600 text-white bg-blue-600 hover:bg-[#1d4ed8]'
                  }`}
                >
                  {bound ? '重新认证' : `连接 ${app.name}`}
                </button>
                {bound && (
                  <button
                    onClick={() => handleRevoke(app)}
                    disabled={revokeMutation.isPending}
                    className="w-full mt-1.5 min-h-[28px] rounded-lg text-[10px] text-[#94a3b8] hover:text-[#ef4444] hover:bg-[#fef2f2] transition-colors duration-150"
                  >
                    取消授权
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Authorize Modal */}
      <Modal
        title={bindingMap.has(bindModalApp?.id ?? '') ? '重新授权' : '授权应用'}
        open={!!bindModalApp}
        onCancel={closeBindModal}
        onOk={handleAuthorize}
        okText="授权"
        cancelText="取消"
        confirmLoading={bindMutation.isPending}
        okButtonProps={{ disabled: !formUsername.trim() || !formPassword.trim() }}
        destroyOnClose
        width={400}
      >
        {bindModalApp && (
          <div className="pt-2">
            <div className="rounded-lg bg-[#F8FAFC] px-3 py-2 mb-4">
              <div className="text-xs text-[#64748B]">
                应用：<span className="text-[#0F172A] font-medium">{bindModalApp.name}</span>
              </div>
            </div>
            <div className="flex flex-col gap-3">
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1">用户名 *</label>
                <Input
                  value={formUsername}
                  onChange={(e) => setFormUsername(e.target.value)}
                  placeholder="你在该系统的用户名"
                  autoComplete="off"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-[#374151] mb-1">密码 *</label>
                <Input.Password
                  value={formPassword}
                  onChange={(e) => setFormPassword(e.target.value)}
                  placeholder="你在该系统的密码"
                  autoComplete="new-password"
                />
              </div>
              <div className="text-[10px] text-[#94A3B8]">
                授权时系统会调该应用的登录端点验证账号密码，验证通过后凭证加密存储。
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

/* ═══════════════════════════════════════════════════════════
 * 应用管理区（application:write 权限可见）
 * ═══════════════════════════════════════════════════════════ */

function AppManagementSection() {
  const queryClient = useQueryClient()
  const [modalOpen, setModalOpen] = useState(false)
  const [editingApp, setEditingApp] = useState<Application | null>(null)

  /* ─── Form state ─── */
  const [formName, setFormName] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formMcpIds, setFormMcpIds] = useState<string[]>([])
  const [formLoginUrl, setFormLoginUrl] = useState('')
  const [formLoginMethod, setFormLoginMethod] = useState('POST')
  const [formUsernameField, setFormUsernameField] = useState('username')
  const [formPasswordField, setFormPasswordField] = useState('password')
  const [formTokenJsonpath, setFormTokenJsonpath] = useState('data.token')
  const [formSessionTtl, setFormSessionTtl] = useState(3600)

  /* ─── Query ─── */
  const { data: appData } = useQuery({
    queryKey: applicationKeys.lists(),
    queryFn: () => applicationApi.list(),
  })
  const applications = appData?.items ?? []

  /* ─── Mutations ─── */
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: applicationKeys.all })
    queryClient.invalidateQueries({ queryKey: myAppAuthorizationKeys.all })
  }

  const saveM = useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: ApplicationCreateInput }) =>
      id ? applicationApi.update(id, input) : applicationApi.create(input),
    onSuccess: (_, { id }) => {
      message.success(id ? '应用已更新' : '应用创建成功')
      invalidate()
      closeAppModal()
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
    setFormLoginUrl(lc.login_url as string ?? '')
    setFormLoginMethod(lc.method as string ?? 'POST')
    setFormUsernameField(lc.username_field as string ?? 'username')
    setFormPasswordField(lc.password_field as string ?? 'password')
    setFormTokenJsonpath(lc.token_jsonpath as string ?? 'data.token')
    setFormSessionTtl(lc.session_ttl as number ?? 3600)
    setModalOpen(true)
  }

  const closeAppModal = () => {
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
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <AppstoreOutlined className="text-[#0F172A]" />
          <h3 className="text-sm font-semibold text-[#0F172A]">应用管理</h3>
        </div>
        <Button type="primary" size="small" icon={<PlusOutlined />} onClick={openCreate}>
          创建应用
        </Button>
      </div>

      {applications.length === 0 ? (
        <div className="text-xs text-[#94A3B8] py-4 text-center border border-dashed border-gray-200 rounded-lg">
          暂无应用，点击「创建应用」开始
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {applications.map((app) => (
            <div key={app.id} className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-4 py-2.5 hover:bg-[#F8FAFC] transition-colors">
              <AppstoreOutlined className="text-[#3B82F6] text-sm shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-[#0F172A] truncate">{app.name}</div>
                {app.description && <div className="text-[11px] text-[#94A3B8] truncate">{app.description}</div>}
              </div>
              <span className="text-xs text-[#64748B] shrink-0">{app.mcp_connection_ids?.length ?? 0} 个 MCP</span>
              {(app.login_config as Record<string, unknown>)?.login_url ? (
                <Tag className="!m-0 !rounded shrink-0" color="green">已配置验证</Tag>
              ) : (
                <Tag className="!m-0 !rounded shrink-0" color="orange">未配置验证</Tag>
              )}
              <Tooltip title="编辑">
                <button onClick={() => openEdit(app)} className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#94A3B8] hover:text-[#0F172A] hover:bg-gray-100 transition-colors text-xs">
                  <EditOutlined />
                </button>
              </Tooltip>
              <Tooltip title="删除">
                <button onClick={() => handleDelete(app)} disabled={deleteM.isPending} className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#94A3B8] hover:text-[#EF4444] hover:bg-gray-100 transition-colors text-xs disabled:opacity-40">
                  <DeleteOutlined />
                </button>
              </Tooltip>
            </div>
          ))}
        </div>
      )}

      {/* App Create/Edit Modal */}
      <Modal
        title={editingApp ? `编辑应用 — ${editingApp.name}` : '创建应用'}
        open={modalOpen}
        onCancel={closeAppModal}
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
          <details className="rounded-lg border border-gray-200 px-3 py-2" open={!formLoginUrl}>
            <summary className="text-[11px] font-medium text-[#64748B] cursor-pointer select-none">
              登录验证配置（配置后用户可授权此应用）
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

/* ═══════════════════════════════════════════════════════════
 * MCP 绑定编辑器：分组折叠 + 整组添加/移除 + 单个勾选
 * ═══════════════════════════════════════════════════════════ */

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

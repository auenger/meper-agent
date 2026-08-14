/**
 * MCP page — manage MCP server connections.
 *
 * Displays connected/disconnected/error MCP servers with
 * connection status and available tool counts per server.
 *
 * Powered by TanStack Query + mcp-api.ts service.
 */
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Button, Tag, Select, Tooltip, message, Spin, Modal, Empty, Tabs,
  Input, InputNumber,
} from 'antd'
import {
  PlusOutlined,
  SearchOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  EditOutlined,
  DeleteOutlined,
  ApiOutlined,
  InfoCircleOutlined,
  ThunderboltOutlined,
  FolderOutlined,
} from '@ant-design/icons'
import { useTheme } from '../contexts/ThemeContext'
import {
  mcpApi,
  mcpKeys,
  type McpConnection,
  type ConnectionStatus,
  type McpAuthType,
} from '../services/mcp-api'
import {
  mcpCategoryApi,
  mcpCategoryKeys,
  type McpCategory,
} from '../services/mcp-category-api'
import { toolsApi, type Tool } from '../services/tools-api'
import { agentKeys } from '../services/agent-api'

/* ─── Status mappings ─── */
const STATUS_STYLES: Record<ConnectionStatus, { label: string; color: string; bg: string; dot: string }> = {
  connected: { label: '已连接', color: '#10B981', bg: '#D1FAE5', dot: '#10B981' },
  connecting: { label: '连接中', color: '#3B82F6', bg: '#DBEAFE', dot: '#3B82F6' },
  disconnected: { label: '已断开', color: '#94A3B8', bg: '#F1F5F9', dot: '#94A3B8' },
  error: { label: '错误', color: '#EF4444', bg: '#FEE2E2', dot: '#EF4444' },
}

/* ─── Debounce hook ─── */
function useDebouncedValue<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)
  useState(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  })
  return debounced
}

/* ─── Helper: format time ─── */
function formatTime(iso: string) {
  if (!iso) return '-'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

export default function McpPage() {
  const { t } = useTheme()
  const queryClient = useQueryClient()

  /* ─── Filter state ─── */
  const [searchInput, setSearchInput] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [categoryFilter, setCategoryFilter] = useState<string>('all')
  const debouncedSearch = useDebouncedValue(searchInput, 300)

  /* ─── Category modal state ─── */
  const [categoryModalOpen, setCategoryModalOpen] = useState(false)

  /* ─── MCP form category state ─── */
  const [formCategoryId, setFormCategoryId] = useState('')

  /* ─── Tool list modal state ─── */
  const [toolListModalOpen, setToolListModalOpen] = useState(false)
  const [toolListModalConn, setToolListModalConn] = useState<McpConnection | null>(null)
  const [toolListModalTools, setToolListModalTools] = useState<Tool[]>([])
  const [toolListModalLoading, setToolListModalLoading] = useState(false)

  /* ─── Test connection state ─── */
  const [testingConnId, setTestingConnId] = useState<string | null>(null)

  /* ─── Modal state ─── */
  const [modalOpen, setModalOpen] = useState(false)
  const [editingConn, setEditingConn] = useState<McpConnection | null>(null)
  const [modalMode, setModalMode] = useState<'create' | 'edit'>('create')
  const [formName, setFormName] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formUrl, setFormUrl] = useState('')
  const [formProtocol, setFormProtocol] = useState('streamable-http')
  const [formAuthType, setFormAuthType] = useState<McpAuthType>('none')
  const [formAuthToken, setFormAuthToken] = useState('')           // bearer_token
  const [formAuthApiKey, setFormAuthApiKey] = useState('')         // api_key 值
  const [formAuthHeaderName, setFormAuthHeaderName] = useState('') // api_key header 名（选填）
  const [formAuthUsername, setFormAuthUsername] = useState('')     // basic
  const [formAuthPassword, setFormAuthPassword] = useState('')     // basic
  const [formDefaultParams, setFormDefaultParams] = useState('')
  const [formTimeout, setFormTimeout] = useState(30)

  /* ─── Query: category list (for filter / form / table display) ─── */
  const { data: categoryData } = useQuery({
    queryKey: mcpCategoryKeys.lists(),
    queryFn: () => mcpCategoryApi.list(),
  })
  const categories = categoryData?.items ?? []
  const categoryMap = useMemo(() => {
    const m = new Map<string, McpCategory>()
    for (const c of categoryData?.items ?? []) m.set(c.id, c)
    return m
  }, [categoryData])

  /* ─── Query: connection list ─── */
  const queryParams = {
    page: 1,
    page_size: 50,
    ...(debouncedSearch ? { name: debouncedSearch } : {}),
    ...(statusFilter !== 'all' ? { status: statusFilter as ConnectionStatus } : {}),
    ...(categoryFilter !== 'all' ? { category_id: categoryFilter } : {}),
  }

  const {
    data,
    isLoading,
    isError,
  } = useQuery({
    queryKey: mcpKeys.list(queryParams),
    queryFn: () => mcpApi.list(queryParams),
  })

  const connections = data?.items ?? []
  const total = data?.total ?? 0

  /* ─── Mutation: create ─── */
  const createMutation = useMutation({
    mutationFn: async (input: Parameters<typeof mcpApi.create>[0]) => {
      const conn = await mcpApi.create(input)
      // Auto discover tools after creation
      try {
        await mcpApi.discover(conn.id)
      } catch {
        // Non-blocking — discover failure should not block creation
      }
      return conn
    },
    onSuccess: () => {
      message.success('MCP 连接创建成功，工具已同步')
      queryClient.invalidateQueries({ queryKey: mcpKeys.lists() })
      queryClient.invalidateQueries({ queryKey: agentKeys.all })
      setModalOpen(false)
    },
  })

  /* ─── Mutation: update ─── */
  const updateMutation = useMutation({
    mutationFn: async ({ id, input }: { id: string; input: Parameters<typeof mcpApi.update>[1] }) => {
      const conn = await mcpApi.update(id, input)
      // Auto re-discover tools after update
      try {
        await mcpApi.discover(conn.id)
      } catch {
        // Non-blocking — discover failure should not block update
      }
      return conn
    },
    onSuccess: () => {
      message.success('MCP 连接更新成功，工具已同步')
      queryClient.invalidateQueries({ queryKey: mcpKeys.lists() })
      queryClient.invalidateQueries({ queryKey: agentKeys.all })
      setModalOpen(false)
    },
  })

  /* ─── Mutation: delete ─── */
  const deleteMutation = useMutation({
    mutationFn: mcpApi.remove,
    onSuccess: () => {
      message.success('MCP 连接已删除')
      queryClient.invalidateQueries({ queryKey: mcpKeys.lists() })
      queryClient.invalidateQueries({ queryKey: agentKeys.all })
    },
  })

  /* ─── Actions ─── */
  const openCreateModal = () => {
    setEditingConn(null)
    setModalMode('create')
    setFormName('')
    setFormDescription('')
    setFormCategoryId('')
    setFormUrl('')
    setFormProtocol('streamable-http')
    setFormAuthType('none')
    setFormAuthToken('')
    setFormAuthApiKey('')
    setFormAuthHeaderName('')
    setFormAuthUsername('')
    setFormAuthPassword('')
    setFormDefaultParams('')
    setFormTimeout(30)
    setModalOpen(true)
  }

  const openEditModal = (conn: McpConnection) => {
    setEditingConn(conn)
    setModalMode('edit')
    setFormName(conn.name)
    setFormDescription(conn.description || '')
    setFormCategoryId(conn.category_id || '')
    setFormUrl(conn.url)
    setFormProtocol(conn.protocol)
    setFormAuthType(conn.auth_type)
    // 回填结构化认证字段：脱敏值(***)不回填，留空 + placeholder 提示"已配置"。
    const ac = conn.auth_config || {}
    setFormAuthToken(ac.token && ac.token !== '***' ? ac.token : '')
    setFormAuthApiKey(ac.api_key && ac.api_key !== '***' ? ac.api_key : '')
    setFormAuthHeaderName(typeof ac.header_name === 'string' ? ac.header_name : '')
    setFormAuthUsername(typeof ac.username === 'string' ? ac.username : '')
    setFormAuthPassword(ac.password && ac.password !== '***' ? ac.password : '')
    setFormDefaultParams(conn.default_params && Object.keys(conn.default_params).length > 0 ? JSON.stringify(conn.default_params) : '')
    setFormTimeout(conn.timeout)
    setModalOpen(true)
  }

  const closeModal = () => {
    setModalOpen(false)
    setEditingConn(null)
  }

  const handleSubmit = async () => {
    if (!formName.trim() || !formUrl.trim()) {
      message.warning('请填写必填字段')
      return
    }

    // 按认证方式构造 auth_config：只包含用户实际填了内容的字段。
    // 空值不放进去 → 后端合并时保留原密钥（编辑场景留空=不修改）。
    const authConfig: Record<string, string> = {}
    if (formAuthType === 'api_key') {
      if (formAuthApiKey.trim()) authConfig.api_key = formAuthApiKey.trim()
      if (formAuthHeaderName.trim()) authConfig.header_name = formAuthHeaderName.trim()
    } else if (formAuthType === 'bearer_token') {
      if (formAuthToken.trim()) authConfig.token = formAuthToken.trim()
    } else if (formAuthType === 'basic') {
      if (formAuthUsername.trim()) authConfig.username = formAuthUsername.trim()
      if (formAuthPassword.trim()) authConfig.password = formAuthPassword.trim()
    }

    let defaultParams = {}
    if (formDefaultParams.trim()) {
      try {
        defaultParams = JSON.parse(formDefaultParams)
      } catch {
        message.error('默认参数 JSON 格式错误')
        return
      }
    }

    const input = {
      name: formName.trim(),
      description: formDescription.trim(),
      category_id: formCategoryId || '',
      url: formUrl.trim(),
      protocol: formProtocol || 'streamable-http',
      auth_type: formAuthType,
      auth_config: authConfig,
      default_params: defaultParams,
      timeout: formTimeout || 30,
    }

    if (modalMode === 'edit' && editingConn) {
      updateMutation.mutate({ id: editingConn.id, input })
    } else {
      createMutation.mutate(input)
    }
  }

  const handleDelete = (conn: McpConnection) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除 MCP 连接「${conn.name}」吗？关联的工具将一并删除。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => deleteMutation.mutate(conn.id),
    })
  }

  const handleTestConnection = async (conn: McpConnection) => {
    setTestingConnId(conn.id)
    try {
      const testResult = await mcpApi.test(conn.id)
      if (!testResult.success) {
        message.error(`连接失败：${testResult.error}`)
      } else {
        message.success(`连接成功 · ${testResult.tool_count} 个工具`)
        // Auto discover after successful test
        const discoverResult = await mcpApi.discover(conn.id)
        if (discoverResult.error) {
          message.warning(`同步工具失败：${discoverResult.error}`)
        } else {
          message.success(`工具同步完成（新增 ${discoverResult.created}，更新 ${discoverResult.updated}）`)
        }
      }
      queryClient.invalidateQueries({ queryKey: mcpKeys.lists() })
      queryClient.invalidateQueries({ queryKey: agentKeys.all })
    } catch (err: unknown) {
      const msg = err && typeof err === 'object' && 'message' in err
        ? (err as { message: string }).message : '测试连接失败'
      message.error(msg)
    } finally {
      setTestingConnId(null)
    }
  }

  const handleViewDetail = async (conn: McpConnection) => {
    setToolListModalConn(conn)
    setToolListModalTools([])
    setToolListModalOpen(true)
    setToolListModalLoading(true)

    try {
      const result = await toolsApi.list({
        mcp_connection_id: conn.id,
        page_size: 100,
      })
      setToolListModalTools(result.items)
    } catch {
      message.error('获取工具列表失败')
    } finally {
      setToolListModalLoading(false)
    }
  }

  /* ─── Stats ─── */
  const stats = [
    { label: '连接总数', value: total.toString() },
    { label: '已连接', value: connections.filter(c => c.status === 'connected').length.toString() },
    { label: '已断开', value: connections.filter(c => c.status === 'disconnected').length.toString() },
    { label: '错误', value: connections.filter(c => c.status === 'error').length.toString() },
  ]

  return (
    <div className="animate-[fadeIn_0.3s_ease-out]">
      {/* Stats row */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {stats.map((s) => (
          <div key={s.label} className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="text-2xl font-semibold text-[#0F172A] mb-0.5">
              {isLoading ? <Spin size="small" /> : s.value}
            </div>
            <div className="text-xs text-[#64748B]">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Search / action bar */}
      <div className="flex items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="relative">
            <SearchOutlined className="absolute left-3 top-1/2 -translate-y-1/2 text-[#94A3B8] text-sm" />
            <input
              type="text"
              placeholder="搜索连接名称..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="pl-9 pr-4 py-2 rounded-lg border border-gray-200 text-sm focus:outline-none focus:ring-2 w-64"
              style={{ '--tw-ring-color': t.bg } as React.CSSProperties}
            />
          </div>
          <Select
            value={statusFilter}
            onChange={setStatusFilter}
            className="w-28"
            options={[
              { value: 'all', label: '全部状态' },
              { value: 'connected', label: '已连接' },
              { value: 'disconnected', label: '已断开' },
              { value: 'error', label: '错误' },
            ]}
          />
          <Select
            value={categoryFilter}
            onChange={setCategoryFilter}
            className="w-36"
            options={[
              { value: 'all', label: '全部分组' },
              { value: '', label: '未分组' },
              ...categories.map((c) => ({ value: c.id, label: c.name })),
            ]}
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            icon={<FolderOutlined />}
            onClick={() => setCategoryModalOpen(true)}
          >
            分组管理
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreateModal}
          >
            添加 MCP 连接
          </Button>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Spin size="large" tip="加载中..." />
        </div>
      )}

      {/* Error state */}
      {isError && !isLoading && (
        <div className="flex flex-col items-center justify-center py-20 text-[#94A3B8]">
          <ExclamationCircleOutlined className="text-4xl mb-3" />
          <p className="text-sm">加载失败，请稍后重试</p>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !isError && connections.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-[#94A3B8]">
          <ApiOutlined className="text-4xl mb-3" />
          <p className="text-sm">暂无 MCP 连接，点击右上角「添加 MCP 连接」开始创建</p>
        </div>
      )}

      {/* Table */}
      {!isLoading && !isError && connections.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white">
          {/* Table header */}
          <div className="grid grid-cols-[1.2fr_1.4fr_90px_90px_100px_90px_70px_170px] gap-4 px-5 py-3 bg-[#F8FAFC] border-b border-gray-100 text-xs font-medium text-[#64748B] items-center">
            <span>连接名称</span>
            <span>URL</span>
            <span>分组</span>
            <span>协议</span>
            <span>状态</span>
            <span>上次连接</span>
            <span>工具数</span>
            <span>操作</span>
          </div>

          {/* Rows */}
          {connections.map((conn, i) => {
            const ss = STATUS_STYLES[conn.status] ?? STATUS_STYLES.disconnected
            return (
              <div
                key={conn.id}
                className={`grid grid-cols-[1.2fr_1.4fr_90px_90px_100px_90px_70px_170px] gap-4 px-5 py-3.5 items-center hover:bg-[#F8FAFC] transition-colors duration-150 ${i > 0 ? 'border-t border-gray-50' : ''}`}
              >
                {/* Name */}
                <div className="flex items-center gap-2 min-w-0">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm shrink-0" style={{ background: t.bg, color: t.primary }}>
                    <ApiOutlined />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-[#0F172A] truncate">{conn.name}</div>
                    {conn.description && (
                      <div className="text-xs text-[#94A3B8] truncate">{conn.description}</div>
                    )}
                  </div>
                </div>

                {/* URL */}
                <code className="text-xs font-mono text-[#64748B] truncate">{conn.url}</code>

                {/* Category */}
                <span className="text-xs">
                  {conn.category_id && categoryMap.has(conn.category_id) ? (
                    <Tag className="!m-0 !px-2 !py-0.5 !text-[11px] !rounded" color="blue">
                      {categoryMap.get(conn.category_id)?.name}
                    </Tag>
                  ) : (
                    <span className="text-[#CBD5E1]">—</span>
                  )}
                </span>

                {/* Protocol */}
                <span className="text-xs text-[#64748B]">{conn.protocol}</span>

                {/* Status with dot indicator */}
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: ss.dot }} />
                  <Tag className="!m-0 !px-2 !py-0.5 !text-[11px] !rounded" style={{ color: ss.color, background: ss.bg, borderColor: 'transparent' }}>
                    {conn.status === 'connected' && <CheckCircleOutlined className="mr-1" />}
                    {conn.status === 'disconnected' && <CloseCircleOutlined className="mr-1" />}
                    {conn.status === 'error' && <ExclamationCircleOutlined className="mr-1" />}
                    {conn.status === 'connecting' && <Spin size="small" className="mr-1" />}
                    {ss.label}
                  </Tag>
                </div>

                {/* Last connected */}
                <span className="text-xs text-[#64748B]">{formatTime(conn.last_connected_at)}</span>

                {/* Tools count */}
                <span className="text-sm font-medium text-[#0F172A]">{conn.tool_count}</span>

                {/* Actions */}
                <div className="flex items-center gap-1">
                  <Tooltip title="测试连接">
                    <button
                      onClick={() => handleTestConnection(conn)}
                      disabled={testingConnId === conn.id}
                      className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#10B981] hover:bg-[#D1FAE5] transition-colors duration-150 text-xs disabled:opacity-40"
                    >
                      {testingConnId === conn.id ? <Spin size="small" /> : <ThunderboltOutlined />}
                    </button>
                  </Tooltip>
                  <Tooltip title="查看工具">
                    <button
                      onClick={() => handleViewDetail(conn)}
                      disabled={toolListModalLoading && toolListModalConn?.id === conn.id}
                      className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#3B82F6] hover:bg-[#DBEAFE] transition-colors duration-150 text-xs disabled:opacity-40"
                    >
                      <InfoCircleOutlined />
                    </button>
                  </Tooltip>
                  <Tooltip title="编辑">
                    <button
                      onClick={() => openEditModal(conn)}
                      className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#94A3B8] hover:text-[#0F172A] hover:bg-gray-50 transition-colors duration-150 text-xs"
                    >
                      <EditOutlined />
                    </button>
                  </Tooltip>
                  {conn.status === 'error' && (
                    <Tooltip title={conn.status_message || '错误详情'}>
                      <button className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#EF4444] hover:text-[#DC2626] hover:bg-red-50 transition-colors duration-150 text-xs">
                        <ExclamationCircleOutlined />
                      </button>
                    </Tooltip>
                  )}
                  <Tooltip title="删除">
                    <button
                      onClick={() => handleDelete(conn)}
                      disabled={deleteMutation.isPending}
                      className="border-0 bg-transparent w-7 h-7 flex items-center justify-center rounded text-[#94A3B8] hover:text-[#EF4444] hover:bg-gray-50 transition-colors duration-150 text-xs disabled:opacity-40"
                    >
                      <DeleteOutlined />
                    </button>
                  </Tooltip>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Create / Edit Modal */}
      <Modal
        title={modalMode === 'create' ? '添加 MCP 连接' : '编辑 MCP 连接'}
        open={modalOpen}
        onCancel={closeModal}
        onOk={handleSubmit}
        okText={modalMode === 'create' ? '创建' : '保存'}
        cancelText="取消"
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        okButtonProps={{ disabled: !formName.trim() || !formUrl.trim() }}
        destroyOnClose
        width={560}
      >
        <Tabs
          defaultActiveKey="general"
          size="small"
          className="mt-1"
          items={[
            {
              key: 'general',
              label: '通用配置',
              children: (
                <div className="flex flex-col gap-3.5 pt-1">
                  <div className="grid grid-cols-[1fr_auto] gap-3">
                    <div>
                      <label className="block text-xs font-medium text-[#374151] mb-1">连接名称 *</label>
                      <Input value={formName} onChange={(e) => setFormName(e.target.value)} placeholder="如：文件系统 MCP" maxLength={100} />
                    </div>
                    <div className="w-28">
                      <label className="block text-xs font-medium text-[#374151] mb-1">超时（秒）</label>
                      <InputNumber value={formTimeout} onChange={(v) => setFormTimeout(v ?? 30)} min={1} max={300} className="w-full" />
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-[#374151] mb-1">MCP 服务地址 *</label>
                    <Input value={formUrl} onChange={(e) => setFormUrl(e.target.value)} placeholder="如：http://localhost:8080/mcp" />
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-[#374151] mb-1">分组</label>
                    <Select
                      value={formCategoryId}
                      onChange={setFormCategoryId}
                      className="w-full"
                      placeholder="选择分组（可选）"
                      allowClear
                      options={[
                        { value: '', label: '未分组' },
                        ...categories.map((c) => ({ value: c.id, label: c.name })),
                      ]}
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-[#374151] mb-1">传输协议</label>
                      <Select value={formProtocol} onChange={setFormProtocol} className="w-full"
                        options={[
                          { value: 'streamable-http', label: 'Streamable HTTP' },
                          { value: 'sse', label: 'SSE' },
                        ]} />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-[#374151] mb-1">认证方式</label>
                      <Select value={formAuthType} onChange={setFormAuthType} className="w-full"
                        options={[
                          { value: 'none', label: '无认证' },
                          { value: 'api_key', label: 'API Key' },
                          { value: 'bearer_token', label: 'Bearer Token' },
                          { value: 'basic', label: 'Basic Auth' },
                        ]} />
                    </div>
                  </div>

                  {/* 认证字段（按类型） */}
                  {formAuthType === 'api_key' && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-[#374151] mb-1">Header 名</label>
                        <Input value={formAuthHeaderName} onChange={(e) => setFormAuthHeaderName(e.target.value)} placeholder="X-API-Key" />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-[#374151] mb-1">API Key</label>
                        <Input.Password value={formAuthApiKey} onChange={(e) => setFormAuthApiKey(e.target.value)} placeholder={modalMode === 'edit' ? '留空不修改' : '请输入'} />
                      </div>
                    </div>
                  )}
                  {formAuthType === 'bearer_token' && (
                    <div>
                      <label className="block text-xs font-medium text-[#374151] mb-1">Token</label>
                      <Input.Password value={formAuthToken} onChange={(e) => setFormAuthToken(e.target.value)} placeholder={modalMode === 'edit' ? '留空不修改' : '请输入'} />
                    </div>
                  )}
                  {formAuthType === 'basic' && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-[#374151] mb-1">用户名</label>
                        <Input value={formAuthUsername} onChange={(e) => setFormAuthUsername(e.target.value)} />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-[#374151] mb-1">密码</label>
                        <Input.Password value={formAuthPassword} onChange={(e) => setFormAuthPassword(e.target.value)} placeholder={modalMode === 'edit' ? '留空不修改' : '请输入'} />
                      </div>
                    </div>
                  )}

                  <div>
                    <label className="block text-xs font-medium text-[#374151] mb-1">描述</label>
                    <Input value={formDescription} onChange={(e) => setFormDescription(e.target.value)} placeholder="连接的简要描述" maxLength={500} />
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-[#374151] mb-1">默认参数（JSON）</label>
                    <Input.TextArea value={formDefaultParams} onChange={(e) => setFormDefaultParams(e.target.value)} placeholder='如：{"token": "xxx"}' rows={2} />
                    <div className="text-[10px] text-[#94A3B8] mt-1">工具调用时自动注入的参数，LLM 同名参数会覆盖</div>
                  </div>
                </div>
              ),
            },
          ]}
        />
      </Modal>

      {/* Tool list Modal */}
      <Modal
        title={`MCP 工具列表 — ${toolListModalConn?.name ?? ''}`}
        open={toolListModalOpen}
        onCancel={() => setToolListModalOpen(false)}
        footer={null}
        destroyOnClose
        width={640}
      >
        {toolListModalLoading ? (
          <div className="flex justify-center py-8">
            <Spin size="large" tip="加载工具列表..." />
          </div>
        ) : toolListModalTools.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="该连接暂无已发现的工具"
            className="py-6"
          />
        ) : (
          <div className="flex flex-col gap-3 max-h-[480px] overflow-y-auto">
            {toolListModalTools.map((tool) => {
              // Extract parameter info from input_schema
              const props = (tool.input_schema?.properties as Record<string, { type?: string; description?: string }>) ?? {}
              const paramEntries = Object.entries(props)
              const required = (tool.input_schema?.required as string[]) ?? []

              return (
                <div key={tool.id} className="rounded-lg border border-gray-100 p-3 hover:border-gray-200 transition-colors duration-150">
                  <div className="flex items-start gap-2.5">
                    <div className="w-7 h-7 rounded-md flex items-center justify-center text-xs shrink-0 mt-0.5" style={{ background: '#DBEAFE', color: '#2563EB' }}>
                      <ApiOutlined />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-[#0F172A] mb-0.5">{tool.name}</div>
                      {tool.description && (
                        <div className="text-xs text-[#64748B] mb-2">{tool.description}</div>
                      )}
                      {paramEntries.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {paramEntries.map(([paramName, paramDef]) => (
                            <Tag
                              key={paramName}
                              className="!m-0 !px-1.5 !py-0 !text-[11px] !rounded"
                              style={{
                                color: required.includes(paramName) ? '#2563EB' : '#64748B',
                                background: required.includes(paramName) ? '#DBEAFE' : '#F1F5F9',
                                borderColor: 'transparent',
                              }}
                            >
                              {paramName}
                              {paramDef.type && <span className="ml-1 opacity-60">({paramDef.type})</span>}
                              {required.includes(paramName) && <span className="ml-0.5">*</span>}
                            </Tag>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        {toolListModalTools.length > 0 && (
          <div className="text-xs text-[#94A3B8] text-center pt-3 border-t border-gray-100 mt-3">
            共 {toolListModalTools.length} 个工具
          </div>
        )}
      </Modal>

      {/* Category manage Modal */}
      <McpCategoryModal open={categoryModalOpen} onClose={() => setCategoryModalOpen(false)} />
    </div>
  )
}

/* ─── MCP 分组管理 Modal ─── */

function McpCategoryModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [editId, setEditId] = useState<string | null>(null)
  const [formName, setFormName] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formSort, setFormSort] = useState(0)

  const { data, isLoading } = useQuery({
    queryKey: mcpCategoryKeys.lists(),
    queryFn: () => mcpCategoryApi.list(),
    enabled: open,
  })
  const categories = data?.items ?? []

  const createM = useMutation({
    mutationFn: (input: Parameters<typeof mcpCategoryApi.create>[0]) => mcpCategoryApi.create(input),
    onSuccess: () => {
      message.success('分组创建成功')
      queryClient.invalidateQueries({ queryKey: mcpCategoryKeys.all })
      resetForm()
    },
    onError: (e: unknown) => {
      const msg = e && typeof e === 'object' && 'response' in e
        ? (((e as { response?: { data?: { message?: string } } }).response?.data?.message) ?? '创建失败')
        : '创建失败'
      message.error(msg)
    },
  })

  const updateM = useMutation({
    mutationFn: ({ id, input }: { id: string; input: Parameters<typeof mcpCategoryApi.update>[1] }) =>
      mcpCategoryApi.update(id, input),
    onSuccess: () => {
      message.success('分组已更新')
      queryClient.invalidateQueries({ queryKey: mcpCategoryKeys.all })
      resetForm()
    },
    onError: (e: unknown) => {
      const msg = e && typeof e === 'object' && 'response' in e
        ? (((e as { response?: { data?: { message?: string } } }).response?.data?.message) ?? '更新失败')
        : '更新失败'
      message.error(msg)
    },
  })

  const deleteM = useMutation({
    mutationFn: mcpCategoryApi.remove,
    onSuccess: () => {
      message.success('分组已删除')
      queryClient.invalidateQueries({ queryKey: mcpCategoryKeys.all })
    },
    onError: (e: unknown) => {
      const msg = e && typeof e === 'object' && 'response' in e
        ? (((e as { response?: { data?: { message?: string } } }).response?.data?.message) ?? '删除失败')
        : '删除失败'
      message.error(msg)
    },
  })

  function resetForm() {
    setEditId(null)
    setFormName('')
    setFormDescription('')
    setFormSort(0)
  }

  function startEdit(c: McpCategory) {
    setEditId(c.id)
    setFormName(c.name)
    setFormDescription(c.description || '')
    setFormSort(c.sort ?? 0)
  }

  function handleSubmit() {
    if (!formName.trim()) {
      message.warning('请填写分组名称')
      return
    }
    const input = {
      name: formName.trim(),
      description: formDescription.trim(),
      sort: formSort ?? 0,
    }
    if (editId) {
      updateM.mutate({ id: editId, input })
    } else {
      createM.mutate(input)
    }
  }

  function handleDelete(c: McpCategory) {
    Modal.confirm({
      title: '确认删除分组',
      content: `确定要删除分组「${c.name}」吗？若分组下仍有 MCP 连接将无法删除。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => deleteM.mutate(c.id),
    })
  }

  return (
    <Modal
      title="MCP 分组管理"
      open={open}
      onCancel={() => { resetForm(); onClose() }}
      footer={null}
      destroyOnClose
      width={520}
    >
      <div className="rounded-lg border border-gray-200 bg-[#F8FAFC] p-3 mb-4">
        <div className="text-xs font-medium text-[#374151] mb-2">
          {editId ? '编辑分组' : '新增分组'}
        </div>
        <div className="flex flex-col gap-2.5">
          <div className="grid grid-cols-[1fr_90px] gap-2">
            <div>
              <label className="block text-[11px] text-[#64748B] mb-1">分组名称 *</label>
              <Input value={formName} onChange={(e) => setFormName(e.target.value)} placeholder="如：飞书" maxLength={50} size="small" />
            </div>
            <div>
              <label className="block text-[11px] text-[#64748B] mb-1">排序</label>
              <InputNumber value={formSort} onChange={(v) => setFormSort(v ?? 0)} min={0} className="w-full" size="small" />
            </div>
          </div>
          <div>
            <label className="block text-[11px] text-[#64748B] mb-1">描述</label>
            <Input value={formDescription} onChange={(e) => setFormDescription(e.target.value)} placeholder="分组描述（可选）" maxLength={200} size="small" />
          </div>
          <div className="flex items-center gap-2 justify-end">
            {editId && (
              <Button size="small" onClick={resetForm}>取消编辑</Button>
            )}
            <Button
              type="primary"
              size="small"
              loading={createM.isPending || updateM.isPending}
              onClick={handleSubmit}
            >
              {editId ? '保存' : '新增'}
            </Button>
          </div>
        </div>
      </div>

      {/* 分组列表 */}
      {isLoading ? (
        <div className="flex justify-center py-6"><Spin size="small" /></div>
      ) : categories.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分组" className="py-6" />
      ) : (
        <div className="flex flex-col gap-1.5 max-h-[320px] overflow-y-auto">
          {categories.map((c) => (
            <div key={c.id} className="flex items-center gap-2 rounded-lg border border-gray-100 px-3 py-2 hover:bg-[#F8FAFC] transition-colors">
              <FolderOutlined className="text-[#3B82F6] text-sm shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-[#0F172A] truncate">{c.name}</div>
                {c.description && (
                  <div className="text-[11px] text-[#94A3B8] truncate">{c.description}</div>
                )}
              </div>
              <span className="text-[11px] text-[#CBD5E1] shrink-0">排序 {c.sort ?? 0}</span>
              <Tooltip title="编辑">
                <button
                  onClick={() => startEdit(c)}
                  className="border-0 bg-transparent w-6 h-6 flex items-center justify-center rounded text-[#94A3B8] hover:text-[#0F172A] hover:bg-gray-100 transition-colors text-xs"
                >
                  <EditOutlined />
                </button>
              </Tooltip>
              <Tooltip title="删除">
                <button
                  onClick={() => handleDelete(c)}
                  disabled={deleteM.isPending}
                  className="border-0 bg-transparent w-6 h-6 flex items-center justify-center rounded text-[#94A3B8] hover:text-[#EF4444] hover:bg-gray-100 transition-colors text-xs disabled:opacity-40"
                >
                  <DeleteOutlined />
                </button>
              </Tooltip>
            </div>
          ))}
        </div>
      )}
    </Modal>
  )
}

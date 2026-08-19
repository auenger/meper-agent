/**
 * ToolSelector — 四段式工具选择器组件。
 *
 * 将 Agent 工具配置拆分为四个分类：
 *  1. Built-in 工具（可配的文件类工具 + 始终开启的能力型工具）— 动态拉取自 /tools/builtin
 *  2. Skill 工具（source=markdown 的上传技能）— Switch 开关列表
 *  3. MCP 连接（远程工具服务器）— Switch 开关列表
 *  4. 工作流（Workflow 模板）— Switch 开关列表
 *
 * 作为受控组件使用，value/onChange 接收/返回统一的 ToolSelectorValue。
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Switch, Skeleton, Typography, Alert, Tag, Button, Dropdown,
  Modal, Form, Input, InputNumber,
} from 'antd'
import {
  CodeOutlined,
  ApiOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  ThunderboltOutlined,
  ApartmentOutlined,
  ToolOutlined,
  LockOutlined,
  PlusOutlined,
  FolderOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { toolsApi, toolKeys } from '../services/tools-api'
import { mcpApi, mcpKeys } from '../services/mcp-api'
import { mcpCategoryApi, mcpCategoryKeys, type McpCategory } from '../services/mcp-category-api'
import { workflowsApi, workflowKeys } from '../services/workflows-api'
import { knowledgeApi, knowledgeKeys } from '../services/knowledge-api'
import type { CustomToolBinding } from '../services/agent-api'

const { Text } = Typography

/** MCP 连接状态中文映射 */
const MCP_STATUS_LABELS: Record<string, string> = {
  connecting: '连接中',
  connected: '已连接',
  disconnected: '已断开',
  error: '异常',
}

/* ─── Value 类型 ─── */
export interface ToolSelectorValue {
  builtin_config: string[]
  skill_ids: string[]
  mcp_connection_ids: string[]
  workflow_ids: string[]
  custom_tool_ids: string[]
  /** 自定义工具绑定(含 user_args/凭证)。 */
  custom_tools: CustomToolBinding[]
  knowledge_base_ids: string[]
}

// eslint-disable-next-line react-refresh/only-export-components
export const DEFAULT_TOOL_VALUE: ToolSelectorValue = {
  // 文件类内建工具 + run_code 默认全开(与后端 create_agent 端点的默认值
  // 保持一致,避免新建后工具面板的勾选状态与落库值不一致导致视觉跳变)。
  builtin_config: ['bash', 'read', 'write', 'glob', 'grep', 'run_code'],
  skill_ids: [],
  mcp_connection_ids: [],
  workflow_ids: [],
  custom_tool_ids: [],
  custom_tools: [],
  knowledge_base_ids: [],
}

/* ─── Props ─── */
export interface ToolSelectorProps {
  value?: ToolSelectorValue
  onChange?: (value: ToolSelectorValue) => void
  /** 是否正在加载（父表单编辑态初始化时使用） */
  loading?: boolean
}

/**
 * 合并当前值与 partial update，返回新对象。
 */
function mergeValue(
  prev: ToolSelectorValue,
  patch: Partial<ToolSelectorValue>,
): ToolSelectorValue {
  return { ...prev, ...patch }
}

export default function ToolSelector({
  value = DEFAULT_TOOL_VALUE,
  onChange,
  loading = false,
}: ToolSelectorProps) {
  /* ─── 数据请求 ─── */
  const { data: builtinsData, isLoading: builtinsLoading, isError: builtinsError } = useQuery({
    queryKey: toolKeys.builtins(),
    queryFn: () => toolsApi.listBuiltins(),
  })

  const { data: skillsData, isLoading: skillsLoading, isError: skillsError } = useQuery({
    queryKey: toolKeys.list({ page: 1, page_size: 100, source: 'markdown' }),
    queryFn: () => toolsApi.list({ page: 1, page_size: 100, source: 'markdown' }),
  })

  const { data: mcpData, isLoading: mcpLoading, isError: mcpError } = useQuery({
    queryKey: mcpKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => mcpApi.list({ page: 1, page_size: 100 }),
  })

  const { data: mcpCategoryData } = useQuery({
    queryKey: mcpCategoryKeys.lists(),
    queryFn: () => mcpCategoryApi.list(),
  })

  const { data: wfData, isLoading: wfLoading, isError: wfError } = useQuery({
    queryKey: workflowKeys.list({ page: 1, page_size: 100, status: 'published' }),
    queryFn: () => workflowsApi.list({ page: 1, page_size: 100, status: 'published' }),
  })

  const { data: kbData, isLoading: kbLoading, isError: kbError } = useQuery({
    queryKey: knowledgeKeys.list({}),
    queryFn: () => knowledgeApi.list({}),
  })

  const availableSkills = skillsData?.items ?? []
  const availableMcpConnections = useMemo(() => mcpData?.items ?? [], [mcpData])
  const availableWorkflows = wfData?.items ?? []
  const availableKbs = kbData?.items ?? []

  /* MCP 分组：按 category_id 分桶，分组按 sort 升序，未分组排最后 */
  const UNGROUPED_KEY = '__ungrouped__'
  const mcpBuckets = useMemo(() => {
    const cats = mcpCategoryData?.items ?? []
    // 收集所有出现过的 category_id（含未分组），保持分组 sort 顺序
    const byCat = new Map<string, typeof availableMcpConnections>()
    for (const conn of availableMcpConnections) {
      const key = conn.category_id || UNGROUPED_KEY
      if (!byCat.has(key)) byCat.set(key, [])
      byCat.get(key)!.push(conn)
    }
    // 组装成有序列表：有分组的按 sort 升序，未分组排最后
    const result: { key: string; category: McpCategory | undefined; conns: typeof availableMcpConnections }[] = []
    const grouped = cats
      .filter((c) => byCat.has(c.id))
      .sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0))
    for (const c of grouped) {
      result.push({ key: c.id, category: c, conns: byCat.get(c.id)! })
    }
    if (byCat.has(UNGROUPED_KEY)) {
      result.push({ key: UNGROUPED_KEY, category: undefined, conns: byCat.get(UNGROUPED_KEY)! })
    }
    return result
  }, [availableMcpConnections, mcpCategoryData])
  const [mcpCollapsed, setMcpCollapsed] = useState<Record<string, boolean>>({})

  /* Built-in 工具拆分:可配的文件类 vs 始终开启的能力型 */
  const allBuiltins = builtinsData ?? []
  const configurableBuiltins = allBuiltins.filter((t) => t.configurable !== false)
  const alwaysOnBuiltins = allBuiltins.filter((t) => t.configurable === false)

  /* ─── Loading ─── */
  if (loading) {
    return (
      <div className="flex flex-col gap-4 p-4">
        <Skeleton active paragraph={{ rows: 1 }} />
        <Skeleton active paragraph={{ rows: 1 }} />
        <Skeleton active paragraph={{ rows: 1 }} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {/* ────────── Built-in 工具 ────────── */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <ThunderboltOutlined className="text-[#F59E0B] text-base" />
          <Text strong className="text-sm">
            Built-in 工具
          </Text>
          <Text className="text-[11px] text-[#94A3B8]">
            （{value.builtin_config.length}/{configurableBuiltins.length} 已启用）
          </Text>
        </div>
        {builtinsError ? (
          <Alert message="加载内建工具失败" type="error" showIcon className="!rounded-lg" />
        ) : builtinsLoading ? (
          <Skeleton active paragraph={{ rows: 2 }} />
        ) : (
          <div className="max-h-[180px] overflow-y-auto border border-[#E2E8F0] rounded-lg divide-y divide-[#E2E8F0]">
            {configurableBuiltins.map((tool) => {
              const enabled = value.builtin_config.includes(tool.name)
              return (
                <div
                  key={tool.name}
                  className="flex items-center justify-between px-3 py-2 hover:bg-[#F8FAFC] transition-colors"
                >
                  <span className="text-sm text-[#0F172A] truncate pr-2">{tool.name}</span>
                  <Switch
                    size="small"
                    checked={enabled}
                    onChange={(checked) => {
                      const next = checked
                        ? [...value.builtin_config, tool.name]
                        : value.builtin_config.filter((n) => n !== tool.name)
                      onChange?.(mergeValue(value, { builtin_config: next }))
                    }}
                  />
                </div>
              )
            })}
            {alwaysOnBuiltins.map((tool) => (
              <div
                key={tool.name}
                className="flex items-center justify-between px-3 py-2 bg-[#F8FAFC]"
              >
                <div className="flex items-center gap-1.5 min-w-0 pr-2">
                  <LockOutlined className="text-[#94A3B8] text-xs shrink-0" />
                  <span className="text-sm text-[#0F172A] truncate">{tool.name}</span>
                </div>
                <Switch size="small" checked disabled />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ────────── Skill 工具 ────────── */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <CodeOutlined className="text-[#3B82F6] text-base" />
          <Text strong className="text-sm">
            Skill 工具
          </Text>
          <Text className="text-[11px] text-[#94A3B8]">
            （{value.skill_ids.length}/{availableSkills.length} 已启用）
          </Text>
        </div>
        {skillsError ? (
          <Alert message="加载 Skill 列表失败" type="error" showIcon className="!rounded-lg" />
        ) : skillsLoading ? (
          <Skeleton active paragraph={{ rows: 2 }} />
        ) : (
          <div className="max-h-[180px] overflow-y-auto border border-[#E2E8F0] rounded-lg divide-y divide-[#E2E8F0]">
            {availableSkills.length === 0 ? (
              <div className="px-3 py-3 text-center text-[11px] text-[#94A3B8]">
                暂无可用 Skill，请先在工具中心上传
              </div>
            ) : availableSkills.map((s) => {
              const enabled = value.skill_ids.includes(s.id)
              return (
                <div
                  key={s.id}
                  className="flex items-center justify-between px-3 py-2 hover:bg-[#F8FAFC] transition-colors"
                >
                  <span className="text-sm text-[#0F172A] truncate pr-2">{s.name}</span>
                  <Switch
                    size="small"
                    checked={enabled}
                    onChange={(checked) => {
                      const next = checked
                        ? [...value.skill_ids, s.id]
                        : value.skill_ids.filter((id) => id !== s.id)
                      onChange?.(mergeValue(value, { skill_ids: next }))
                    }}
                  />
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ────────── MCP 连接（按分组折叠 + 组内全选） ────────── */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <ApiOutlined className="text-[#10B981] text-base" />
          <Text strong className="text-sm">
            MCP 连接
          </Text>
          <Text className="text-[11px] text-[#94A3B8]">
            （{value.mcp_connection_ids.length}/{availableMcpConnections.length} 已启用）
          </Text>
        </div>
        {mcpError ? (
          <Alert message="加载 MCP 连接列表失败" type="error" showIcon className="!rounded-lg" />
        ) : mcpLoading ? (
          <Skeleton active paragraph={{ rows: 2 }} />
        ) : (
          <div className="max-h-[240px] overflow-y-auto border border-[#E2E8F0] rounded-lg flex flex-col">
            {availableMcpConnections.length === 0 ? (
              <div className="px-3 py-3 text-center text-[11px] text-[#94A3B8]">
                暂无 MCP 连接，请先在 MCP 页面配置
              </div>
            ) : mcpBuckets.length === 1 ? (
              /* 只有一个桶（必然是未分组）→ 沿用平铺渲染，不显示分组标题 */
              <div className="divide-y divide-[#E2E8F0]">
                {mcpBuckets[0].conns.map((c) => {
                  const enabled = value.mcp_connection_ids.includes(c.id)
                  const statusLabel = MCP_STATUS_LABELS[c.status] ?? c.status
                  return (
                    <div
                      key={c.id}
                      className="flex items-center justify-between px-3 py-2 hover:bg-[#F8FAFC] transition-colors"
                    >
                      <div className="flex items-center gap-2 min-w-0 pr-2">
                        <span className="text-sm text-[#0F172A] truncate">{c.name}</span>
                        <span className={`text-[11px] shrink-0 ${
                          c.status === 'connected' ? 'text-[#10B981]' :
                          c.status === 'error' ? 'text-[#EF4444]' :
                          'text-[#94A3B8]'
                        }`}>
                          {statusLabel}
                        </span>
                      </div>
                      <Switch
                        size="small"
                        checked={enabled}
                        onChange={(checked) => {
                          const next = checked
                            ? [...value.mcp_connection_ids, c.id]
                            : value.mcp_connection_ids.filter((id) => id !== c.id)
                          onChange?.(mergeValue(value, { mcp_connection_ids: next }))
                        }}
                      />
                    </div>
                  )
                })}
              </div>
            ) : (
              /* 多个桶 → 分组折叠 + 组内全选 */
              mcpBuckets.map((bucket) => {
                const collapsed = mcpCollapsed[bucket.key]
                const groupIds = bucket.conns.map((c) => c.id)
                const selectedInGroup = groupIds.filter((id) => value.mcp_connection_ids.includes(id))
                const allSelected = selectedInGroup.length === groupIds.length
                const groupName = bucket.category?.name ?? '未分组'
                return (
                  <div key={bucket.key} className="flex flex-col border-b border-[#E2E8F0] last:border-b-0">
                    {/* 分组标题行 */}
                    <div className="flex items-center justify-between px-2.5 py-1.5 bg-[#F8FAFC]">
                      <button
                        type="button"
                        onClick={() => setMcpCollapsed((s) => ({ ...s, [bucket.key]: !s[bucket.key] }))}
                        className="flex items-center gap-1.5 text-[#0F172A] hover:text-[#2563EB] transition-colors"
                      >
                        {collapsed ? <RightOutlined className="text-[10px]" /> : <DownOutlined className="text-[10px]" />}
                        <FolderOutlined className="text-[#3B82F6] text-xs" />
                        <Text strong className="text-xs">{groupName}</Text>
                        <Text className="text-[10px] text-[#94A3B8]">
                          （{selectedInGroup.length}/{groupIds.length}）
                        </Text>
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          const next = allSelected
                            ? value.mcp_connection_ids.filter((id) => !groupIds.includes(id))
                            : Array.from(new Set([...value.mcp_connection_ids, ...groupIds]))
                          onChange?.(mergeValue(value, { mcp_connection_ids: next }))
                        }}
                        className="text-[10px] text-[#2563EB] hover:underline"
                      >
                        {allSelected ? '取消全选' : '全选'}
                      </button>
                    </div>
                    {/* 组内连接列表 */}
                    {!collapsed && (
                      <div className="divide-y divide-[#F1F5F9]">
                        {bucket.conns.map((c) => {
                          const enabled = value.mcp_connection_ids.includes(c.id)
                          const statusLabel = MCP_STATUS_LABELS[c.status] ?? c.status
                          return (
                            <div
                              key={c.id}
                              className="flex items-center justify-between px-3 py-2 hover:bg-[#F8FAFC] transition-colors"
                            >
                              <div className="flex items-center gap-2 min-w-0 pr-2">
                                <span className="text-sm text-[#0F172A] truncate">{c.name}</span>
                                <span className={`text-[11px] shrink-0 ${
                                  c.status === 'connected' ? 'text-[#10B981]' :
                                  c.status === 'error' ? 'text-[#EF4444]' :
                                  'text-[#94A3B8]'
                                }`}>
                                  {statusLabel}
                                </span>
                              </div>
                              <Switch
                                size="small"
                                checked={enabled}
                                onChange={(checked) => {
                                  const next = checked
                                    ? [...value.mcp_connection_ids, c.id]
                                    : value.mcp_connection_ids.filter((id) => id !== c.id)
                                  onChange?.(mergeValue(value, { mcp_connection_ids: next }))
                                }}
                              />
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        )}
      </div>

      {/* ────────── 知识库 ────────── */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <DatabaseOutlined className="text-[#0EA5E9] text-base" />
          <Text strong className="text-sm">
            知识库
          </Text>
          <Text className="text-[11px] text-[#94A3B8]">
            （{value.knowledge_base_ids.length}/{availableKbs.length} 已绑定）
          </Text>
        </div>
        {kbError ? (
          <Alert message="加载知识库列表失败" type="error" showIcon className="!rounded-lg" />
        ) : kbLoading ? (
          <Skeleton active paragraph={{ rows: 2 }} />
        ) : (
          <div className="max-h-[180px] overflow-y-auto border border-[#E2E8F0] rounded-lg divide-y divide-[#E2E8F0]">
            {availableKbs.length === 0 ? (
              <div className="px-3 py-3 text-center text-[11px] text-[#94A3B8]">
                暂无知识库，请先在知识库页面创建
              </div>
            ) : availableKbs.map((kb) => {
              const enabled = value.knowledge_base_ids.includes(kb.id)
              return (
                <div
                  key={kb.id}
                  className="flex items-center justify-between px-3 py-2 hover:bg-[#F8FAFC] transition-colors"
                >
                  <div className="flex items-center gap-2 min-w-0 pr-2">
                    <Tag color={kb.type === 'vector' ? 'purple' : 'green'} className="!text-[10px] !px-1.5 !py-0">
                      {kb.type === 'vector' ? '向量' : '树形'}
                    </Tag>
                    <span className="text-sm text-[#0F172A] truncate">{kb.name}</span>
                    {kb.description && (
                      <span className="text-[11px] text-[#94A3B8] truncate max-w-[200px]">
                        {kb.description}
                      </span>
                    )}
                  </div>
                  <Switch
                    size="small"
                    checked={enabled}
                    onChange={(checked) => {
                      const next = checked
                        ? [...value.knowledge_base_ids, kb.id]
                        : value.knowledge_base_ids.filter((id) => id !== kb.id)
                      onChange?.(mergeValue(value, { knowledge_base_ids: next }))
                    }}
                  />
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ────────── Workflow ────────── */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <ApartmentOutlined className="text-[#F97316] text-base" />
          <Text strong className="text-sm">
            工作流
          </Text>
          <Text className="text-[11px] text-[#94A3B8]">
            （{value.workflow_ids.length}/{availableWorkflows.length} 已启用）
          </Text>
        </div>
        {wfError ? (
          <Alert message="加载工作流列表失败" type="error" showIcon className="!rounded-lg" />
        ) : wfLoading ? (
          <Skeleton active paragraph={{ rows: 2 }} />
        ) : (
          <div className="max-h-[180px] overflow-y-auto border border-[#E2E8F0] rounded-lg divide-y divide-[#E2E8F0]">
            {availableWorkflows.length === 0 ? (
              <div className="px-3 py-3 text-center text-[11px] text-[#94A3B8]">
                暂无已发布的工作流，请先在工作流页面创建并发布
              </div>
            ) : availableWorkflows.map((wf) => {
              const enabled = value.workflow_ids.includes(wf.id)
              return (
                <div
                  key={wf.id}
                  className="flex items-center justify-between px-3 py-2 hover:bg-[#F8FAFC] transition-colors"
                >
                  <div className="flex items-center gap-2 min-w-0 pr-2">
                    <span className="text-sm text-[#0F172A] truncate">{wf.name}</span>
                    {wf.description && (
                      <span className="text-[11px] text-[#94A3B8] truncate max-w-[200px]">
                        {wf.description}
                      </span>
                    )}
                  </div>
                  <Switch
                    size="small"
                    checked={enabled}
                    onChange={(checked) => {
                      const next = checked
                        ? [...value.workflow_ids, wf.id]
                        : value.workflow_ids.filter((id) => id !== wf.id)
                      onChange?.(mergeValue(value, { workflow_ids: next }))
                    }}
                  />
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ────────── Custom Tools ────────── */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <ToolOutlined className="text-[#8B5CF6] text-base" />
          <Text strong className="text-sm">
            自定义工具
          </Text>
          <Text className="text-[11px] text-[#94A3B8]">
            （{value.custom_tools?.length || 0} 已绑定）
          </Text>
        </div>
        <CustomToolSelector
          value={value.custom_tools || []}
          onChange={(tools) => onChange?.(mergeValue(value, {
            custom_tools: tools,
            custom_tool_ids: tools.map((b) => b.tool_id),
          }))}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Custom tool selector — 已绑定卡片(含参数概要) + 添加弹窗(填 user_args)
// ---------------------------------------------------------------------------

function CustomToolSelector({
  value,
  onChange,
}: {
  value: CustomToolBinding[]
  onChange: (tools: CustomToolBinding[]) => void
}) {
  const [configModal, setConfigModal] = useState<{
    open: boolean
    tool?: { id: string; name: string; description: string; userArgsSchema?: Record<string, unknown> }
    editing?: CustomToolBinding
  }>({ open: false })

  const { data, isLoading, error } = useQuery({
    queryKey: toolKeys.customTools(),
    queryFn: () =>
      toolsApi.list({ page: 1, page_size: 100, source: 'openapi' }).then(async (r1) => {
        const r2 = await toolsApi.list({ page: 1, page_size: 100, source: 'code' })
        const r3 = await toolsApi.list({ page: 1, page_size: 100, source: 'prebuilt' })
        return [...r1.items, ...r2.items, ...r3.items]
      }),
  })

  if (error) {
    return <Alert message="加载自定义工具失败" type="error" showIcon className="!rounded-lg" />
  }
  if (isLoading) {
    return <Skeleton active paragraph={{ rows: 2 }} />
  }

  const availableTools = data || []

  const SOURCE_TAGS: Record<string, { color: string; label: string }> = {
    openapi: { color: 'blue', label: 'API' },
    code: { color: 'green', label: 'Code' },
    prebuilt: { color: 'purple', label: 'Prebuilt' },
  }

  // 已绑定的 tool_id → availableTools 中的 tool 定义(取 user_args_schema)
  const toolDefById = new Map(availableTools.map((t) => [t.id, t]))

  const handleSaveBinding = (binding: CustomToolBinding) => {
    const exists = value.some((b) => b.tool_id === binding.tool_id)
    const next = exists
      ? value.map((b) => (b.tool_id === binding.tool_id ? binding : b))
      : [...value, binding]
    onChange(next)
    setConfigModal({ open: false })
  }

  const handleRemove = (toolId: string) => {
    onChange(value.filter((b) => b.tool_id !== toolId))
  }

  // 未绑定的工具(可选添加)
  const boundIds = new Set(value.map((b) => b.tool_id))
  const unboundTools = availableTools.filter((t) => !boundIds.has(t.id))

  return (
    <div className="space-y-2">
      {/* 已绑定的自定义工具卡片 */}
      {value.length === 0 ? (
        <div className="border border-[#E2E8F0] rounded-lg px-3 py-3 text-center text-[11px] text-[#94A3B8]">
          暂未绑定自定义工具
        </div>
      ) : (
        value.map((binding) => {
          const def = toolDefById.get(binding.tool_id)
          const name = def?.name || binding.tool_id
          const tag = def ? (SOURCE_TAGS[def.source] || { color: 'default', label: def.source }) : null
          // 参数概要(敏感字段显示 ****)
          const argSummary = Object.entries(binding.user_args || {})
            .map(([k, v]) => {
              const val = typeof v === 'string' && v.startsWith('enc:') ? '****' : String(v)
              return `${k}: ${val}`
            })
            .join(' · ')
          return (
            <div
              key={binding.tool_id}
              className="group flex items-center gap-2 border border-[#E2E8F0] rounded-lg px-3 py-2 hover:border-[#CBD5E1] transition-colors"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  {tag && <Tag color={tag.color} className="!text-[10px] !px-1.5 !py-0">{tag.label}</Tag>}
                  <span className="text-sm text-[#0F172A] truncate">{name}</span>
                </div>
                {argSummary && (
                  <div className="text-[11px] text-[#94A3B8] truncate mt-0.5">{argSummary}</div>
                )}
              </div>
              {def?.user_args_schema && Object.keys(def.user_args_schema).length > 0 && (
                <Button
                  type="text"
                  size="small"
                  onClick={() => setConfigModal({
                    open: true,
                    tool: {
                      id: def.id,
                      name: def.name,
                      description: def.description,
                      userArgsSchema: def.user_args_schema as Record<string, unknown>,
                    },
                    editing: binding,
                  })}
                  className="!text-[#3B82F6] !text-xs"
                >
                  编辑参数
                </Button>
              )}
              <Button
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleRemove(binding.tool_id)}
                className="!opacity-0 group-hover:!opacity-100 transition-opacity"
              />
            </div>
          )
        })
      )}

      {/* 添加按钮 + 下拉选择 */}
      {unboundTools.length > 0 && (
        <Dropdown
          trigger={['click']}
          menu={{
            items: unboundTools.map((t) => ({
              key: t.id,
              label: (
                <div className="flex items-center gap-2">
                  <Tag color={(SOURCE_TAGS[t.source] || {}).color} className="!text-[10px] !m-0">
                    {(SOURCE_TAGS[t.source] || {}).label}
                  </Tag>
                  <span>{t.name}</span>
                </div>
              ),
              onClick: () => setConfigModal({
                open: true,
                tool: {
                  id: t.id,
                  name: t.name,
                  description: t.description,
                  userArgsSchema: t.user_args_schema as Record<string, unknown> | undefined,
                },
              }),
            })),
          }}
        >
          <Button size="small" icon={<PlusOutlined />} className="!text-[#3B82F6] !border-[#BFDBFE]">
            添加自定义工具
          </Button>
        </Dropdown>
      )}

      {/* 参数配置弹窗 */}
      <CustomToolConfigModal
        open={configModal.open}
        tool={configModal.tool}
        editing={configModal.editing}
        onSave={handleSaveBinding}
        onCancel={() => setConfigModal({ open: false })}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// 参数配置弹窗 — 基于 user_args_schema 动态渲染表单
// ---------------------------------------------------------------------------

function CustomToolConfigModal({
  open, tool, editing, onSave, onCancel,
}: {
  open: boolean
  tool?: { id: string; name: string; description: string; userArgsSchema?: Record<string, unknown> }
  editing?: CustomToolBinding
  onSave: (binding: CustomToolBinding) => void
  onCancel: () => void
}) {
  const [form] = Form.useForm()

  const properties = useMemo(() => {
    const schema = tool?.userArgsSchema as { properties?: Record<string, unknown> } | undefined
    return (schema?.properties || {}) as Record<string, {
      type?: string
      description?: string
      sensitive?: boolean
      default?: unknown
    }>
  }, [tool])

  // 弹窗打开时填充已有值(editing 的 user_args)
  useEffect(() => {
    if (open && editing) {
      const values: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(editing.user_args || {})) {
        // 敏感字段保留 enc: 原值(前端不显示明文,提交时原样回传)
        values[k] = v
      }
      form.setFieldsValue(values)
    } else if (open) {
      form.resetFields()
    }
  }, [open, editing, form])

  const handleOk = async () => {
    try {
      const values = await form.validateFields()
      const userArgs: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(values)) {
        const isSensitive = properties[k]?.sensitive
        // 敏感字段为空时不传(保留原值);非空才更新
        if (isSensitive && (v === undefined || v === '')) {
          // 保留 editing 里的原值
          if (editing?.user_args?.[k]) userArgs[k] = editing.user_args[k]
          continue
        }
        if (v !== undefined) userArgs[k] = v
      }
      onSave({ tool_id: tool!.id, user_args: userArgs })
    } catch {
      // 校验失败,保持弹窗
    }
  }

  return (
    <Modal
      title={editing ? `编辑参数 — ${tool?.name}` : `添加 — ${tool?.name}`}
      open={open}
      onOk={handleOk}
      onCancel={onCancel}
      okText="确认"
      cancelText="取消"
      destroyOnClose
    >
      {tool?.description && (
        <div className="text-xs text-[#94A3B8] mb-3">{tool.description}</div>
      )}
      {Object.keys(properties).length === 0 ? (
        <div className="py-4 text-center text-sm text-[#94A3B8]">
          此工具无需配置参数,直接点击确认
        </div>
      ) : (
        <Form form={form} layout="vertical" size="small">
          {Object.entries(properties).map(([name, prop]) => {
            const label = (
              <span className="flex items-center gap-1">
                {name}
                {prop.sensitive && (
                  <Tag color="red" className="!text-[10px] !m-0 !px-1">敏感</Tag>
                )}
              </span>
            )
            return (
              <Form.Item key={name} name={name} label={label}>
                {prop.sensitive ? (
                  <Input.Password
                    placeholder={editing?.user_args?.[name] ? '已配置(留空不修改)' : '输入凭证'}
                    autoComplete="new-password"
                  />
                ) : prop.type === 'number' || prop.type === 'integer' ? (
                  <InputNumber className="w-full" placeholder={prop.description || `输入 ${name}`} />
                ) : (
                  <Input placeholder={prop.description || `输入 ${name}`} />
                )}
              </Form.Item>
            )
          })}
        </Form>
      )}
    </Modal>
  )
}

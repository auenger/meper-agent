/**
 * WorkflowDesigner — 工作流模块（重写版）。
 *
 * WS4-Workflow：替换原手搓 SVG 画布（mock handleSimulateRun 模拟），
 * 改为真实后端对接：
 *   - 列表  GET /workflows
 *   - 载入  useQuery(GET /workflows/{id}) → 嵌入 Palette + Canvas + ConfigPanel
 *   - 保存  useMutation(PUT /workflows/{id})
 *   - 发布  useMutation(POST /workflows/{id}/publish)
 *   - 执行  POST /tasks + 轮询 GET /tasks/{id} 读真实 timeline（替代伪造 ExecutionLog）
 *
 * 三栏编辑器由 features/workflow-editor 提供（@xyflow/react 画布）。
 * 无 props — 自管理状态，与 App 的 mock workflows 解耦。
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Play, Save, Upload, Loader2, X, Clock, Pencil, ChevronsLeft, ChevronsRight } from 'lucide-react'
import {
  workflowsApi,
  workflowKeys,
  type WorkflowDetail,
  type WorkflowSummary,
} from '../services/workflows-api'
import {
  tasksApi,
  type TaskDetail,
  type TimelineEvent,
  type TaskStatusValue,
} from '../services/tasks-api'
import type { WorkflowNode } from '../services/types'
import WorkflowNodePalette from '../features/workflow-editor/WorkflowNodePalette'
import WorkflowCanvas from '../features/workflow-editor/WorkflowCanvas'
import WorkflowNodeConfigPanel from '../features/workflow-editor/WorkflowNodeConfigPanel'
import ExecuteInputDialog from '../features/workflow-editor/ExecuteInputDialog'
import { validateWorkflow } from '../features/workflow-editor/utils/workflow-validator'
import type { VariableDefinition } from '../features/workflow-editor/utils/variable-types'
import { Button, Tag, Input } from './ui'
import { toast } from './ui/toast'
import { getErrorMessage } from '../lib/api-client';

/* ─── helpers ─── */

/** 工作流是否可发布（必须先保存） */
function isDraftOrPublished(status: string): boolean {
  return status === 'draft' || status === 'published'
}

const STATUS_LABEL: Record<string, { text: string; color: string }> = {
  draft: { text: '草稿', color: '#64748B' },
  published: { text: '已发布', color: '#10B981' },
  archived: { text: '已归档', color: '#94A3B8' },
}

const TASK_STATUS_META: Record<string, { text: string; color: string }> = {
  pending: { text: '排队中', color: '#64748B' },
  running: { text: '运行中', color: '#3B82F6' },
  waiting_human: { text: '等待人工', color: '#F97316' },
  completed: { text: '已完成', color: '#10B981' },
  failed: { text: '失败', color: '#EF4444' },
  cancelled: { text: '已取消', color: '#94A3B8' },
}

/** 终态任务状态（轮询在这些状态停止） */
const TERMINAL_TASK_STATUSES: TaskStatusValue[] = ['completed', 'failed', 'cancelled']

function formatTime(ts: string): string {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ts
  }
}

/* ─── Timeline 追踪弹窗 ─── */

function TaskTraceModal({ task, onClose }: { task: TaskDetail | null; onClose: () => void }) {
  if (!task) return null
  const meta = TASK_STATUS_META[task.status] ?? { text: task.status, color: '#64748B' }
  const timeline: TimelineEvent[] = task.timeline ?? []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-[#18181b] rounded-xl shadow-2xl w-[640px] max-h-[80vh] flex flex-col border border-[#27272a]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-[#27272a]">
          <div className="flex items-center gap-2">
            <Clock size={14} className="text-[#1E5EFF]" />
            <span className="text-sm font-medium text-[#fafafa]">执行追踪</span>
            <Tag color={meta.color}>{meta.text}</Tag>
          </div>
          <X size={16} className="text-[#71717a] cursor-pointer hover:text-[#fafafa]" onClick={onClose} />
        </div>

        <div className="px-5 py-3 border-b border-[#27272a] grid grid-cols-3 gap-3 text-[11px]">
          <div>
            <div className="text-[#71717a]">任务 ID</div>
            <div className="text-[#fafafa] font-mono truncate">{task.id}</div>
          </div>
          <div>
            <div className="text-[#71717a]">版本</div>
            <div className="text-[#fafafa] font-mono">{task.workflow_version}</div>
          </div>
          <div>
            <div className="text-[#71717a]">创建时间</div>
            <div className="text-[#fafafa]">{formatTime(task.created_at)}</div>
          </div>
        </div>

        {task.error && (
          <div className="mx-5 mt-3 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-[11px]">
            <div className="text-red-400 font-medium mb-1">执行失败：{task.error.error_code}</div>
            <div className="text-red-400 font-mono break-all">{task.error.error_message}</div>
            {task.error.node_id && <div className="text-red-400/70 mt-1">失败节点：{task.error.node_id}</div>}
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {timeline.length === 0 ? (
            <p className="text-xs text-[#71717a] text-center py-6">暂无执行事件</p>
          ) : (
            <div className="space-y-2">
              {timeline.map((evt, idx) => (
                <div key={idx} className="flex gap-3 text-[11px] leading-relaxed">
                  <span className="text-[#71717a] font-mono shrink-0 w-32">
                    {formatTime(evt.timestamp)}
                  </span>
                  <span
                    className={`shrink-0 w-2 h-2 rounded-full mt-1.5 ${
                      evt.event_type === 'node_failed' || evt.event_type === 'error'
                        ? 'bg-red-500'
                        : evt.event_type === 'node_complete' || evt.event_type === 'workflow_completed'
                          ? 'bg-green-500'
                          : evt.event_type === 'node_start'
                            ? 'bg-blue-500'
                            : 'bg-[#52525b]'
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-[#1E5EFF] font-mono">{evt.event_type}</span>
                    {evt.actor && <span className="text-[#71717a]"> · {evt.actor}</span>}
                    {evt.data && Object.keys(evt.data).length > 0 && (
                      <pre className="text-[10px] text-slate-400 mt-0.5 whitespace-pre-wrap break-all font-mono">
                        {JSON.stringify(evt.data)}
                      </pre>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ─── Main Component ─── */

export function WorkflowDesigner({
  theme = 'dark',
  workflowId: controlledWorkflowId,
  onBack,
  onCreated,
}: {
  theme?: 'dark' | 'light'
  /** Optional controlled workflow id (when opened from a list view). */
  workflowId?: string | null
  /** Optional back action (e.g. return to workflow list). */
  onBack?: () => void
  /** Notify parent when a new workflow is created (controlled mode). */
  onCreated?: (id: string) => void
}) {
  const queryClient = useQueryClient()
  const [internalWorkflowId, setInternalWorkflowId] = useState<string | null>(null)
  // Controlled id wins when provided; otherwise fall back to internal selection.
  const selectedWorkflowId = controlledWorkflowId ?? internalWorkflowId
  const setSelectedWorkflowId = (id: string | null) => {
    if (controlledWorkflowId === undefined) setInternalWorkflowId(id)
  }
  const [selectedNode, setSelectedNode] = useState<WorkflowNode | null>(null)
  const [nodes, setNodes] = useState<WorkflowNode[]>([])
  // Editable workflow name draft. Initialized from the loaded detail and reset
  // on workflow switch (same effect that resets `nodes`). handleSave reads this
  // instead of the stale workflowDetail.name so the workflow can be renamed.
  const [nameDraft, setNameDraft] = useState('')
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  // 左侧节点类型面板收缩态（收缩后仅图标列 + Tooltip）
  const [paletteCollapsed, setPaletteCollapsed] = useState(true)
  const [dirtySinceLoad, setDirtySinceLoad] = useState(false)

  // 执行任务追踪弹窗 + 轮询
  const [trackingTask, setTrackingTask] = useState<TaskDetail | null>(null)
  const [traceOpen, setTraceOpen] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 执行参数输入弹窗（开始节点声明了 output_variables 时先收集输入）
  const [execInputOpen, setExecInputOpen] = useState(false)
  const [execInputVariables, setExecInputVariables] = useState<VariableDefinition[]>([])

  /* ─── 工作流列表 ─── */
  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: workflowKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => workflowsApi.list({ page: 1, page_size: 100 }),
  })
  const workflows: WorkflowSummary[] = listData?.items ?? []

  // 自动选中第一个工作流
  useEffect(() => {
    if (!selectedWorkflowId && workflows.length > 0) {
      setSelectedWorkflowId(workflows[0].id)
    }
    // 如果当前选中的已被删除，回退到第一个
    if (selectedWorkflowId && workflows.length > 0 && !workflows.some((w) => w.id === selectedWorkflowId)) {
      setSelectedWorkflowId(workflows[0].id)
    }
  }, [workflows, selectedWorkflowId])

  /* ─── 载入单个工作流 ─── */
  const { data: workflowDetail, isLoading: detailLoading } = useQuery({
    queryKey: selectedWorkflowId ? workflowKeys.detail(selectedWorkflowId) : ['workflows', 'detail', 'none'],
    queryFn: () => workflowsApi.get(selectedWorkflowId!),
    enabled: !!selectedWorkflowId,
  })

  // 详情载入后同步到本地 nodes 编辑状态
  useEffect(() => {
    if (workflowDetail) {
      setNodes(workflowDetail.nodes ?? [])
      setNameDraft(workflowDetail.name)
      setHasUnsavedChanges(false)
      setDirtySinceLoad(false)
      setSelectedNode(null)
    }
  }, [workflowDetail?.id]) // 仅在切换工作流时重置，避免编辑中被打断

  /* ─── 本地节点变更（标记 dirty） ─── */
  const handleNodesChange = useCallback((next: WorkflowNode[]) => {
    setNodes(next)
    setHasUnsavedChanges(true)
    setDirtySinceLoad(true)
  }, [])

  const handleNodeChange = useCallback((updated: WorkflowNode) => {
    setNodes((prev) => prev.map((n) => (n.node_id === updated.node_id ? updated : n)))
    setSelectedNode(updated)
    setHasUnsavedChanges(true)
    setDirtySinceLoad(true)
  }, [])

  const handleNodeDelete = useCallback((nodeId: string) => {
    setNodes((prev) => {
      // 同步移除其他节点 next_nodes/conditions 中对该节点的引用
      return prev
        .filter((n) => n.node_id !== nodeId)
        .map((n) => {
          const config = { ...(n.config ?? {}) } as Record<string, unknown>
          if (Array.isArray(config.next_nodes)) {
            config.next_nodes = (config.next_nodes as Array<{ target: string }>).filter(
              (nn) => nn.target !== nodeId,
            )
          }
          if (n.type === 'gateway' && Array.isArray(config.conditions)) {
            config.conditions = (config.conditions as Array<{ target?: string }>).filter(
              (c) => c.target !== nodeId,
            )
            if (config.default_branch === nodeId) config.default_branch = ''
          }
          return { ...n, config }
        })
    })
    setSelectedNode(null)
    setHasUnsavedChanges(true)
    setDirtySinceLoad(true)
  }, [])

  /* ─── 保存 ─── */
  const saveMutation = useMutation({
    mutationFn: (data: { name?: string; description?: string; nodes: WorkflowNode[] }) =>
      workflowsApi.update(selectedWorkflowId!, data),
    onSuccess: (updated: WorkflowDetail) => {
      setHasUnsavedChanges(false)
      setDirtySinceLoad(false)
      // Keep the draft in sync with the persisted name (covers rename + trim).
      setNameDraft(updated.name)
      queryClient.invalidateQueries({ queryKey: workflowKeys.lists() })
      queryClient.setQueryData(workflowKeys.detail(updated.id), updated)
    },
  })

  const handleSave = useCallback(() => {
    if (!selectedWorkflowId || !workflowDetail) return
    saveMutation.mutate({
      // Empty name falls back to the original so the backend doesn't reject it
      // (Workflow.name has min_length=1).
      name: nameDraft.trim() || workflowDetail.name,
      description: workflowDetail.description,
      nodes,
    })
  }, [selectedWorkflowId, workflowDetail, nameDraft, nodes, saveMutation])

  /* ─── 发布（必须先保存） ─── */
  const publishMutation = useMutation({
    mutationFn: () => workflowsApi.publish(selectedWorkflowId!),
    onSuccess: (updated: WorkflowDetail) => {
      queryClient.invalidateQueries({ queryKey: workflowKeys.lists() })
      queryClient.setQueryData(workflowKeys.detail(updated.id), updated)
    },
  })

  const validation = workflowDetail
    ? validateWorkflow(nodes, workflowDetail.edges ?? [], hasUnsavedChanges)
    : null

  const handlePublish = useCallback(() => {
    if (!selectedWorkflowId) return
    if (hasUnsavedChanges) return // 校验会拦截，这里双保险
    if (validation?.errors.length) {
      const errs = validation.errors.slice(0, 3).map((e) => e.message).join('；')
      toast.error(
        `无法发布：${validation.errors.length} 个错误${errs ? `：${errs}` : ''}`,
        { duration: 0 },
      )
      return
    }
    publishMutation.mutate()
  }, [selectedWorkflowId, hasUnsavedChanges, validation, publishMutation])

  /* ─── 新建工作流 ─── */
  const createMutation = useMutation({
    mutationFn: (data: { name: string; description?: string }) => workflowsApi.create(data),
    onSuccess: (created: WorkflowDetail) => {
      queryClient.invalidateQueries({ queryKey: workflowKeys.lists() })
      if (onCreated) onCreated(created.id)
      else setSelectedWorkflowId(created.id)
    },
  })

  const handleCreate = useCallback(() => {
    createMutation.mutate({
      name: `新工作流 ${new Date().toLocaleString('zh-CN', { hour12: false })}`,
      description: '',
    })
  }, [createMutation])

  /* ─── 执行：POST /tasks + 轮询 GET /tasks/{id} ─── */
  // 清理轮询定时器
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  const [executing, setExecuting] = useState(false)

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const pollTask = useCallback(
    (taskId: string) => {
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const detail = await tasksApi.get(taskId)
          setTrackingTask(detail)
          if (TERMINAL_TASK_STATUSES.includes(detail.status)) {
            stopPolling()
            setExecuting(false)
          }
        } catch (err) {
          stopPolling()
          setExecuting(false)
          toast.error(getErrorMessage(err, '轮询任务失败'), { duration: 0 })
        }
      }, 2000)
    },
    [stopPolling],
  )

  /** 真正发起执行：创建任务 + 轮询 + 打开追踪弹窗 */
  const runWorkflow = useCallback(
    async (input: Record<string, unknown>) => {
      if (!selectedWorkflowId) return
      setExecuting(true)
      setTraceOpen(true)
      try {
        const task = await tasksApi.create({
          workflow_id: selectedWorkflowId,
          input,
        })
        setTrackingTask(task)
        pollTask(task.id)
      } catch (err) {
        setExecuting(false)
        toast.error(getErrorMessage(err, '创建执行任务失败'), { duration: 0 })
      }
    },
    [selectedWorkflowId, pollTask],
  )

  const handleExecute = useCallback(async () => {
    if (!selectedWorkflowId || !workflowDetail) return
    if (workflowDetail.status !== 'published') {
      toast.error('只有已发布的工作流才能执行，请先发布', { duration: 0 })
      return
    }

    // 开始节点是否声明了输入变量 —— 有则先弹窗收集，无则直接执行
    const startNode = nodes.find((n) => n.type === 'start')
    const outputVars = (startNode?.config?.output_variables as VariableDefinition[] | undefined) ?? []
    const hasInput = Array.isArray(outputVars) && outputVars.length > 0
    if (hasInput) {
      setExecInputVariables(outputVars)
      setExecInputOpen(true)
      return
    }
    runWorkflow({})
  }, [selectedWorkflowId, workflowDetail, nodes, runWorkflow])

  /** 执行参数弹窗提交 */
  const handleExecInputSubmit = useCallback(
    (values: Record<string, unknown>) => {
      setExecInputOpen(false)
      runWorkflow(values)
    },
    [runWorkflow],
  )

  const closeTrace = useCallback(() => {
    setTraceOpen(false)
    stopPolling()
  }, [stopPolling])

  const currentStatus = workflowDetail?.status
  const canExecute = currentStatus === 'published' && !executing

  /* ─── render ─── */
  return (
    <div className="flex flex-col h-full gap-4">
      {/* 主体（顶栏已移除：选择器/新建下线，关闭按钮并入画布左上浮动栏）*/}
      {!selectedWorkflowId ? (
        <div className="bg-[#18181b] rounded-2xl border border-[#27272a] p-16 text-center text-sm text-[#71717a]">
          {listLoading ? '加载工作流列表...' : '请新建或选择一个工作流开始编辑'}
        </div>
      ) : detailLoading && nodes.length === 0 ? (
        <div className="bg-[#18181b] rounded-2xl border border-[#27272a] p-16 text-center text-sm text-[#71717a]">
          <Loader2 size={20} className="animate-spin mx-auto mb-2 text-[#1E5EFF]" />
          载入工作流详情...
        </div>
      ) : (
        <>
          {/* 三栏编辑器（flex：Palette 可收缩、ConfigPanel 未选中时隐藏，画布占满中间）*/}
          <div className="flex flex-col xl:flex-row gap-3 flex-1 min-h-0">
            {/* 左：Palette（可收缩）*/}
            <div className={`shrink-0 ${paletteCollapsed ? 'w-14' : 'w-56'} flex flex-col bg-[#18181b] rounded-xl border border-[#27272a] overflow-hidden transition-[width] duration-200`}>
              <button
                onClick={() => setPaletteCollapsed((v) => !v)}
                title={paletteCollapsed ? '展开节点类型' : '收起节点类型'}
                className="shrink-0 flex items-center gap-1.5 px-2 py-2 text-[11px] font-medium text-[#a1a1aa] hover:text-[#fafafa] hover:bg-[#1E5EFF]/10 transition-colors cursor-pointer border-b border-[#27272a]"
              >
                {paletteCollapsed ? (
                  <ChevronsRight className="w-3.5 h-3.5 mx-auto" />
                ) : (
                  <>
                    <ChevronsLeft className="w-3.5 h-3.5" />
                    <span>节点类型</span>
                  </>
                )}
              </button>
              <div className="flex-1 overflow-y-auto scrollbar-custom">
                <WorkflowNodePalette collapsed={paletteCollapsed} />
              </div>
            </div>

            {/* 中：Canvas + 浮动操作栏（画布左上角，不占顶部行）*/}
            <div className="flex-1 min-w-0 relative bg-[#09090b] rounded-xl border border-[#27272a] overflow-hidden">
              <div className="absolute top-3 left-3 z-20 flex items-center gap-2 px-2 py-1.5 rounded-lg bg-[#18181b]/80 backdrop-blur border border-[#27272a] shadow-lg">
                {/* 关闭编辑（返回列表）*/}
                {onBack && (
                  <button
                    onClick={onBack}
                    title="关闭编辑"
                    className="p-1 rounded-md text-[#71717a] hover:text-[#fafafa] hover:bg-[#1E5EFF]/10 transition-colors cursor-pointer"
                  >
                    <X size={14} />
                  </button>
                )}
                {/* 名称（保存后生效）*/}
                <div className="flex items-center gap-1.5 px-2 h-7 rounded-md bg-[#121214] border border-[#27272a] focus-within:border-[#1E5EFF]/60 transition-colors">
                  <Pencil size={12} className="text-[#71717a] shrink-0" />
                  <input
                    value={nameDraft}
                    onChange={(e) => {
                      setNameDraft(e.target.value)
                      setHasUnsavedChanges(true)
                      setDirtySinceLoad(true)
                    }}
                    placeholder="工作流名称"
                    title="修改工作流名称（保存后生效）"
                    className="w-32 bg-transparent text-xs text-[#fafafa] placeholder-[#52525b] focus:outline-none font-medium"
                  />
                </div>
                {/* 当前状态 */}
                {workflowDetail && (
                  <Tag color={STATUS_LABEL[currentStatus ?? 'draft']?.color ?? '#64748B'}>
                    {STATUS_LABEL[currentStatus ?? 'draft']?.text ?? currentStatus}
                  </Tag>
                )}
                {workflowDetail && (
                  <span className="text-[10px] text-[#71717a] whitespace-nowrap">v{workflowDetail.version} · {nodes.length}节点</span>
                )}
                {hasUnsavedChanges && <Tag color="#F59E0B">未保存</Tag>}
                <Button size="small" icon={<Save size={13} />} onClick={handleSave} loading={saveMutation.isPending} disabled={!hasUnsavedChanges}>
                  保存
                </Button>
                <Button size="small" type="primary" icon={<Upload size={13} />} onClick={handlePublish} loading={publishMutation.isPending} disabled={hasUnsavedChanges || !isDraftOrPublished(currentStatus ?? '')}>
                  发布
                </Button>
                <Button size="small" type="primary" icon={executing ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} onClick={handleExecute} disabled={!canExecute}>
                  {executing ? '执行中' : '执行'}
                </Button>
              </div>
              <WorkflowCanvas
                workflowNodes={nodes}
                selectedNodeId={selectedNode?.node_id ?? null}
                onNodesChange={handleNodesChange}
                onSelectNode={setSelectedNode}
                theme={theme}
              />
            </div>

            {/* 右：ConfigPanel（仅选中节点时渲染；未选中时画布占满）*/}
            {selectedNode && (
              <div className="shrink-0 w-96 overflow-y-auto scrollbar-custom">
                <WorkflowNodeConfigPanel
                  selectedNode={selectedNode}
                  allNodes={nodes}
                  onNodeChange={handleNodeChange}
                  onNodeDelete={handleNodeDelete}
                />
              </div>
            )}
          </div>
        </>
      )}

      {/* 执行追踪弹窗 */}
      {traceOpen && (
        <TaskTraceModal task={trackingTask} onClose={closeTrace} />
      )}

      {/* 执行参数输入弹窗 */}
      <ExecuteInputDialog
        open={execInputOpen}
        variables={execInputVariables}
        onCancel={() => setExecInputOpen(false)}
        onSubmit={handleExecInputSubmit}
      />
    </div>
  )
}

export default WorkflowDesigner

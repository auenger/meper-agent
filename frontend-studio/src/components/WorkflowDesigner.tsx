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
import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Play, Save, Upload, Loader2, X, Pencil, ChevronsLeft, ChevronsRight } from 'lucide-react'
import {
  workflowsApi,
  workflowKeys,
  type WorkflowDetail,
  type WorkflowSummary,
} from '../services/workflows-api'
import type { WorkflowNode } from '../services/types'
import { useWorkflowExecution } from '../hooks/useWorkflowExecution'
import { usePermission } from '../hooks/use-permission'
import WorkflowNodePalette from '../features/workflow-editor/WorkflowNodePalette'
import WorkflowCanvas from '../features/workflow-editor/WorkflowCanvas'
import WorkflowNodeConfigPanel from '../features/workflow-editor/WorkflowNodeConfigPanel'
import ExecuteInputDialog from '../features/workflow-editor/ExecuteInputDialog'
import { TaskTraceModal } from '../features/workflow-editor/TaskTraceModal'
import { validateWorkflow } from '../features/workflow-editor/utils/workflow-validator'
import { Button, Tag, Input } from './ui'
import { toast } from './ui/toast'

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
  // 写权限门控：无 workflow:write 的用户隐藏保存/发布按钮（画布查看、执行不受影响）
  const canWrite = usePermission('workflow:write')
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

  // 工作流执行（建任务 + 轮询 + 追踪弹窗 + 输入参数弹窗）—— 详情页与卡片列表页共用
  const exec = useWorkflowExecution()

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

  /* ─── 执行：委托 useWorkflowExecution（建任务 + 轮询 + 追踪弹窗）─── */
  const handleExecute = useCallback(() => {
    if (!selectedWorkflowId) return
    exec.execute(selectedWorkflowId, workflowDetail ?? undefined)
  }, [selectedWorkflowId, workflowDetail, exec])

  const currentStatus = workflowDetail?.status
  const canExecute = currentStatus === 'published' && !exec.executing

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
                {canWrite && (
                  <Button size="small" icon={<Save size={13} />} onClick={handleSave} loading={saveMutation.isPending} disabled={!hasUnsavedChanges}>
                    保存
                  </Button>
                )}
                {canWrite && (
                  <Button size="small" type="primary" icon={<Upload size={13} />} onClick={handlePublish} loading={publishMutation.isPending} disabled={hasUnsavedChanges || !isDraftOrPublished(currentStatus ?? '')}>
                    发布
                  </Button>
                )}
                <Button size="small" type="primary" icon={exec.executing ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} onClick={handleExecute} disabled={!canExecute}>
                  {exec.executing ? '执行中' : '执行'}
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
      {exec.traceOpen && (
        <TaskTraceModal task={exec.trackingTask} onClose={exec.closeTrace} />
      )}

      {/* 执行参数输入弹窗 */}
      <ExecuteInputDialog
        open={exec.execInputOpen}
        variables={exec.execInputVariables}
        onCancel={exec.cancelInput}
        onSubmit={exec.submitInput}
      />
    </div>
  )
}

export default WorkflowDesigner

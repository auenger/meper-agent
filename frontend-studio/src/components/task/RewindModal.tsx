/**
 * RewindModal — 退回重跑弹窗（人工审核暂停期间回退到指定已执行节点）。
 *
 * intervene action='rewind'：把目标节点及其全部下游从 checkpoint 剔除后
 * 恢复执行，引擎重跑该子图。后端仅允许 waiting_human 状态、目标必须是
 * completed_nodes 中非当前暂停节点的节点。
 *
 * 由 frontend（antd 浅色版）tasks-page 的 Rewind Modal 翻译而来：
 * antd Select → studio Select；Input.TextArea/Segmented → 原生 textarea +
 * 手写二段按钮组；输出预览取 checkpoint.variable_snapshot（与 variables
 * 同源，TaskSummary 即可持有，看板场景零额外请求）。
 *
 * 共享入口：TaskDetailPage 操作栏 + TaskBoard 看板卡片（各自传入已有的
 * resolveTemplateId，与 TaskFlowTimeline/TaskFlowGraph 共用 workflowKeys
 * 缓存，同任务详情页内不重复请求）。
 */
import { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { tasksApi, taskKeys, type TaskSummary } from '../../services/tasks-api'
import { workflowsApi, workflowKeys } from '../../services/workflows-api'
import { Modal, Select } from '../ui'
import { toast } from '../ui/toast'
import { getErrorMessage } from '../../lib/api-client'
import { NODE_TYPE_LABEL } from './task-flow-utils'

export interface RewindModalProps {
  task: TaskSummary
  open: boolean
  onClose: () => void
  /** registry id (wfr_...) → 模板 id (wf_...) 解析，未传则直接用 task.workflow_id */
  resolveTemplateId?: (maybeRegistryId: string) => string
}

export function RewindModal({ task, open, onClose, resolveTemplateId }: RewindModalProps) {
  const qc = useQueryClient()
  const checkpoint = task.checkpoint
  const pausedAt = checkpoint?.paused_at_node ?? ''
  // 可选目标：已执行节点中排除当前暂停的 human 节点（后端同样拒绝）
  const completed = useMemo(
    () => (checkpoint?.completed_nodes ?? []).filter((n) => n !== pausedAt),
    [checkpoint, pausedAt],
  )

  /* ─── 表单状态（每次打开重置，Modal 无 destroyOnClose） ─── */
  const [targetNode, setTargetNode] = useState('')
  const [varsMode, setVarsMode] = useState<'none' | 'json'>('none')
  const [varsText, setVarsText] = useState('{}')
  useEffect(() => {
    if (!open) return
    setTargetNode('')
    setVarsMode('none')
    // 预填当前 variable_snapshot 作为 JSON 编辑起点。依赖只看 open：
    // 弹窗打开期间 checkpoint 引用变化（RQ 刷新重建对象）不得清掉用户已填表单。
    const snapshot = task.checkpoint?.variable_snapshot
    setVarsText(snapshot ? JSON.stringify(snapshot, null, 2) : '{}')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  /* ─── 工作流模板：node_id → label / type（缓存与流程图 / 时间线共享） ─── */
  const templateId = useMemo(
    () => (resolveTemplateId ? resolveTemplateId(task.workflow_id) : task.workflow_id),
    [task.workflow_id, resolveTemplateId],
  )
  const { data: wf, isLoading: wfLoading } = useQuery({
    queryKey: workflowKeys.detail(templateId),
    queryFn: () => workflowsApi.get(templateId),
    enabled: open && !!templateId,
    staleTime: 60_000,
    retry: 1,
  })
  const nodeMetaMap = useMemo(() => {
    const m: Record<string, { label: string; type: string }> = {}
    for (const n of wf?.nodes ?? []) m[n.node_id] = { label: n.label || n.node_id, type: n.type }
    return m
  }, [wf])
  const pausedLabel = pausedAt
    ? (nodeMetaMap[pausedAt]?.label ?? pausedAt)
    : ''

  /* ─── rewind mutation（自持，两处入口共用） ─── */
  const rewind = useMutation({
    mutationFn: (vars: { targetNodeId: string; variables?: Record<string, unknown> }) =>
      tasksApi.intervene(task.id, {
        action: 'rewind',
        version: task.version,
        target_node_id: vars.targetNodeId,
        variables: vars.variables,
      }),
    onSuccess: () => {
      toast.success('已退回重跑')
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
      qc.invalidateQueries({ queryKey: taskKeys.detail(task.id) })
      onClose()
    },
    onError: (e) => toast.error(getErrorMessage(e, '退回失败，请重试')),
  })

  const submit = () => {
    if (rewind.isPending) return
    if (!targetNode) {
      toast.warning('请选择退回节点')
      return
    }
    let variables: Record<string, unknown> | undefined
    if (varsMode === 'json') {
      try {
        variables = JSON.parse(varsText)
      } catch {
        toast.error('JSON 格式错误，请检查变量输入')
        return
      }
    }
    rewind.mutate({ targetNodeId: targetNode, variables })
  }

  const snapshot = checkpoint?.variable_snapshot ?? {}
  const targetOutput = targetNode ? snapshot[targetNode] : undefined
  const options = completed.map((n) => {
    const meta = nodeMetaMap[n]
    return {
      value: n,
      label: meta ? `${meta.label} · ${NODE_TYPE_LABEL[meta.type] ?? meta.type}` : n,
    }
  })

  return (
    <Modal
      title="退回重跑"
      open={open}
      onOk={submit}
      onCancel={() => { if (!rewind.isPending) onClose() }}
      okText="确定退回"
      cancelText="取消"
      width={560}
      okButtonProps={{ disabled: !targetNode || rewind.isPending }}
    >
      {!checkpoint ? (
        <div className="text-xs text-[#71717a] py-2">无可回退的执行上下文</div>
      ) : (
        <div className="flex flex-col gap-3 py-2">
          <p className="text-xs text-[#a1a1aa]">
            选择一个已执行的节点，任务将从该节点重新执行其全部下游。
            {pausedLabel && <>当前审批节点（{pausedLabel}）不可选。</>}
          </p>

          {/* 目标节点选择 */}
          <div>
            <label className="block text-xs text-[#a1a1aa] mb-1.5">
              退回到节点 <span className="text-rose-400">*</span>
            </label>
            <Select
              value={targetNode || null}
              onChange={(v) => setTargetNode(v ?? '')}
              placeholder="选择一个已执行的节点"
              options={options}
              loading={wfLoading}
              disabled={wfLoading || completed.length === 0}
            />
            {completed.length === 0 && !wfLoading && (
              <p className="text-[10px] text-[#71717a] mt-1.5">无可回退的节点</p>
            )}
          </div>

          {/* 选中节点的当前输出预览（确认退回点） */}
          {targetNode && (
            <div>
              <label className="block text-xs text-[#a1a1aa] mb-1.5">该节点当前输出</label>
              <pre className="text-[11px] font-mono bg-[#09090b] border border-[#27272a] rounded-lg p-3 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[#a1a1aa]">
                {targetOutput === undefined ? '（无输出）' : JSON.stringify(targetOutput, null, 2)}
              </pre>
            </div>
          )}

          {/* 可选：修改变量（merge 语义） */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs text-[#a1a1aa]">修改变量（可选）</label>
              <div className="flex items-center gap-0.5 bg-[#27272a] rounded-md p-0.5">
                <button
                  type="button"
                  onClick={() => setVarsMode('none')}
                  className={`px-2 py-0.5 text-[10px] rounded transition-colors border-0 cursor-pointer ${
                    varsMode === 'none' ? 'bg-[#52525b] text-[#fafafa]' : 'bg-transparent text-[#a1a1aa] hover:text-[#fafafa]'
                  }`}
                >
                  不改
                </button>
                <button
                  type="button"
                  onClick={() => setVarsMode('json')}
                  className={`px-2 py-0.5 text-[10px] rounded transition-colors border-0 cursor-pointer ${
                    varsMode === 'json' ? 'bg-[#52525b] text-[#fafafa]' : 'bg-transparent text-[#a1a1aa] hover:text-[#fafafa]'
                  }`}
                >
                  JSON 编辑
                </button>
              </div>
            </div>
            {varsMode === 'json' ? (
              <textarea
                value={varsText}
                onChange={(e) => setVarsText(e.target.value)}
                placeholder='{"input": {"q": "修改后的值"}}'
                rows={6}
                className="w-full px-3 py-2 text-xs border border-[#27272a] bg-[#121214] text-[#fafafa] rounded-md focus:outline-none focus:border-[#1E5EFF] resize-y font-mono"
              />
            ) : (
              <div className="text-[10px] text-[#71717a]">不修改变量，仅退回并重跑下游节点。</div>
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}

export default RewindModal

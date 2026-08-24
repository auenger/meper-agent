/**
 * TaskDetailPage — 任务详情全屏 view（替代 TaskDetailDrawer）。
 *
 * 借鉴 silieco（packages/views/issues/components/issue-detail.tsx）的全页面双栏理念：
 * 左主轴（错误 → 审批信息 → 流程图 → 执行时间线）+ 右可折叠属性栏（基本信息 /
 * 输入参数 / 产物）+ sticky header（状态 + 操作栏）。studio 无 react-router，故用
 * main 区条件覆盖（由 App 在 activeTaskDetail 有值时渲染），复刻 openWorkflow 模式。
 *
 * 自包含：内部持有 taskDetail / agent / workflow / users 查询（queryKey 与 TaskBoard
 * 一致，命中 RQ 缓存不重复请求）+ intervene / remove mutation + handler。props 精简。
 * mode='full' 显示操作栏（取消/继续/通过/驳回/重试/删除）；mode='view' 仅查看，操作栏隐藏。
 *
 * 复用既有子组件：TaskFlowGraph / TaskFlowTimeline / TaskOutputFiles / DataView /
 * ApprovalView。审批视图变量解析、审批弹窗逻辑与旧 TaskDetailDrawer 一致。
 */
import { useState, useMemo, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronLeft, Ban, RotateCcw, Trash2, Check, X, AlertTriangle, PanelRight, ChevronDown, Workflow, Undo2,
} from 'lucide-react'
import {
  tasksApi, taskKeys,
  type TaskDetail, type CommentValue, type WorkflowRegistryEntry,
} from '../../services/tasks-api'
import { agentApi, agentKeys } from '../../services/agent-api'
import { userApi } from '../../services/user-api'
import { useAuthStore } from '../../stores/auth-store'
import { TASK_STATUS_STYLES } from '../../constants/task-status'
import { Button, Modal, Spin } from '../ui'
import { toast } from '../ui/toast'
import { confirmDialog } from '../ui/confirm'
import { TaskOutputFiles } from './TaskOutputFiles'
import { TaskFlowTimeline } from './TaskFlowTimeline'
import { TaskFlowGraph } from './TaskFlowGraph'
import { RewindModal } from './RewindModal'
import { APPROVAL_ACCENT } from './TaskBoardCard'
import { DataView, DataViewEnhanceProvider } from './DataView'
import { ApprovalView } from '../approval-view/ApprovalView'
import type { ApprovalViewConfig } from '../approval-view/types'

export interface TaskDetailPageProps {
  taskId: string
  /** full=完整操作（操作栏可见）；view=仅查看（操作栏隐藏） */
  mode: 'full' | 'view'
  onBack: () => void
  theme?: 'light' | 'dark'
}

// comment 空判：text 模式空字符串时省略（后端默认归一化为 ""），其余（含 json）透传
function isCommentEmpty(c: CommentValue | undefined): boolean {
  return !c || (typeof c === 'string' && !c) || (typeof c === 'object' && c.type === 'text' && !c.value)
}

export function TaskDetailPage({ taskId, mode, onBack, theme = 'dark' }: TaskDetailPageProps) {
  const qc = useQueryClient()
  const full = mode === 'full'
  const permissions = useAuthStore((s) => s.user?.permissions ?? [])
  const canReadUsers = permissions.includes('user:read')

  /* ─── 查询（queryKey 与 TaskBoard 一致，共享 RQ 缓存） ─── */
  const { data: taskDetail, isLoading } = useQuery({
    queryKey: taskKeys.detail(taskId),
    queryFn: () => tasksApi.get(taskId),
    // 刷新由 WebSocket task_status 事件 invalidate 驱动（use-task-realtime），不轮询。
  })
  const { data: agentListData } = useQuery({
    queryKey: agentKeys.lists(),
    queryFn: () => agentApi.list({ page_size: 100, status: 'all' }),
    staleTime: 60_000,
  })
  const { data: wfData } = useQuery({
    queryKey: taskKeys.workflows(),
    queryFn: () => tasksApi.listWorkflows(),
    staleTime: 60_000,
  })
  const { data: usersData } = useQuery({
    queryKey: ['users', { page: 1, page_size: 100 }],
    queryFn: async () => (await userApi.list({ page: 1, page_size: 100 })).data,
    enabled: canReadUsers,
    staleTime: 60_000,
  })

  /* ─── 派生映射 ─── */
  const agentNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const a of agentListData?.items ?? []) map[a.id] = a.name
    return map
  }, [agentListData])
  const workflowNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const wf of (wfData?.items ?? []) as WorkflowRegistryEntry[]) {
      if (wf.name) {
        map[wf._id] = wf.name
        map[wf.workflow_id] = wf.name
      }
    }
    return map
  }, [wfData])
  const resolveTemplateId = useCallback((maybeRegistryId: string): string => {
    if (!maybeRegistryId) return ''
    const entry = (wfData?.items ?? []).find(
      (wf) => wf._id === maybeRegistryId || wf.workflow_id === maybeRegistryId,
    )
    return entry?.workflow_id ?? maybeRegistryId
  }, [wfData])
  const creatorNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const u of usersData?.items ?? []) map[u.id] = u.username
    return map
  }, [usersData])
  const creatorLabel = useCallback((created_by: string, created_by_type?: string) => {
    if (!created_by) return created_by_type === 'system' ? '系统' : '—'
    if (created_by_type === 'user') return creatorNameMap[created_by] ?? created_by
    return created_by
  }, [creatorNameMap])

  /* ─── Mutations（取消/重试/审批/resume/删除） ─── */
  const intervene = useMutation({
    mutationFn: (vars: { action: string; version: number; comment?: CommentValue }) =>
      tasksApi.intervene(taskId, { action: vars.action, version: vars.version, comment: vars.comment }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
      qc.invalidateQueries({ queryKey: taskKeys.detail(taskId) })
    },
  })
  const removeTask = useMutation({
    mutationFn: () => tasksApi.remove(taskId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
      onBack()
    },
  })

  /* ─── 操作 handler（仅 full 模式触发） ─── */
  const handleCancel = useCallback(async () => {
    if (!taskDetail) return
    const ok = await confirmDialog({
      title: `取消任务「${taskDetail.id.slice(-8)}」？`,
      description: '任务将被中止，无法恢复。',
      okText: '取消任务', danger: true,
    })
    if (ok) intervene.mutate({ action: 'cancel', version: taskDetail.version })
  }, [taskDetail, intervene])
  const handleRetry = useCallback(async () => {
    if (!taskDetail) return
    const ok = await confirmDialog({
      title: `重试任务「${taskDetail.id.slice(-8)}」？`,
      description: '任务将重置为待执行并重新开始执行。',
      okText: '重试',
    })
    if (ok) intervene.mutate({ action: 'retry', version: taskDetail.version })
  }, [taskDetail, intervene])
  const handleResume = useCallback(() => {
    if (!taskDetail) return
    intervene.mutate({ action: 'resume', version: taskDetail.version })
  }, [taskDetail, intervene])
  const handleDelete = useCallback(async () => {
    if (!taskDetail) return
    const ok = await confirmDialog({
      title: `删除任务「${taskDetail.id.slice(-8)}」？`,
      description: '此操作不可恢复，任务及其时间线将被永久删除。',
      okText: '删除', danger: true,
    })
    if (ok) removeTask.mutate()
  }, [taskDetail, removeTask])

  /* ─── 审批弹窗 ─── */
  const [approvalAction, setApprovalAction] = useState<'approve' | 'reject' | null>(null)
  const [approvalComment, setApprovalComment] = useState('')
  const [approvalCommentMode, setApprovalCommentMode] = useState<'text' | 'json'>('text')
  const openApproval = useCallback((action: 'approve' | 'reject') => {
    setApprovalAction(action); setApprovalComment(''); setApprovalCommentMode('text')
  }, [])
  const closeApproval = useCallback(() => {
    if (intervene.isPending) return
    setApprovalAction(null); setApprovalComment(''); setApprovalCommentMode('text')
  }, [intervene.isPending])
  const submitApproval = useCallback(() => {
    if (!approvalAction || !taskDetail || intervene.isPending) return
    let comment: CommentValue
    if (approvalCommentMode === 'json') {
      const trimmed = approvalComment.trim()
      if (!trimmed) {
        comment = { type: 'json', value: '' }
      } else {
        try {
          comment = { type: 'json', value: JSON.parse(trimmed) }
        } catch {
          toast.error('JSON 格式错误，请检查输入')
          return
        }
      }
    } else {
      comment = { type: 'text', value: approvalComment }
    }
    intervene.mutate({
      action: approvalAction, version: taskDetail.version,
      comment: isCommentEmpty(comment) ? undefined : comment,
    })
    setApprovalAction(null); setApprovalComment(''); setApprovalCommentMode('text')
  }, [approvalAction, approvalComment, approvalCommentMode, taskDetail, intervene])

  /* ─── 右侧栏折叠 ─── */
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  /* ─── 退回重跑弹窗 ─── */
  const [rewindOpen, setRewindOpen] = useState(false)
  const [basicOpen, setBasicOpen] = useState(true)
  const [inputOpen, setInputOpen] = useState(true)
  const [outputOpen, setOutputOpen] = useState(true)
  const [graphOpen, setGraphOpen] = useState(true)

  const status = taskDetail?.status
  const humanOptions = (status === 'waiting_human' && taskDetail?.checkpoint?.human_context?.options)
    ? (taskDetail.checkpoint.human_context.options as string[]).filter(Boolean) : []
  const useResume = status === 'waiting_human' && humanOptions.length === 0
  const interveneLoading = intervene.isPending

  return (
    <DataViewEnhanceProvider agentNameMap={agentNameMap}>
      <div className="flex flex-col flex-1 min-h-0 bg-[#121214]">
        {/* ── Header（sticky）── */}
        <div className="flex items-center gap-3 border-b border-[#27272a] px-5 py-3 shrink-0">
          <button
            onClick={onBack}
            className="p-1.5 rounded-lg text-[#a1a1aa] hover:text-[#fafafa] hover:bg-[#18181b] transition cursor-pointer shrink-0"
            title="返回"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <div className="min-w-0 flex-1">
            <div className="text-[10px] text-indigo-400 font-bold uppercase tracking-wider font-mono">任务详情</div>
            <h3 className="text-sm font-bold text-[#fafafa] truncate mt-0.5">
              {taskDetail ? (workflowNameMap[taskDetail.workflow_id] ?? taskDetail.workflow_id) : '加载中…'}
            </h3>
          </div>
          {taskDetail && status && (
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium border shrink-0"
              style={{
                color: TASK_STATUS_STYLES[status].color,
                background: TASK_STATUS_STYLES[status].bg,
                borderColor: `${TASK_STATUS_STYLES[status].accent}33`,
              }}
            >
              {TASK_STATUS_STYLES[status].label}
            </span>
          )}
          {/* 操作栏（仅 full 模式） */}
          {full && taskDetail && status && (
            <div className="flex items-center gap-2 shrink-0 ml-2">
              {status === 'running' && (
                <Button danger size="small" icon={<Ban className="w-3.5 h-3.5" />} onClick={handleCancel} loading={interveneLoading}>取消</Button>
              )}
              {status === 'waiting_human' && useResume && (
                <Button type="primary" size="small" icon={<RotateCcw className="w-3.5 h-3.5" />} onClick={handleResume} loading={interveneLoading}>继续</Button>
              )}
              {status === 'waiting_human' && !useResume && (
                <>
                  <Button type="primary" size="small" icon={<Check className="w-3.5 h-3.5" />} onClick={() => openApproval('approve')} loading={interveneLoading}
                    className="!bg-[#8B5CF6] !border-[#8B5CF6] hover:!bg-[#7c4fe0]">通过</Button>
                  <Button danger size="small" icon={<X className="w-3.5 h-3.5" />} onClick={() => openApproval('reject')} loading={interveneLoading}>驳回</Button>
                </>
              )}
              {/* 退回重跑：无论有无 human options 都可用（对齐 frontend 老版） */}
              {status === 'waiting_human' && (
                <Button size="small" icon={<Undo2 className="w-3.5 h-3.5" />} onClick={() => setRewindOpen(true)} loading={interveneLoading}>退回重跑</Button>
              )}
              {status === 'failed' && (
                <Button type="primary" size="small" icon={<RotateCcw className="w-3.5 h-3.5" />} onClick={handleRetry} loading={interveneLoading}>重试</Button>
              )}
              {(status === 'completed' || status === 'failed' || status === 'cancelled') && (
                <Button danger size="small" icon={<Trash2 className="w-3.5 h-3.5" />} onClick={handleDelete} loading={removeTask.isPending}>删除</Button>
              )}
            </div>
          )}
          <button
            onClick={() => setSidebarCollapsed((v) => !v)}
            className={`p-1.5 rounded-lg transition cursor-pointer shrink-0 ${sidebarCollapsed ? 'text-[#71717a] hover:text-[#fafafa]' : 'text-[#a78bfa] hover:bg-[#18181b]'}`}
            title={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
          >
            <PanelRight className="w-4 h-4" />
          </button>
        </div>

        {/* ── Body 双栏 ── */}
        <div className="flex flex-1 min-h-0">
          {/* 左主区：执行流程主轴 */}
          <div className="flex-1 overflow-y-auto scrollbar-custom">
            {isLoading ? (
              <div className="flex items-center justify-center py-20"><Spin size="large" /></div>
            ) : !taskDetail ? (
              <div className="flex items-center justify-center py-20 text-sm text-[#71717a]">任务不存在或已删除</div>
            ) : (
              <div className="max-w-4xl mx-auto p-6 space-y-6">
                {/* 错误提示（failed 时置顶醒目） */}
                {taskDetail.error && (
                  <div className="p-3 rounded-lg border border-rose-500/30 bg-rose-500/5 flex gap-2">
                    <AlertTriangle className="w-3.5 h-3.5 text-rose-400 shrink-0 mt-0.5" />
                    <div className="min-w-0">
                      <div className="text-xs font-medium text-rose-400">执行错误</div>
                      <div className="text-[11px] text-[#d4d4d8] mt-0.5 break-all">
                        [{taskDetail.error.error_code}] {taskDetail.error.error_message}
                      </div>
                      {(taskDetail.error.node_id || taskDetail.error.node_type) && (
                        <div className="text-[10px] text-[#71717a] mt-1 font-mono">
                          {taskDetail.error.node_type ?? '—'} · {taskDetail.error.node_id ?? '—'}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* 审批信息（waiting_human 时，含 ApprovalView） */}
                {taskDetail.status === 'waiting_human' && taskDetail.checkpoint && (
                  <section>
                    <SectionTitle>
                      <AlertTriangle className="w-3.5 h-3.5 inline mr-1 -mt-0.5" style={{ color: APPROVAL_ACCENT }} />
                      审批信息
                    </SectionTitle>
                    <div className="space-y-2.5">
                      {taskDetail.checkpoint.human_context?.view?.sections?.length ? (
                        <ApprovalView
                          view={taskDetail.checkpoint.human_context.view as ApprovalViewConfig}
                          variables={taskDetail.variables ?? {}}
                          taskId={taskDetail.id}
                        />
                      ) : (
                        <>
                          {taskDetail.checkpoint.human_context?.title && (
                            <InfoRow label="审批标题" value={String(taskDetail.checkpoint.human_context.title)} />
                          )}
                          {taskDetail.checkpoint.human_context?.description && (
                            <div>
                              <div className="text-xs text-[#a1a1aa] mb-1">审批描述</div>
                              <div className="text-xs text-[#d4d4d8] bg-[#09090b] rounded-lg p-3 border border-[#27272a] whitespace-pre-wrap">
                                {String(taskDetail.checkpoint.human_context.description)}
                              </div>
                            </div>
                          )}
                        </>
                      )}
                      {taskDetail.checkpoint.timeout_deadline && (
                        <InfoRow label="超时截止" value={`${formatDateTime(taskDetail.checkpoint.timeout_deadline)} (${taskDetail.checkpoint.timeout_action})`} />
                      )}
                      {(() => {
                        const pausedNode = taskDetail.checkpoint.paused_at_node
                        const upstreamVars = Object.entries(taskDetail.variables ?? {})
                          .filter(([key]) => key !== 'input' && key !== pausedNode)
                        if (upstreamVars.length === 0) return null
                        return (
                          <div>
                            <div className="text-xs text-[#a1a1aa] mb-1">上游节点输出</div>
                            <div className="max-h-48 overflow-y-auto space-y-2 scrollbar-custom">
                              {upstreamVars.map(([key, value]) => (
                                <div key={key}>
                                  <div className="text-[11px] text-[#71717a] font-medium mb-0.5">{key}</div>
                                  <DataView value={value} context="approval_upstream" showRaw={false} />
                                </div>
                              ))}
                            </div>
                          </div>
                        )
                      })()}
                    </div>
                  </section>
                )}

                {/* 流程图（可折叠 card） */}
                <section className={`rounded-lg border overflow-hidden ${
                  theme === 'dark' ? 'border-[#27272a] bg-[#18181b]' : 'border-slate-200 bg-white'
                }`}>
                  <button
                    type="button"
                    onClick={() => setGraphOpen((v) => !v)}
                    className={`flex items-center justify-between w-full px-4 py-2.5 cursor-pointer transition-colors ${
                      theme === 'dark' ? 'hover:bg-[#1f1f23]' : 'hover:bg-slate-100'
                    }`}
                  >
                    <span className={`flex items-center gap-2 text-sm font-medium ${
                      theme === 'dark' ? 'text-[#fafafa]' : 'text-slate-800'
                    }`}>
                      <Workflow className={`w-4 h-4 ${theme === 'dark' ? 'text-[#a1a1aa]' : 'text-slate-500'}`} /> 流程图
                    </span>
                    <ChevronDown className={`w-4 h-4 transition-transform ${graphOpen ? '' : '-rotate-90'} ${
                      theme === 'dark' ? 'text-[#71717a]' : 'text-slate-400'
                    }`} />
                  </button>
                  {graphOpen && (
                    <div className={`p-3 border-t ${theme === 'dark' ? 'border-[#27272a]' : 'border-slate-200'}`}>
                      <TaskFlowGraph task={taskDetail} theme={theme} resolveTemplateId={resolveTemplateId} />
                    </div>
                  )}
                </section>

                {/* 执行时间线 */}
                <section>
                  <SectionTitle>
                    时间线 <span className="font-normal text-[#71717a]">({taskDetail.timeline?.length ?? 0})</span>
                  </SectionTitle>
                  <TaskFlowTimeline task={taskDetail} theme={theme} resolveTemplateId={resolveTemplateId} />
                </section>
              </div>
            )}
          </div>

          {/* 右侧栏：可折叠属性 */}
          {!sidebarCollapsed && taskDetail && (
            <aside className="w-[360px] border-l border-[#27272a] overflow-y-auto scrollbar-custom shrink-0">
              <div className="p-4 space-y-4">
                {/* 基本信息 */}
                <CollapsibleSection title="基本信息" open={basicOpen} onToggle={() => setBasicOpen((v) => !v)}>
                  <div className="space-y-2.5">
                    <InfoRow label="ID" value={taskDetail.id} mono />
                    <InfoRow label="工作流" value={workflowNameMap[taskDetail.workflow_id] ?? taskDetail.workflow_id} />
                    <InfoRow label="创建者" value={creatorLabel(taskDetail.created_by, taskDetail.created_by_type)} />
                    <InfoRow label="版本" value={`v${taskDetail.version}`} />
                    <InfoRow label="创建时间" value={formatDateTime(taskDetail.created_at)} />
                    <InfoRow label="更新时间" value={formatDateTime(taskDetail.updated_at)} />
                    {taskDetail.total_tokens ? (
                      <InfoRow label="Token 消耗" value={`${taskDetail.total_tokens.toLocaleString()} tokens`} />
                    ) : null}
                  </div>
                </CollapsibleSection>

                {/* 输入参数 */}
                <CollapsibleSection title="输入参数" open={inputOpen} onToggle={() => setInputOpen((v) => !v)}>
                  {Object.keys(taskDetail.input ?? {}).length === 0 ? (
                    <div className="text-xs text-[#71717a] italic">无</div>
                  ) : (
                    <DataView value={taskDetail.input} context="task_input" showRaw={false} />
                  )}
                </CollapsibleSection>

                {/* 产物 */}
                {(taskDetail.status === 'completed' || taskDetail.status === 'running') && (
                  <CollapsibleSection title="产物" open={outputOpen} onToggle={() => setOutputOpen((v) => !v)}>
                    <TaskOutputFiles taskId={taskDetail.id} />
                  </CollapsibleSection>
                )}
              </div>
            </aside>
          )}
        </div>
      </div>

      {/* ── 退回重跑弹窗（仅 full 模式触发；waiting_human 时操作栏可见入口） ── */}
      {taskDetail && (
        <RewindModal
          task={taskDetail}
          open={rewindOpen}
          onClose={() => setRewindOpen(false)}
          resolveTemplateId={resolveTemplateId}
        />
      )}

      {/* ── 审批弹窗（仅 full 模式触发） ── */}
      <Modal
        title={approvalAction === 'approve' ? '通过审批' : '驳回审批'}
        open={!!approvalAction}
        onOk={submitApproval}
        onCancel={closeApproval}
        okText={approvalAction === 'approve' ? '通过' : '驳回'}
        cancelText="取消"
        okButtonProps={{ disabled: interveneLoading }}
      >
        <div className="flex flex-col gap-3 py-2">
          <p className="text-xs text-[#d4d4d8]">
            {approvalAction === 'approve'
              ? `确定通过任务「${taskDetail?.id.slice(-8) ?? ''}」并继续执行吗？`
              : `确定驳回任务「${taskDetail?.id.slice(-8) ?? ''}」吗？任务将被标记为失败。`}
          </p>
          {(() => {
            const ctx = taskDetail?.checkpoint?.human_context
            if (!ctx) return null
            return (
              <div className="border border-[#27272a] rounded-lg p-3 bg-[#18181b]">
                {ctx.title && <div className="text-sm font-medium text-[#fafafa] mb-1.5">{ctx.title}</div>}
                {ctx.description && <div className="text-xs text-[#a1a1aa] whitespace-pre-wrap break-words leading-relaxed">{ctx.description}</div>}
                {!ctx.title && !ctx.description && <div className="text-xs text-[#71717a] italic">该审批节点未配置说明</div>}
              </div>
            )
          })()}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs text-[#a1a1aa]">comment（可选）</label>
              <div className="flex items-center gap-0.5 bg-[#27272a] rounded-md p-0.5">
                <button type="button" onClick={() => setApprovalCommentMode('text')}
                  className={`px-2 py-0.5 text-[10px] rounded transition-colors border-0 cursor-pointer ${approvalCommentMode === 'text' ? 'bg-[#52525b] text-[#fafafa]' : 'bg-transparent text-[#a1a1aa] hover:text-[#fafafa]'}`}>文本</button>
                <button type="button" onClick={() => setApprovalCommentMode('json')}
                  className={`px-2 py-0.5 text-[10px] rounded transition-colors border-0 cursor-pointer ${approvalCommentMode === 'json' ? 'bg-[#52525b] text-[#fafafa]' : 'bg-transparent text-[#a1a1aa] hover:text-[#fafafa]'}`}>JSON</button>
              </div>
            </div>
            <textarea
              value={approvalComment}
              onChange={(e) => setApprovalComment(e.target.value)}
              placeholder={approvalCommentMode === 'json' ? '{"score": 8, "note": "ok"}' : approvalAction === 'reject' ? '建议填写驳回原因（可选）' : '审批意见（可选）'}
              rows={3}
              className={`w-full px-3 py-2 text-xs border border-[#27272a] bg-[#121214] text-[#fafafa] rounded-md focus:outline-none focus:border-[#1E5EFF] resize-none ${approvalCommentMode === 'json' ? 'font-mono' : ''}`}
            />
          </div>
        </div>
      </Modal>
    </DataViewEnhanceProvider>
  )
}

/* ─── 子组件 ─── */

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="text-[11px] font-medium text-[#71717a] uppercase tracking-wider mb-3 flex items-center gap-1">
      {children}
    </h4>
  )
}

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs text-[#a1a1aa] shrink-0">{label}</span>
      <span className={`text-xs text-[#d4d4d8] text-right max-w-[230px] truncate ${mono ? 'font-mono' : ''}`}>
        {value}
      </span>
    </div>
  )
}

/** 右侧栏可折叠 section —— 借鉴 silieco 的渐进式披露。 */
function CollapsibleSection({
  title, open, onToggle, children,
}: {
  title: string
  open: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-1 text-[11px] font-medium text-[#a1a1aa] uppercase tracking-wider mb-2 hover:text-[#fafafa] transition-colors cursor-pointer"
      >
        <ChevronDown className={`w-3 h-3 transition-transform ${open ? '' : '-rotate-90'}`} />
        {title}
      </button>
      {open && <div className="pl-1">{children}</div>}
    </div>
  )
}

/* ─── 工具函数 ─── */

function formatDateTime(iso: string): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default TaskDetailPage

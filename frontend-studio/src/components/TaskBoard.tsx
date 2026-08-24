/**
 * Studio TaskBoard — 任务协作看板（6 列按后端状态精确分桶）。
 *
 * 对齐旧版 frontend/src/pages/tasks-page.tsx，适配 studio 暗色风 + 原生组件。
 *
 * 设计要点：
 * - 6 列状态分桶：pending / running / waiting_human / completed / failed / cancelled
 * - 列表查询：useQueries 并发发起 6 次 list（按 status，page=1, page_size=50）
 *   刷新由 WebSocket 的 task_status 事件 invalidate 驱动（见 use-task-realtime），不做定时轮询
 * - 节点进度：仅对 running / waiting_human 两列拉详情（staleTime 3s，上限 20 个）
 * - 详情查询与详情 Drawer 共用 taskKeys.detail(id) 缓存
 * - 搜索改为纯前端过滤（task.id / workflow_id includes）
 * - workflowNameMap：始终拉一次 workflow 列表，把 workflow_id 解析成可读名称
 */
import { useState, useMemo, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient, useQueries } from '@tanstack/react-query'
import { Plus, Search, Loader2, Bolt, AlertTriangle, CheckCircle, XCircle } from 'lucide-react'
import {
  tasksApi, taskKeys,
  type TaskSummary, type TaskStatusValue, type TaskDetail, type NodeProgress,
  type CommentValue, BOARD_STATUSES, parseNodeProgress, type WorkflowRegistryEntry,
} from '../services/tasks-api'
import { userApi } from '../services/user-api'
import { useAuthStore } from '../stores/auth-store'
import { TASK_STATUS_STYLES } from '../constants/task-status'
import { TaskBoardCard } from './task/TaskBoardCard'
import { RewindModal } from './task/RewindModal'
import { Modal, Select, Button } from './ui'
import { confirmDialog } from './ui/confirm'
import { getErrorMessage } from '../lib/api-client'

export function TaskBoard({ theme = 'dark', onOpenTaskDetail }: { theme?: 'light' | 'dark'; onOpenTaskDetail?: (taskId: string) => void }) {
  const qc = useQueryClient()
  const permissions = useAuthStore((s) => s.user?.permissions ?? [])
  const canReadUsers = permissions.includes('user:read')

  /* ─── Search state（纯前端过滤） ─── */
  const [searchInput, setSearchInput] = useState('')
  const debouncedSearch = useDebouncedValue(searchInput.toLowerCase(), 200)

  /* ─── Create modal state ───
     entryId 存选中的 workflow_registry 条目 _id（wfr_...，唯一稳定）；
     提交时再由 resolveTemplateId 解析成真正的模板 workflow_id（wf_...）传给后端，
     避免 task.workflow_id 存成 wfr_ 导致引擎找不到模板（engine 直接按 _id 查 workflows 集合）。 */
  const [createOpen, setCreateOpen] = useState(false)
  const [newTask, setNewTask] = useState<{ entryId: string; input: string }>({ entryId: '', input: '' })
  const [actionError, setActionError] = useState<string | null>(null)

  /* ─── Rewind modal state（看板卡片「退回重跑」打开的共享弹窗） ─── */
  const [rewindTask, setRewindTask] = useState<TaskSummary | null>(null)

  /* ─── 6 列并发列表查询（按 status 分桶，刷新由 WS task_status 事件驱动） ─── */
  const boardQueries = useQueries({
    queries: BOARD_STATUSES.map((status) => ({
      queryKey: taskKeys.list({ status, page: 1, page_size: 50 }),
      queryFn: () => tasksApi.list({ status, page: 1, page_size: 50 }),
    })),
  })

  const tasksByStatus = useMemo(() => {
    const map: Record<TaskStatusValue, TaskSummary[]> = {
      pending: [], running: [], waiting_human: [], completed: [], failed: [], cancelled: [],
    }
    BOARD_STATUSES.forEach((status, idx) => {
      const result = boardQueries[idx]
      map[status] = (result?.data?.items ?? []) as TaskSummary[]
    })
    return map
  }, [boardQueries])

  const boardLoading = boardQueries.some((q) => q.isLoading)

  /* ─── 收集 active 任务，批量拉详情（staleTime 3s，上限 20 个） ─── */
  const MAX_PROGRESS_TASKS = 20
  const ACTIVE_STATUSES: TaskStatusValue[] = ['running', 'waiting_human']
  const activeTasksForDetail = useMemo(() => {
    const collected: TaskSummary[] = []
    for (const status of ACTIVE_STATUSES) {
      for (const task of tasksByStatus[status]) {
        if (collected.length >= MAX_PROGRESS_TASKS) break
        collected.push(task)
      }
      if (collected.length >= MAX_PROGRESS_TASKS) break
    }
    return collected
  }, [tasksByStatus])

  const activeDetailQueries = useQueries({
    queries: activeTasksForDetail.map((task) => ({
      queryKey: taskKeys.detail(task.id),
      queryFn: () => tasksApi.get(task.id),
      staleTime: 3_000,
    })),
  })

  const progressMap = useMemo(() => {
    const map: Record<string, NodeProgress | null | undefined> = {}
    activeTasksForDetail.forEach((task, idx) => {
      const detail = activeDetailQueries[idx]?.data
      if (detail) map[task.id] = parseNodeProgress(detail.timeline)
    })
    return map
  }, [activeTasksForDetail, activeDetailQueries])

  /* ─── Workflow 名称映射（看板卡片用，始终启用） ───
     registry 条目有两个 id 字段，职责不同：
       _id         → workflow_registry 条目 id（wfr_...），用于 UI 选项 value（唯一稳定）
       workflow_id → 工作流模板 id（wf_...），后端 task.workflow_id 应存这个
     新建任务统一用 entry._id 选择、提交时 resolveTemplateId 转 workflow_id，保证
     task.workflow_id 一定是模板 id，引擎 run_and_persist 才能按 _id 查到 workflows 文档。
     历史任务可能存了 wfr_（旧 bug），workflowNameMap/resolveTemplateId 双向兼容。 */
  const { data: wfData } = useQuery({
    queryKey: taskKeys.workflows(),
    queryFn: () => tasksApi.listWorkflows(),
    staleTime: 60_000,
  })
  const workflowNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const wf of (wfData?.items ?? []) as WorkflowRegistryEntry[]) {
      if (wf.name) {
        map[wf._id] = wf.name          // registry entry id (wfr_...) — 兼容历史任务存 wfr_ 的情况
        map[wf.workflow_id] = wf.name   // 模板 id (wf_...) — 新任务正常存这个
      }
    }
    return map
  }, [wfData])
  // 把 registry entry _id (wfr_) 解析为模板 workflow_id (wf_)；传入已是 wf_ 则原样返回。
  const resolveTemplateId = useCallback((maybeRegistryId: string): string => {
    if (!maybeRegistryId) return ''
    const entry = (wfData?.items ?? []).find(
      (wf) => wf._id === maybeRegistryId || wf.workflow_id === maybeRegistryId,
    )
    return entry?.workflow_id ?? maybeRegistryId
  }, [wfData])
  const workflows: WorkflowRegistryEntry[] = wfData?.items ?? []

  /* ─── Creator 名称映射（user:read 权限时拉一次 users 列表，把 created_by 的 user id 解析成可读 username） ─── */
  const { data: usersData } = useQuery({
    queryKey: ['users', { page: 1, page_size: 100 }],
    queryFn: async () => (await userApi.list({ page: 1, page_size: 100 })).data,
    enabled: canReadUsers,
    staleTime: 60_000,
  })
  const creatorNameMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const u of usersData?.items ?? []) map[u.id] = u.username
    return map
  }, [usersData])
  const creatorLabel = useCallback((created_by: string, created_by_type?: string) => {
    if (!created_by) return created_by_type === 'system' ? '系统' : '—'
    // user 类型优先用 username，agent/system 类型保留原样
    if (created_by_type === 'user') return creatorNameMap[created_by] ?? created_by
    return created_by
  }, [creatorNameMap])

  /* ─── Mutations ─── */
  const createTask = useMutation({
    mutationFn: (vars: { entryId: string; input: string }) =>
      // 把 registry entry _id (wfr_) 解析成模板 workflow_id (wf_) 再传给后端
      tasksApi.create({
        workflow_id: resolveTemplateId(vars.entryId),
        input: vars.input ? { prompt: vars.input } : {},
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
      setCreateOpen(false)
      setNewTask({ entryId: '', input: '' })
    },
    onError: (e) => setActionError(`创建任务失败：${getErrorMessage(e)}`),
  })

  const intervene = useMutation({
    mutationFn: (vars: { taskId: string; action: string; version: number; comment?: CommentValue }) =>
      tasksApi.intervene(vars.taskId, { action: vars.action, version: vars.version, comment: vars.comment }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
    },
    onError: (e) => setActionError(`操作失败：${getErrorMessage(e)}`),
  })

  const removeTask = useMutation({
    mutationFn: (taskId: string) => tasksApi.remove(taskId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: taskKeys.lists() })
    },
    onError: (e) => setActionError(`删除失败：${getErrorMessage(e)}`),
  })

  /* ─── Actions ─── */
  const handleCreateSubmit = () => {
    if (!newTask.entryId) {
      setActionError('请先选择一个已发布的工作流')
      return
    }
    setActionError(null)
    createTask.mutate(newTask)
  }

  const handleCancel = useCallback(async (task: TaskSummary | TaskDetail) => {
    const ok = await confirmDialog({
      title: `取消任务「${task.id.slice(-8)}」？`,
      description: '任务将被中止，无法恢复。',
      okText: '取消任务',
      danger: true,
    })
    if (!ok) return
    intervene.mutate({ taskId: task.id, action: 'cancel', version: task.version })
  }, [intervene])

  const handleRetry = useCallback(async (task: TaskSummary | TaskDetail) => {
    const ok = await confirmDialog({
      title: `重试任务「${task.id.slice(-8)}」？`,
      description: '任务将重置为待执行并重新开始执行。',
      okText: '重试',
    })
    if (!ok) return
    intervene.mutate({ taskId: task.id, action: 'retry', version: task.version })
  }, [intervene])

  // comment 空判：text 模式空字符串时省略（后端默认归一化为 ""），其余（含 json）透传
  const isCommentEmpty = (c: CommentValue | undefined): boolean =>
    !c || (typeof c === 'string' && !c) || (typeof c === 'object' && c.type === 'text' && !c.value)

  const handleApprovalSubmit = useCallback((task: TaskSummary | TaskDetail, action: 'approve' | 'reject', comment: CommentValue) => {
    intervene.mutate({ taskId: task.id, action, version: task.version, comment: isCommentEmpty(comment) ? undefined : comment })
  }, [intervene])

  const handleDelete = useCallback(async (task: TaskSummary | TaskDetail) => {
    const ok = await confirmDialog({
      title: `删除任务「${task.id.slice(-8)}」？`,
      description: '此操作不可恢复，任务及其时间线将被永久删除。',
      okText: '删除',
      danger: true,
    })
    if (!ok) return
    removeTask.mutate(task.id)
  }, [removeTask])

  const handleCardClick = useCallback((task: TaskSummary) => {
    onOpenTaskDetail?.(task.id)
  }, [onOpenTaskDetail])

  /* ─── 过滤函数（前端 includes 匹配） ─── */
  const filterTasks = useCallback((tasks: TaskSummary[]) => {
    if (!debouncedSearch) return tasks
    return tasks.filter(
      (task) =>
        task.id.toLowerCase().includes(debouncedSearch) ||
        task.workflow_id.toLowerCase().includes(debouncedSearch),
    )
  }, [debouncedSearch])

  /* ─── 顶部紧凑统计卡（4 张） ─── */
  const statCards = [
    { label: '运行中', value: tasksByStatus.running.length, color: '#3B82F6', icon: Bolt },
    { label: '等待人工', value: tasksByStatus.waiting_human.length, color: '#8B5CF6', icon: AlertTriangle },
    { label: '已完成', value: tasksByStatus.completed.length, color: '#10B981', icon: CheckCircle },
    { label: '已失败', value: tasksByStatus.failed.length + tasksByStatus.cancelled.length, color: '#EF4444', icon: XCircle },
  ]

  return (
    <div className="flex flex-col h-full min-h-0 gap-4 font-sans">
      {actionError && (
        <div className="px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-400 text-[11px] flex items-center justify-between shrink-0">
          <span>⚠ {actionError}</span>
          <button onClick={() => setActionError(null)} className="text-rose-400 hover:text-rose-300 cursor-pointer">✕</button>
        </div>
      )}

      {/* ─── 顶部统计条 ─── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 shrink-0">
        {statCards.map((s) => (
          <div
            key={s.label}
            className="bg-[#18181b] rounded-xl border border-[#27272a] p-3 flex items-center gap-3"
          >
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
              style={{ background: `${s.color}1A`, color: s.color }}
            >
              <s.icon className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="text-xl font-semibold text-[#fafafa] leading-tight font-mono">{s.value}</div>
              <div className="text-[11px] text-[#a1a1aa]">{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* ─── 工具栏：搜索 + 新建 ─── */}
      <div className="flex items-center justify-between gap-3 shrink-0">
        <div className="relative flex-1 max-w-xs">
          <Search className="w-3.5 h-3.5 pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#71717a]" />
          <input
            type="text"
            placeholder="搜索任务 ID 或工作流..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="w-full pl-9 pr-3 h-8 text-xs bg-[#121214] border border-[#27272a] rounded-lg text-[#fafafa] focus:outline-none focus:border-[#1E5EFF] transition font-medium"
          />
        </div>
        <Button
          type="primary"
          icon={<Plus className="w-3.5 h-3.5" />}
          onClick={() => { setActionError(null); setCreateOpen(true) }}
        >
          新建任务
        </Button>
      </div>

      {/* ─── 6 列看板（横向滚动，列内垂直滚动） ─── */}
      <div className="flex gap-4 overflow-x-auto pb-2 flex-1 min-h-0 scrollbar-custom">
        {boardLoading ? (
          <div className="flex items-center justify-center w-full py-20 text-[#71717a] text-xs">
            <Loader2 className="w-4 h-4 animate-spin mr-2" /> 加载任务…
          </div>
        ) : (
          BOARD_STATUSES.map((status) => {
            const style = TASK_STATUS_STYLES[status]
            const tasks = filterTasks(tasksByStatus[status])
            return (
              <div
                key={status}
                className="min-w-[280px] w-[280px] flex-shrink-0 flex flex-col bg-[#18181b] rounded-xl border border-[#27272a]"
              >
                {/* 列头 */}
                <div
                  className="flex items-center justify-between gap-2 px-3 py-2.5 rounded-t-xl border-b border-[#27272a] shrink-0"
                  style={{ background: style.bg }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className={`w-2 h-2 rounded-full shrink-0 ${style.pulse ? 'animate-pulse' : ''}`}
                      style={{ backgroundColor: style.accent }}
                    />
                    <span className="text-xs font-medium truncate" style={{ color: style.color }}>
                      {style.label}
                    </span>
                  </div>
                  <span
                    className="px-1.5 h-5 min-w-[20px] flex items-center justify-center rounded-full text-[10px] font-mono font-bold text-white"
                    style={{ backgroundColor: style.accent }}
                  >
                    {tasks.length}
                  </span>
                </div>

                {/* 卡片区 */}
                <div className="flex-1 overflow-y-auto p-2 space-y-2 min-h-[120px] scrollbar-custom">
                  {tasks.length === 0 ? (
                    <div className="flex items-center justify-center py-8 text-[10px] text-[#71717a]">
                      暂无
                    </div>
                  ) : (
                    tasks.map((task) => (
                      <TaskBoardCard
                        key={task.id}
                        task={task}
                        progress={progressMap[task.id]}
                        workflowName={workflowNameMap[task.workflow_id]}
                        creatorName={creatorLabel(task.created_by, task.created_by_type)}
                        onClick={() => handleCardClick(task)}
                        onCancel={handleCancel}
                        onRetry={handleRetry}
                        onDelete={handleDelete}
                        onApprovalSubmit={handleApprovalSubmit}
                        onRewind={setRewindTask}
                        interveneLoading={intervene.isPending}
                        deleteLoading={removeTask.isPending}
                      />
                    ))
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* ─── 退回重跑 Modal（卡片「退回重跑」打开，与详情页共享组件） ─── */}
      {rewindTask && (
        <RewindModal
          task={rewindTask}
          open={!!rewindTask}
          onClose={() => setRewindTask(null)}
          resolveTemplateId={resolveTemplateId}
        />
      )}

      {/* ─── 新建任务 Modal ─── */}
      <Modal
        title="新建任务"
        open={createOpen}
        onOk={handleCreateSubmit}
        onCancel={() => setCreateOpen(false)}
        okText="创建"
        cancelText="取消"
        okButtonProps={{ disabled: !newTask.entryId || createTask.isPending }}
        width={520}
      >
        <div className="flex flex-col gap-4 py-2">
          <div className="space-y-1.5">
            <label className="text-xs text-[#a1a1aa] font-semibold">
              工作流（已发布） <span className="text-rose-400">*</span>
            </label>
            <Select
              value={newTask.entryId || null}
              onChange={(v) => setNewTask({ ...newTask, entryId: v ?? '' })}
              placeholder="— 选择工作流 —"
              options={workflows.map((wf) => ({
                value: wf._id,
                label: `${wf.name} (v${wf.version})${wf.has_human_node ? ' · 含人工节点' : ''}`,
              }))}
            />
            {workflows.length === 0 && (
              <p className="text-[10px] text-amber-400">暂无已发布工作流，请先在工作流页面发布一个。</p>
            )}
          </div>

          <div className="space-y-1.5">
            <label className="text-xs text-[#a1a1aa] font-semibold">输入 (prompt，可选)</label>
            <textarea
              value={newTask.input}
              onChange={(e) => setNewTask({ ...newTask, input: e.target.value })}
              placeholder="任务的初始输入…"
              rows={3}
              className="w-full p-3 bg-[#121214] border border-[#27272a] rounded-lg text-[#fafafa] text-xs focus:outline-none focus:border-[#1E5EFF] transition resize-y"
            />
          </div>
        </div>
      </Modal>
    </div>
  )
}

/* ─── debounce hook（纯前端，避免引入额外依赖） ─── */
function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useMemo(() => {
    const id = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(id)
  }, [value, delay])
  return debounced
}

/**
 * TaskBoardCard — 看板单卡片组件（任务协作看板中的任务单元）。
 *
 * 布局（对齐旧版 frontend/src/components/task-board-card.tsx，适配 studio 暗色风）：
 * - 左侧 3px 状态色条（style.accent）
 * - 顶部：工作流名称 + 触发时间戳（主标题）+ 状态 Tag
 * - 次行：任务 ID（灰色 mono，技术标识）
 * - 进度区（仅 progress 存在且 running/waiting_human 时渲染）：
 *   「已执行 N 个节点」+ 4px 高脉动条
 *   - running 时动画推移
 *   - waiting_human 时静态满格（紫色）
 * - 等待审批（仅 waiting_human 且非终态）：「通过/驳回」两枚快捷按钮
 * - 底部：耗时 · 创建时间
 * - hover 时右上角浮出操作按钮（取消/重试/删除，按状态条件渲染）
 * - 整卡 onClick → 打开详情抽屉
 */
import { useState } from 'react'
import { Check, X, RotateCcw, Trash2, Ban, Hash, User, Undo2 } from 'lucide-react'
import type { TaskSummary, NodeProgress, TaskStatusValue, CommentValue } from '../../services/tasks-api'
import { TASK_STATUS_STYLES } from '../../constants/task-status'
import { Button, Modal, Tag, Tooltip } from '../ui'
import { toast } from '../ui/toast'

/** 审批主色（通过按钮），与详情抽屉保持一致 */
export const APPROVAL_ACCENT = '#8B5CF6'

export interface TaskBoardCardProps {
  task: TaskSummary
  progress?: NodeProgress | null
  workflowName?: string
  /** 创作者展示名（已由父级解析为可读 username） */
  creatorName?: string
  onClick?: () => void
  onCancel?: (task: TaskSummary) => void
  onRetry?: (task: TaskSummary) => void
  onDelete?: (task: TaskSummary) => void
  /** 看板内嵌审批：交由父级统一调 interveneMutation */
  onApprovalSubmit?: (task: TaskSummary, action: 'approve' | 'reject', comment: CommentValue) => void
  /** 退回重跑：上抛给父级打开共享 RewindModal（卡片零逻辑） */
  onRewind?: (task: TaskSummary) => void
  interveneLoading?: boolean
  deleteLoading?: boolean
}

/** 耗时：start → end（end 为空时取 now），毫秒差转人类可读 */
function formatDuration(startIso: string, endIso?: string | null): string {
  if (!startIso) return '-'
  const start = new Date(startIso).getTime()
  const end = endIso ? new Date(endIso).getTime() : Date.now()
  const diff = end - start
  if (diff < 0) return '-'
  if (diff < 1000) return '<1s'
  if (diff < 60000) return `${Math.floor(diff / 1000)}s`
  return `${Math.floor(diff / 60000)}m ${Math.floor((diff % 60000) / 1000)}s`
}

/** 创建时间转「x 分钟前」相对时间 */
function formatTime(iso: string): string {
  if (!iso) return '-'
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 0) return '刚刚'
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  return `${days} 天前`
}

/** 创建时间 → 紧凑时间戳（MM-DD HH:mm），用作卡片主标题区分同工作流的多次任务 */
function formatTimestamp(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return ''
  }
}

export function TaskBoardCard({
  task,
  progress,
  workflowName,
  creatorName,
  onClick,
  onCancel,
  onRetry,
  onDelete,
  onApprovalSubmit,
  onRewind,
  interveneLoading = false,
  deleteLoading = false,
}: TaskBoardCardProps) {
  const status: TaskStatusValue = task.status
  const style = TASK_STATUS_STYLES[status]
  const isRunning = status === 'running'
  const isWaitingHuman = status === 'waiting_human'
  const showProgress = !!progress && (isRunning || isWaitingHuman)
  const isTerminal = status === 'completed' || status === 'failed' || status === 'cancelled'

  // 看板内嵌审批弹窗
  const [approvalModalOpen, setApprovalModalOpen] = useState(false)
  const [approvalAction, setApprovalAction] = useState<'approve' | 'reject' | null>(null)
  const [approvalComment, setApprovalComment] = useState('')
  // comment 输入模式：text 纯文本 / json 结构化（显式选择，避免隐式脆弱解析）
  const [commentMode, setCommentMode] = useState<'text' | 'json'>('text')

  const openApproval = (action: 'approve' | 'reject') => {
    setApprovalAction(action)
    setApprovalComment('')
    setCommentMode('text')
    setApprovalModalOpen(true)
  }

  const closeApproval = () => {
    if (interveneLoading) return
    setApprovalModalOpen(false)
    setApprovalAction(null)
    setApprovalComment('')
    setCommentMode('text')
  }

  const submitApproval = () => {
    if (!approvalAction || interveneLoading) return
    let comment: CommentValue
    if (commentMode === 'json') {
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
    onApprovalSubmit?.(task, approvalAction, comment)
    // 不立即关闭：等父级 mutation 完成后任务状态变更，卡片消失；失败时 loading 复位可重试。
  }

  return (
    <div
      onClick={onClick}
      className="group relative bg-[#18181b] rounded-lg border border-[#27272a] hover:border-[#52525b] hover:shadow-lg transition-all duration-150 cursor-pointer overflow-hidden"
    >
      {/* 左侧 3px 状态色条 */}
      <div className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ backgroundColor: style.accent }} />

      {/* hover 时右上角浮出的操作按钮组 */}
      <div className="absolute right-1.5 top-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-150 flex items-center gap-0.5 bg-[#09090b]/90 backdrop-blur-sm rounded-md px-0.5 py-0.5 shadow-sm border border-[#27272a] z-10">
        {isRunning && onCancel && (
          <Tooltip title="取消">
            <button
              onClick={(e) => { e.stopPropagation(); onCancel(task) }}
              disabled={interveneLoading}
              className="border-0 bg-transparent w-5 h-5 flex items-center justify-center rounded text-[#a1a1aa] hover:text-[#EF4444] hover:bg-[#27272a] transition-colors disabled:opacity-40"
            >
              <Ban className="w-3 h-3" />
            </button>
          </Tooltip>
        )}
        {status === 'failed' && onRetry && (
          <Tooltip title="重试">
            <button
              onClick={(e) => { e.stopPropagation(); onRetry(task) }}
              disabled={interveneLoading}
              className="border-0 bg-transparent w-5 h-5 flex items-center justify-center rounded text-[#a1a1aa] hover:text-[#1E5EFF] hover:bg-[#27272a] transition-colors disabled:opacity-40"
            >
              <RotateCcw className="w-3 h-3" />
            </button>
          </Tooltip>
        )}
        {isTerminal && onDelete && (
          <Tooltip title="删除">
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(task) }}
              disabled={deleteLoading}
              className="border-0 bg-transparent w-5 h-5 flex items-center justify-center rounded text-[#a1a1aa] hover:text-[#EF4444] hover:bg-[#27272a] transition-colors disabled:opacity-40"
            >
              <Trash2 className="w-3 h-3" />
            </button>
          </Tooltip>
        )}
      </div>

      <div className="pl-3 pr-3 py-2.5">
        {/* 顶部：工作流名称 + 触发时间戳（主标题）+ 状态 Tag */}
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <div className="flex items-center gap-1.5 min-w-0 flex-1">
            <span className="text-xs font-medium text-[#fafafa] truncate">
              {workflowName ?? task.workflow_id}
            </span>
            <span className="text-[10px] text-[#71717a] font-mono shrink-0">
              {formatTimestamp(task.created_at)}
            </span>
          </div>
          <Tag
            color={style.color}
            className="!inline-flex !items-center !gap-1 !px-1.5 !py-0 !text-[10px] !rounded !shrink-0"
          >
            {style.label}
          </Tag>
        </div>

        {/* 次行：任务 ID（灰色 mono，技术标识） */}
        <div className="text-[11px] text-[#71717a] truncate mb-1.5 flex items-center gap-1">
          <Hash className="w-3 h-3 text-[#52525b] shrink-0" />
          <span className="truncate font-mono">{task.id.slice(-12)}</span>
        </div>

        {/* 创作者（灰色小字，解析为可读 name） */}
        {creatorName && (
          <div className="text-[11px] text-[#a1a1aa] truncate mb-2 flex items-center gap-1">
            <User className="w-3 h-3 text-[#71717a] shrink-0" />
            <span className="truncate">{creatorName}</span>
          </div>
        )}

        {/* 进度区（仅 progress 存在且 running/waiting_human 时渲染） */}
        {showProgress && progress && (
          <div className="mb-2">
            <div className="text-[10px] text-[#71717a] mb-1 flex items-center gap-1">
              <span>已执行 {progress.completedCount} 个节点</span>
              {progress.currentNodeType && <span className="text-[#52525b]">·</span>}
              {progress.currentNodeType && (
                <span className="truncate" style={{ color: style.color }}>
                  {progress.currentNodeType}
                </span>
              )}
            </div>
            {/* 4px 高脉动条：running 动画推移，waiting_human 静态满格 */}
            <div className="h-1 w-full rounded-full overflow-hidden" style={{ background: `${style.accent}22` }}>
              {isRunning ? (
                <div
                  className="h-full rounded-full"
                  style={{
                    width: '40%',
                    backgroundColor: style.accent,
                    animation: 'task-pulse-slide 1.6s ease-in-out infinite',
                  }}
                />
              ) : (
                <div className="h-full rounded-full" style={{ width: '100%', backgroundColor: style.accent }} />
              )}
            </div>
          </div>
        )}

        {/* 等待审批快捷入口（仅 waiting_human 且非终态时显示） */}
        {isWaitingHuman && !isTerminal && onApprovalSubmit && (
          <div className="flex items-center gap-1.5 mb-2" onClick={(e) => e.stopPropagation()}>
            <Button
              type="primary"
              size="small"
              icon={<Check className="w-3 h-3" />}
              onClick={() => openApproval('approve')}
              loading={interveneLoading}
              className="!bg-[#8B5CF6] !border-[#8B5CF6] hover:!bg-[#7c4fe0]"
            >
              通过
            </Button>
            <Button
              danger
              size="small"
              icon={<X className="w-3 h-3" />}
              onClick={() => openApproval('reject')}
              loading={interveneLoading}
            >
              驳回
            </Button>
            {onRewind && (
              <Button
                size="small"
                icon={<Undo2 className="w-3 h-3" />}
                onClick={() => onRewind(task)}
                disabled={interveneLoading}
              >
                退回重跑
              </Button>
            )}
          </div>
        )}

        {/* 底部：耗时 · 创建时间 */}
        <div className="flex items-center justify-between text-[10px] text-[#71717a]">
          <span>{formatDuration(task.created_at, task.updated_at)}</span>
          <span>{formatTime(task.created_at)}</span>
        </div>
      </div>

      {/* 脉动动画 keyframes（注入一次即可，重复无害） */}
      <style>{`
        @keyframes task-pulse-slide {
          0% { transform: translateX(-100%); width: 40%; }
          50% { transform: translateX(150%); width: 60%; }
          100% { transform: translateX(300%); width: 40%; }
        }
      `}</style>

      {/* 审批弹窗：看板内嵌审批，提交后由父级统一调 interveneMutation */}
      <Modal
        title={approvalAction === 'reject' ? '驳回审批' : '通过审批'}
        open={approvalModalOpen}
        onCancel={closeApproval}
        onOk={submitApproval}
        okText="确认"
        cancelText="取消"
        okButtonProps={{ disabled: interveneLoading }}
      >
        <div className="py-2 space-y-3">
          {/* 审核信息：审批人在同一界面看到要审什么，再做通过/驳回决策 */}
          {(() => {
            const ctx = task.checkpoint?.human_context
            if (!ctx) return null
            return (
              <div className="border border-[#27272a] rounded-lg p-3 bg-[#18181b]">
                {ctx.title && (
                  <div className="text-sm font-medium text-[#fafafa] mb-1.5">{ctx.title}</div>
                )}
                {ctx.description && (
                  <div className="text-xs text-[#a1a1aa] whitespace-pre-wrap break-words leading-relaxed">
                    {ctx.description}
                  </div>
                )}
                {!ctx.title && !ctx.description && (
                  <div className="text-xs text-[#71717a] italic">该审批节点未配置说明</div>
                )}
              </div>
            )
          })()}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs text-[#a1a1aa]">comment（可选）</label>
            <div className="flex items-center gap-0.5 bg-[#27272a] rounded-md p-0.5">
              <button
                type="button"
                onClick={() => setCommentMode('text')}
                className={`px-2 py-0.5 text-[10px] rounded transition-colors border-0 cursor-pointer ${
                  commentMode === 'text'
                    ? 'bg-[#52525b] text-[#fafafa]'
                    : 'bg-transparent text-[#a1a1aa] hover:text-[#fafafa]'
                }`}
              >
                文本
              </button>
              <button
                type="button"
                onClick={() => setCommentMode('json')}
                className={`px-2 py-0.5 text-[10px] rounded transition-colors border-0 cursor-pointer ${
                  commentMode === 'json'
                    ? 'bg-[#52525b] text-[#fafafa]'
                    : 'bg-transparent text-[#a1a1aa] hover:text-[#fafafa]'
                }`}
              >
                JSON
              </button>
            </div>
          </div>
          <textarea
            value={approvalComment}
            onChange={(e) => setApprovalComment(e.target.value)}
            placeholder={
              commentMode === 'json'
                ? '{"score": 8, "note": "ok"}'
                : approvalAction === 'reject'
                  ? '建议填写驳回原因（可选）'
                  : '审批意见（可选）'
            }
            rows={3}
            className={`w-full px-3 py-2 text-xs border border-[#27272a] bg-[#121214] text-[#fafafa] rounded-md focus:outline-none focus:border-[#1E5EFF] resize-none ${commentMode === 'json' ? 'font-mono' : ''}`}
          />
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default TaskBoardCard

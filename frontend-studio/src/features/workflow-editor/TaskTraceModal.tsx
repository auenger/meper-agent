/**
 * TaskTraceModal — 工作流执行追踪弹窗。
 *
 * 从 WorkflowDesigner 抽出，供详情页与卡片列表页共用：接收一个 TaskDetail，
 * 展示任务元信息 + timeline 事件流。状态文案/颜色由 TASK_STATUS_META 定义。
 */
import { X, Clock } from 'lucide-react'
import { Tag } from '../../components/ui'
import { type TaskDetail, type TimelineEvent } from '../../services/tasks-api'

const TASK_STATUS_META: Record<string, { text: string; color: string }> = {
  pending: { text: '排队中', color: '#64748B' },
  running: { text: '运行中', color: '#3B82F6' },
  waiting_human: { text: '等待人工', color: '#F97316' },
  completed: { text: '已完成', color: '#10B981' },
  failed: { text: '失败', color: '#EF4444' },
  cancelled: { text: '已取消', color: '#94A3B8' },
}

function formatTime(ts: string): string {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return ts
  }
}

export function TaskTraceModal({ task, onClose }: { task: TaskDetail | null; onClose: () => void }) {
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

export default TaskTraceModal

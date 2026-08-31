/**
 * WorkflowTaskInlineCard — dispatch_workflow 工具结果在消息流内的单行状态卡。
 *
 * 工作流状态/详情不属于聊天内容：节点轨迹、输入输出、审批操作全部收进右上角
 * 任务看板（WorkflowBoard），这里只保留一行「状态图标 + Tag + 工作流名 + 时间 +
 * 查看入口」，整行可点开看板。轻量轮询 tasksApi.get 只为拿最新 status/时间，
 * 终态即停；失败降级显示派发时状态，不阻断对话。
 */
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { Tag } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { tasksApi, type TaskStatusValue } from '../api/tasks'
import {
  fmtTime,
  RUNNING_STATUSES,
  STATUS_TAG,
  type TaskCreated,
} from './WorkflowTaskCard'

const POLL_MS = Number(import.meta.env.VITE_TASK_POLL_INTERVAL_MS) || 15000

/** 状态 → 行首图标（运行态转圈、失败红叉、其余绿勾）。 */
function statusIcon(status: TaskStatusValue, running: boolean) {
  if (running) return <ClockCircleOutlined spin />
  if (status === 'failed' || status === 'cancelled') return <CloseCircleOutlined />
  return <CheckCircleOutlined />
}

export function WorkflowTaskInlineCard({
  created,
  onOpenBoard,
}: {
  created: TaskCreated
  /** 点击整行 → ChatView 打开任务看板并定位该任务。 */
  onOpenBoard?: (taskId: string) => void
}) {
  const [status, setStatus] = useState<TaskStatusValue>(created.status ?? 'pending')
  const [time, setTime] = useState('')
  const [stale, setStale] = useState(false) // 状态刷新失败（无权限/网络），展示已有状态

  const load = useCallback(async () => {
    try {
      const task = await tasksApi.get(created.task_id)
      setStatus(task.status)
      setTime(task.updated_at || task.created_at || '')
      setStale(false)
    } catch {
      setStale(true)
    }
  }, [created.task_id])

  useEffect(() => {
    void load()
  }, [load])

  // 运行态轮询（终态自动停，与 WorkflowTaskCard 同节奏）
  useEffect(() => {
    if (!RUNNING_STATUSES.includes(status)) return
    const id = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(id)
  }, [status, load])

  const running = RUNNING_STATUSES.includes(status)
  const tag = STATUS_TAG[status]

  return (
    <button
      type="button"
      className={`workflow-task-inline workflow-task-inline-${status}`}
      onClick={() => onOpenBoard?.(created.task_id)}
      title="在工作流任务看板中查看详情"
    >
      <span className="workflow-task-inline-icon">{statusIcon(status, running)}</span>
      <Tag color={tag.color} style={{ margin: 0 }}>
        {tag.label}
      </Tag>
      <span className="workflow-task-inline-name">
        {created.workflow_name || '工作流任务'}
      </span>
      {stale ? (
        <ExclamationCircleOutlined
          className="workflow-task-inline-warn"
          title="状态刷新失败，点击查看详情"
        />
      ) : null}
      {time ? <span className="workflow-task-inline-meta">{fmtTime(time)}</span> : null}
      <span className="workflow-task-inline-action">
        {status === 'waiting_human' ? '去处理' : '查看'}
        <RightOutlined />
      </span>
    </button>
  )
}

/**
 * WorkflowBoard — 聊天 header 右上角「工作流」按钮打开的任务看板 Drawer。
 *
 * 后端没有「按 session 查 tasks」的接口，任务列表从消息流派生
 * （collectSessionTasks 解析 dispatch_workflow 的 task_created 结果），切会话随
 * messages 重置。按时间倒序（最新在上），每项复用 WorkflowTaskCard——轮询、审批
 * 干预、节点轨迹、产物下载全部在卡内自带。Drawer 用 destroyOnHidden：关闭即卸载
 * 卡片，不留隐藏轮询；从消息内一行卡点「查看」进来时按 focusTaskId 定位并展开。
 */
import { Drawer, Empty } from 'antd'
import { useEffect, useMemo, useRef } from 'react'

import type { ChatMessage } from '../types'
import {
  parseTaskCreated,
  WorkflowTaskCard,
  type TaskCreated,
} from './WorkflowTaskCard'

/** 从消息流提取本会话的工作流任务（dispatch_workflow 的 task_created 结果）。
 * 按消息出现顺序收录，按 task_id 去重（防御上游重复收集）。 */
export function collectSessionTasks(messages: ChatMessage[]): TaskCreated[] {
  const seen = new Set<string>()
  const tasks: TaskCreated[] = []
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue
    for (const block of msg.content) {
      if (block.type !== 'tool' || block.tool.name !== 'dispatch_workflow') continue
      const created = parseTaskCreated(block.tool.result)
      if (created && !seen.has(created.task_id)) {
        seen.add(created.task_id)
        tasks.push(created)
      }
    }
  }
  return tasks
}

interface WorkflowBoardProps {
  tasks: TaskCreated[]
  open: boolean
  onClose: () => void
  /** 从消息内一行状态卡点「查看」进入时，定位滚动并展开该任务。 */
  focusTaskId?: string | null
}

export function WorkflowBoard({ tasks, open, onClose, focusTaskId }: WorkflowBoardProps) {
  const bodyRef = useRef<HTMLDivElement | null>(null)
  // 最新在上；仅一个任务或 focus 项默认展开，多任务默认折叠保持看板清爽
  const sorted = useMemo(() => [...tasks].reverse(), [tasks])

  // 打开且带 focusTaskId 时滚动定位到对应任务（Drawer 内容挂载后一帧再查）
  useEffect(() => {
    if (!open || !focusTaskId) return
    const raf = requestAnimationFrame(() => {
      bodyRef.current
        ?.querySelector(`[data-task-id="${focusTaskId}"]`)
        ?.scrollIntoView({ block: 'start', behavior: 'smooth' })
    })
    return () => cancelAnimationFrame(raf)
  }, [open, focusTaskId])

  return (
    <Drawer
      title="工作流任务"
      width="min(520px, 92vw)"
      open={open}
      onClose={onClose}
      className="workflow-board-drawer"
      destroyOnHidden
    >
      <div className="workflow-board-list" ref={bodyRef}>
        {sorted.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本会话暂无工作流任务" />
        ) : (
          sorted.map((task) => (
            <div key={task.task_id} data-task-id={task.task_id}>
              <WorkflowTaskCard
                created={task}
                defaultExpanded={sorted.length === 1 || task.task_id === focusTaskId}
              />
            </div>
          ))
        )}
      </div>
    </Drawer>
  )
}

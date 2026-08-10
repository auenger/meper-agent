/**
 * useWorkflowExecution — 执行工作流的可复用 hook。
 *
 * 从 WorkflowDesigner 抽出，供详情页与卡片列表页共用同一套运行逻辑：
 *   execute → 校验 published → 读 Start 节点 output_variables
 *           → 有输入则开输入弹窗，无则直接 runWorkflow
 *   runWorkflow → POST /tasks + 每 2s 轮询 GET /tasks/{id}（终态停）
 *
 * 调用方负责渲染 <TaskTraceModal> 与 <ExecuteInputDialog>，绑定本 hook 返回的状态。
 * execute 的 detail 形参：详情页可传入已载入的 workflowDetail（零请求）；
 * 列表页省略时走 queryClient.fetchQuery（命中详情页缓存则不发请求）。
 */
import { useState, useCallback, useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { tasksApi, type TaskDetail, type TaskStatusValue } from '../services/tasks-api'
import { workflowsApi, workflowKeys, type WorkflowDetail } from '../services/workflows-api'
import type { VariableDefinition } from '../features/workflow-editor/utils/variable-types'
import { toast } from '../components/ui/toast'
import { getErrorMessage } from '../lib/api-client'

/** 终态任务状态（轮询在这些状态停止） */
const TERMINAL_TASK_STATUSES: TaskStatusValue[] = ['completed', 'failed', 'cancelled']

export function useWorkflowExecution() {
  const queryClient = useQueryClient()
  const [executing, setExecuting] = useState(false)
  const [trackingTask, setTrackingTask] = useState<TaskDetail | null>(null)
  const [traceOpen, setTraceOpen] = useState(false)
  const [execInputOpen, setExecInputOpen] = useState(false)
  const [execInputVariables, setExecInputVariables] = useState<VariableDefinition[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // 输入弹窗打开时记录待执行的工作流，提交后据此 runWorkflow
  const pendingWorkflowIdRef = useRef<string | null>(null)

  // 卸载时清理轮询定时器
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

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
    async (workflowId: string, input: Record<string, unknown>) => {
      setExecuting(true)
      setTraceOpen(true)
      try {
        const task = await tasksApi.create({ workflow_id: workflowId, input })
        setTrackingTask(task)
        pollTask(task.id)
      } catch (err) {
        setExecuting(false)
        toast.error(getErrorMessage(err, '创建执行任务失败'), { duration: 0 })
      }
    },
    [pollTask],
  )

  /** 执行入口：校验状态 + 判断是否需要先收集输入参数 */
  const execute = useCallback(
    async (workflowId: string, detail?: WorkflowDetail) => {
      let wf = detail
      if (!wf) {
        try {
          wf = await queryClient.fetchQuery({
            queryKey: workflowKeys.detail(workflowId),
            queryFn: () => workflowsApi.get(workflowId),
          })
        } catch (err) {
          toast.error(getErrorMessage(err, '加载工作流详情失败'), { duration: 0 })
          return
        }
      }
      if (wf.status !== 'published') {
        toast.error('只有已发布的工作流才能执行，请先发布', { duration: 0 })
        return
      }
      // 开始节点是否声明了输入变量 —— 有则先弹窗收集，无则直接执行
      const startNode = (wf.nodes ?? []).find((n) => n.type === 'start')
      const outputVars = (startNode?.config?.output_variables as VariableDefinition[] | undefined) ?? []
      if (Array.isArray(outputVars) && outputVars.length > 0) {
        pendingWorkflowIdRef.current = workflowId
        setExecInputVariables(outputVars)
        setExecInputOpen(true)
        return
      }
      runWorkflow(workflowId, {})
    },
    [queryClient, runWorkflow],
  )

  /** 执行参数弹窗提交 */
  const submitInput = useCallback(
    (values: Record<string, unknown>) => {
      setExecInputOpen(false)
      const id = pendingWorkflowIdRef.current
      if (id) runWorkflow(id, values)
    },
    [runWorkflow],
  )

  const cancelInput = useCallback(() => setExecInputOpen(false), [])

  const closeTrace = useCallback(() => {
    setTraceOpen(false)
    stopPolling()
  }, [stopPolling])

  return {
    executing,
    trackingTask,
    traceOpen,
    execInputOpen,
    execInputVariables,
    execute,
    submitInput,
    cancelInput,
    closeTrace,
  }
}

/**
 * WorkflowTaskCard — 工作流任务的完整可展开卡片（C 端）。
 *
 * 消息流内只留单行状态卡（WorkflowTaskInlineCard），本卡片渲染在右上角任务看板
 * （WorkflowBoard Drawer）里。外层用 AntD Collapse 包裹（对齐 ToolResult 的「工具调用」
 * 卡片样式）；只读 tool result 里的 task_id，自行 fetch 任务详情、工作流定义、轮询状态、
 * 发干预请求，不改 chat history。
 *
 * 节点名：timeline 事件的 data 只带 node_id/node_type（无 label），节点名必须查
 * GET /workflows/{id}.nodes[node_id].label；拉不到则回退到类型名（Agent 节点 等）。
 * 执行轨迹：按 node_id 分组成「阶段」，每节点一行（类型图标+名称+状态+时间），
 * 可展开看事件列表；agent 节点支持「查看执行详情」（懒加载 getNodeTimeline）。
 *
 * 实时：useEffect + setInterval，运行态按 POLL_MS 轮询（默认 15s，可配）、终态停止。
 * 降级：fetch 失败/无权限 → 展示首屏摘要 + 错误提示，不阻断对话。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  RollbackOutlined,
  StopOutlined,
  ToolOutlined,
  BranchesOutlined,
  UserOutlined,
  NodeIndexOutlined,
} from '@ant-design/icons'
import { App, Button, Collapse, Input, Modal, Radio, Tag, Typography } from 'antd'
import type { CSSProperties, ReactNode } from 'react'
import {
  tasksApi,
  type CommentValue,
  type NodeTimelineEntry,
  type TaskDetail,
  type TaskOutputFile,
  type TaskStatusValue,
  type TimelineEvent,
  type WorkflowNode,
} from '../api/tasks'
import { downloadUploadedFile } from '../api/chat'

const POLL_MS = Number(import.meta.env.VITE_TASK_POLL_INTERVAL_MS) || 15000
/** 运行态集合（含待执行/等待人工）——轮询继续的条件；WorkflowTaskInlineCard 复用。 */
export const RUNNING_STATUSES: TaskStatusValue[] = ['pending', 'running', 'waiting_human']

export interface TaskCreated {
  type: 'task_created'
  task_id: string
  workflow_id?: string
  workflow_name?: string
  workflow_description?: string
  status?: TaskStatusValue
  has_human_node?: boolean
  message?: string
}

/** 解析 tool result 文本为 TaskCreated；非 task_created 结构返回 null（调用方降级）。 */
export function parseTaskCreated(raw?: string): TaskCreated | null {
  if (!raw) return null
  const trimmed = raw.trim()
  if (!trimmed.startsWith('{')) return null
  try {
    const obj = JSON.parse(trimmed) as Record<string, unknown>
    if (obj?.type === 'task_created' && typeof obj.task_id === 'string') {
      return obj as unknown as TaskCreated
    }
  } catch {
    /* ignore */
  }
  return null
}

/** 任务状态 → Tag 配置；WorkflowTaskInlineCard 复用。 */
export const STATUS_TAG: Record<TaskStatusValue, { color: string; label: string }> = {
  pending: { color: 'orange', label: '待执行' },
  running: { color: 'processing', label: '执行中' },
  waiting_human: { color: 'purple', label: '等待人工' },
  completed: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '已失败' },
  cancelled: { color: 'default', label: '已取消' },
}

const ACTION_LABEL: Record<string, string> = {
  approve: '已通过',
  reject: '已驳回',
  skip: '已跳过',
  cancel: '已取消',
  retry: '已重新执行',
  resume: '已继续',
  rewind: '已退回重跑',
}

/** 节点类型 → 图标 / 中文标签（与 studio NODE_TYPE_LABEL 对齐）。 */
const NODE_TYPE_ICON: Record<string, ReactNode> = {
  start: <PlayCircleOutlined />,
  end: <StopOutlined />,
  agent: <RobotOutlined />,
  tool: <ToolOutlined />,
  gateway: <BranchesOutlined />,
  parallel: <NodeIndexOutlined />,
  human: <UserOutlined />,
}
const NODE_TYPE_LABEL: Record<string, string> = {
  start: '输入节点',
  end: '输出节点',
  agent: 'Agent 节点',
  tool: '工具节点',
  gateway: '网关节点',
  parallel: '并行节点',
  human: '人工审批节点',
}

type NodeState = 'completed' | 'executing' | 'failed' | 'waiting' | 'undecided' | 'pending'
const NODE_STATE_TAG: Record<NodeState, { color: string; label: string }> = {
  completed: { color: 'success', label: '已完成' },
  executing: { color: 'processing', label: '执行中' },
  failed: { color: 'error', label: '失败' },
  waiting: { color: 'purple', label: '审批中' },
  // human 节点暂停过、但任务未等决策就流转（如取消后恢复），无 approve/reject/skip 事件
  undecided: { color: 'default', label: '未决策' },
  pending: { color: 'default', label: '待执行' },
}

/** timeline event_type → 中文标签（节点事件展开后用）。 */
const EVENT_LABEL: Record<string, string> = {
  created: '任务创建',
  started: '开始执行',
  node_start: '节点开始',
  node_complete: '节点完成',
  node_failed: '节点失败',
  waiting_human: '等待审批',
  approve: '审批通过',
  skip: '审批跳过',
  reject: '审批驳回',
  cancel: '人工取消',
  resume: '人工恢复',
  rewind: '退回重跑',
  completed: '任务完成',
  failed: '任务失败',
  cancelled: '任务取消',
}

interface Stage {
  nodeId: string
  nodeType: string
  events: TimelineEvent[]
}

/** 扁平 timeline → 按 node_id 分组的阶段（顺序按节点首次出现）。 */
function buildStages(task: TaskDetail): Stage[] {
  const order: string[] = []
  const byNode = new Map<string, TimelineEvent[]>()
  const typeByNode = new Map<string, string>()
  for (const evt of task.timeline ?? []) {
    const nodeId = typeof evt.data?.node_id === 'string' ? (evt.data.node_id as string) : undefined
    if (!nodeId) continue
    if (!byNode.has(nodeId)) {
      byNode.set(nodeId, [])
      order.push(nodeId)
    }
    byNode.get(nodeId)!.push(evt)
    const nt = typeof evt.data?.node_type === 'string' ? (evt.data.node_type as string) : undefined
    if (nt) typeByNode.set(nodeId, nt)
  }
  return order.map((nodeId) => ({
    nodeId,
    nodeType: typeByNode.get(nodeId) ?? 'agent',
    events: byNode.get(nodeId)!,
  }))
}

/** 节点状态推导。human 节点特殊：暂停瞬间也会写 node_complete（不代表"完成"），
 * 必须按决策事件推导——approve/skip→完成、reject→失败、无决策且任务仍在等→审批中、
 * 无决策但任务已流转（取消后恢复等）→未决策。isWaiting=任务当前是否停在 waiting_human。 */
function nodeState(events: TimelineEvent[], isWaiting: boolean): NodeState {
  const types = events.map((e) => e.event_type)
  if (types.includes('node_failed') || types.includes('reject')) return 'failed'
  if (types.includes('waiting_human')) {
    if (types.includes('approve') || types.includes('skip')) return 'completed'
    return isWaiting ? 'waiting' : 'undecided'
  }
  if (types.includes('node_complete')) return 'completed'
  if (types.includes('node_start')) return 'executing'
  return 'pending'
}

/** iso 时间 → HH:mm:ss（本地时区）；WorkflowTaskInlineCard 复用。 */
export function fmtTime(iso?: string): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return iso
  }
}

const flex: CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }
const monoPre: CSSProperties = {
  fontSize: 11,
  margin: 0,
  maxHeight: 200,
  overflow: 'auto',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-all',
}

function JsonText({ value }: { value: unknown }) {
  let text: string
  try {
    text = typeof value === 'string' ? JSON.stringify(JSON.parse(value), null, 2) : JSON.stringify(value, null, 2)
  } catch {
    text = String(value)
  }
  if (!text || text === '{}' || text === 'null') return null
  return <pre style={monoPre}>{text}</pre>
}

export function WorkflowTaskCard({
  created,
  defaultExpanded = false,
}: {
  created: TaskCreated
  /** 看板多任务时默认折叠，仅定位项/单任务时展开。 */
  defaultExpanded?: boolean
}) {
  const { message } = App.useApp()
  const [task, setTask] = useState<TaskDetail | null>(null)
  const [nodeMap, setNodeMap] = useState<Map<string, WorkflowNode> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ status?: number; message?: string } | null>(null)
  const [acting, setActing] = useState(false)
  // 取消任务的 reason 弹窗；human 审批意见改为卡片内联输入（approve/reject 携带）
  const [cancelModal, setCancelModal] = useState(false)
  const [commentText, setCommentText] = useState('')
  // human 节点审批意见：通过时写入节点变量传给下游，驳回时进错误信息
  const [decisionComment, setDecisionComment] = useState('')
  // 退回重跑（rewind）：仅 waiting_human 可用，目标须为已完成节点（排除当前暂停节点）
  const [rewindOpen, setRewindOpen] = useState(false)
  const [rewindTarget, setRewindTarget] = useState('')
  const [outputs, setOutputs] = useState<TaskOutputFile[] | null>(null)
  const [expandedStages, setExpandedStages] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      const t = await tasksApi.get(created.task_id)
      setTask(t)
      setError(null)
    } catch (err) {
      setError(err as { status?: number; message?: string })
    } finally {
      setLoading(false)
    }
  }, [created.task_id])

  useEffect(() => {
    void load()
  }, [load])

  const status: TaskStatusValue = task?.status ?? created.status ?? 'pending'
  const isRunning = RUNNING_STATUSES.includes(status)

  // 运行态轮询
  useEffect(() => {
    if (!isRunning) return
    const id = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(id)
  }, [isRunning, load])

  // 工作流定义：task 到位后取一次，建 node_id → {label,type} 映射（拿不到则空，回退类型名）
  useEffect(() => {
    if (!task?.workflow_id) return
    let cancelled = false
    void tasksApi
      .getWorkflow(task.workflow_id)
      .then((wf) => {
        if (cancelled) return
        const m = new Map<string, WorkflowNode>()
        for (const n of wf.nodes ?? []) {
          if (n.node_id) m.set(n.node_id, n)
        }
        setNodeMap(m)
      })
      .catch(() => {
        /* 无权限/模板删除 → nodeMap 留空，渲染回退到类型名 */
      })
    return () => {
      cancelled = true
    }
  }, [task?.workflow_id])

  // 产物文件
  useEffect(() => {
    if (status !== 'completed' && status !== 'running') {
      setOutputs(null)
      return
    }
    let cancelled = false
    void tasksApi
      .listOutputs(created.task_id)
      .then((list) => !cancelled && setOutputs(list))
      .catch(() => !cancelled && setOutputs([]))
    return () => {
      cancelled = true
    }
  }, [created.task_id, status])

  const stages = useMemo<Stage[]>(() => (task ? buildStages(task) : []), [task])

  const run = useCallback(
    async (action: string, comment?: CommentValue, targetNodeId?: string) => {
      if (!task) return
      setActing(true)
      try {
        await tasksApi.intervene(created.task_id, {
          action,
          comment,
          version: task.version,
          target_node_id: targetNodeId,
        })
        message.success(ACTION_LABEL[action] ?? '操作成功')
        await load()
      } catch (err) {
        const e = err as { status?: number; message?: string }
        if (e.status === 409) {
          message.error('任务状态已变更，正在刷新')
          await load()
        } else {
          message.error(e.message ?? '操作失败')
        }
      } finally {
        setActing(false)
      }
    },
    [task, created.task_id, load, message],
  )

  const submitCancel = () => {
    const text = commentText.trim()
    void run('cancel', text ? { type: 'text', value: text } : undefined)
    setCancelModal(false)
    setCommentText('')
  }

  /** 审批意见 → intervene comment（空则不传）。 */
  const decisionCommentValue = (): CommentValue | undefined => {
    const text = decisionComment.trim()
    return text ? { type: 'text', value: text } : undefined
  }

  const submitRewind = () => {
    if (!rewindTarget) return
    void run('rewind', undefined, rewindTarget)
    setRewindOpen(false)
    setRewindTarget('')
  }

  const tag = STATUS_TAG[status]
  const ckpt = task?.checkpoint ?? null

  // 头部状态图标（对齐 ToolResult 的 statusIcon）
  const StatusIcon = isRunning ? (
    <ClockCircleOutlined spin />
  ) : status === 'failed' ? (
    <CloseCircleOutlined />
  ) : (
    <CheckCircleOutlined />
  )
  const toolRunCls = status === 'failed' ? 'error' : isRunning ? 'running' : 'complete'

  const toggleStage = (id: string) =>
    setExpandedStages((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <>
      <Collapse
        className={`workflow-task-card tool-run tool-run-${toolRunCls}`}
        size="small"
        ghost
        defaultActiveKey={defaultExpanded ? [created.task_id] : []}
        items={[
          {
            key: created.task_id,
            label: (
              <div className="tool-title" style={flex}>
                {StatusIcon}
                <Tag color={tag.color} style={{ margin: 0 }}>
                  {tag.label}
                </Tag>
                <strong style={{ fontSize: 13 }}>{created.workflow_name || '工作流任务'}</strong>
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {created.task_id}
                </Typography.Text>
              </div>
            ),
            children: (
              <div className="tool-details">
                {loading && !task ? <Typography.Text type="secondary">加载任务详情…</Typography.Text> : null}

                {error ? (
                  <div style={{ ...flex, color: '#d48806', fontSize: 12, marginBottom: 8 }}>
                    <ExclamationCircleOutlined />
                    <span>无法加载任务详情{error.message ? `：${error.message}` : ''}</span>
                    <Button type="link" size="small" onClick={() => void load()}>
                      重试
                    </Button>
                  </div>
                ) : null}

                {/* 人工审批区：问题 + 意见 + 决策按钮整合在一处（等待审批时）。
                    问题来自 checkpoint.human_context；评论通过时写入节点变量传给下游 */}
                {ckpt && status === 'waiting_human' ? (
                  <section
                    style={{
                      background: 'rgba(114,46,209,0.06)',
                      border: '1px solid rgba(114,46,209,0.3)',
                      borderRadius: 6,
                      padding: '10px 12px',
                      marginBottom: 8,
                    }}
                  >
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#722ed1' }}>
                      {ckpt.human_context?.title || '需要人工确认'}
                    </div>
                    {ckpt.human_context?.description ? (
                      <div
                        style={{
                          fontSize: 12,
                          color: '#531dab',
                          whiteSpace: 'pre-wrap',
                          marginTop: 4,
                        }}
                      >
                        {ckpt.human_context.description}
                      </div>
                    ) : null}
                    {(ckpt.human_context?.options ?? []).filter(Boolean).length ? (
                      <div style={{ ...flex, marginTop: 6 }}>
                        {(ckpt.human_context?.options ?? [])
                          .filter(Boolean)
                          .map((option) => (
                            <Tag key={option} style={{ margin: 0 }}>
                              {option}
                            </Tag>
                          ))}
                      </div>
                    ) : null}
                    <Input.TextArea
                      value={decisionComment}
                      onChange={(event) => setDecisionComment(event.target.value)}
                      placeholder="审批意见（可选）：通过时将传给后续节点"
                      autoSize={{ minRows: 1, maxRows: 4 }}
                      style={{ fontSize: 12, marginTop: 8 }}
                      disabled={acting}
                    />
                    <div style={{ ...flex, marginTop: 8 }}>
                      <Button
                        size="small"
                        type="primary"
                        loading={acting}
                        icon={<CheckCircleOutlined />}
                        onClick={() => void run('approve', decisionCommentValue())}
                      >
                        通过
                      </Button>
                      <Button
                        size="small"
                        danger
                        loading={acting}
                        icon={<CloseCircleOutlined />}
                        onClick={() => void run('reject', decisionCommentValue())}
                      >
                        驳回
                      </Button>
                      <Button size="small" loading={acting} onClick={() => void run('skip')}>
                        跳过
                      </Button>
                      <Button
                        size="small"
                        icon={<RollbackOutlined />}
                        disabled={acting}
                        onClick={() => setRewindOpen(true)}
                      >
                        退回重跑
                      </Button>
                    </div>
                  </section>
                ) : null}

                {task?.error ? (
                  <section style={{ marginBottom: 8 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      错误信息
                    </Typography.Text>
                    <div
                      style={{
                        background: 'rgba(255,77,79,0.08)',
                        border: '1px solid rgba(255,77,79,0.3)',
                        borderRadius: 6,
                        padding: 8,
                        fontSize: 12,
                        color: '#cf1322',
                        marginTop: 4,
                      }}
                    >
                      {task.error.error_code ? <div style={{ fontFamily: 'monospace' }}>[{task.error.error_code}]</div> : null}
                      <div style={{ whiteSpace: 'pre-wrap' }}>{task.error.error_message}</div>
                    </div>
                  </section>
                ) : null}

                {/* 任务级输入/输出 JSON 不再展示——执行轨迹按节点呈现数据，
                    任务级汇总属于冗余（节点事件 + agent trace 已覆盖） */}

                {/* 执行轨迹：按节点分组 */}
                {stages.length > 0 ? (
                  <section style={{ marginTop: 8 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      执行轨迹
                    </Typography.Text>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                      {stages.map((stage) => {
                        const st = nodeState(stage.events, status === 'waiting_human')
                        const nmeta = NODE_STATE_TAG[st]
                        const node = nodeMap?.get(stage.nodeId)
                        const nodeLabel = node?.label || NODE_TYPE_LABEL[stage.nodeType] || stage.nodeId
                        const isOpen = expandedStages.has(stage.nodeId)
                        return (
                          <div
                            key={stage.nodeId}
                            style={{ border: '1px solid #f0f0f0', borderRadius: 6, overflow: 'hidden' }}
                          >
                            <div
                              onClick={() => toggleStage(stage.nodeId)}
                              style={{ ...flex, padding: '6px 8px', cursor: 'pointer', userSelect: 'none' }}
                            >
                              <span style={{ color: '#8c8c8c' }}>
                                {NODE_TYPE_ICON[stage.nodeType] ?? <ToolOutlined />}
                              </span>
                              <span style={{ fontSize: 12, fontWeight: 500, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {nodeLabel}
                              </span>
                              <Tag color={nmeta.color} style={{ margin: 0 }}>
                                {nmeta.label}
                              </Tag>
                              <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                                {fmtTime(stage.events[0]?.timestamp)}
                              </Typography.Text>
                            </div>
                            {isOpen ? (
                              <div style={{ padding: '0 8px 8px', borderTop: '1px solid #f0f0f0' }}>
                                {/* human 节点：问题 + 审批结果/评论。waiting_human 事件 data 带
                                    title/description，决策事件（approve/reject/skip）带 comment。
                                    后端在暂停瞬间也写了 node_start/node_complete（同一时刻、语义是
                                    "暂停"而非"完成"），事件列表过滤掉避免"开始=完成"的困惑 */}
                                {stage.nodeType === 'human' ? (
                                  <HumanDecisionView
                                    events={stage.events}
                                    isWaiting={status === 'waiting_human'}
                                  />
                                ) : null}
                                {/* start 节点 = 工作流输入数据；end 节点 = 工作流输出结果 */}
                                {stage.nodeType === 'start' ? (
                                  <NodeDataView label="输入数据" value={task?.input} />
                                ) : null}
                                {stage.nodeType === 'end' ? (
                                  <NodeDataView label="输出结果" value={task?.output} />
                                ) : null}
                                <div style={{ marginTop: 6 }}>
                                  {(stage.nodeType === 'human'
                                    ? stage.events.filter(
                                        (e) =>
                                          e.event_type !== 'node_start' && e.event_type !== 'node_complete',
                                      )
                                    : stage.events
                                  ).map((evt, i) => (
                                    <div key={i} style={{ fontSize: 11, display: 'flex', gap: 8, lineHeight: 1.6 }}>
                                      <span style={{ fontFamily: 'monospace', color: '#8c8c8c', flexShrink: 0 }}>
                                        {fmtTime(evt.timestamp)}
                                      </span>
                                      <span style={{ minWidth: 0, wordBreak: 'break-word' }}>
                                        {EVENT_LABEL[evt.event_type] ?? evt.event_type}
                                        {evt.event_type === 'node_complete' &&
                                        typeof evt.data?.output_summary === 'string' &&
                                        evt.data.output_summary ? (
                                          <span style={{ color: '#8c8c8c' }}> · {evt.data.output_summary}</span>
                                        ) : null}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                                {stage.nodeType === 'agent' && st !== 'pending' ? (
                                  <NodeAgentDetail taskId={created.task_id} nodeId={stage.nodeId} />
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        )
                      })}
                    </div>
                  </section>
                ) : null}

                {/* 产物文件 */}
                {outputs && outputs.length ? (
                  <section style={{ marginTop: 8 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      产物文件（{outputs.length}）
                    </Typography.Text>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                      {outputs.map((f) => (
                        <Button
                          key={f._id}
                          size="small"
                          icon={<DownloadOutlined />}
                          onClick={() => void downloadUploadedFile(f._id, f.name).catch(() => message.error('下载失败'))}
                        >
                          {f.name}
                        </Button>
                      ))}
                    </div>
                  </section>
                ) : null}

                {/* 操作区：human 审批操作在上方「审批区」（与问题同处），
                    这里只留任务级操作：取消/重试/继续（取消弹窗填原因） */}
                {status === 'running' ? (
                  <div style={{ ...flex, marginTop: 8 }}>
                    <Button size="small" danger loading={acting} onClick={() => setCancelModal(true)}>
                      取消
                    </Button>
                  </div>
                ) : null}
                {status === 'failed' ? (
                  <div style={{ ...flex, marginTop: 8 }}>
                    <Button size="small" type="primary" loading={acting} onClick={() => void run('retry')}>
                      重试
                    </Button>
                  </div>
                ) : null}
                {status === 'cancelled' ? (
                  <div style={{ ...flex, marginTop: 8 }}>
                    <Button size="small" type="primary" loading={acting} onClick={() => void run('resume')}>
                      继续
                    </Button>
                  </div>
                ) : null}
              </div>
            ),
          },
        ]}
      />

      <Modal
        title="取消任务"
        open={cancelModal}
        onOk={submitCancel}
        onCancel={() => {
          setCancelModal(false)
          setCommentText('')
        }}
        okText="确认"
        cancelText="取消"
        confirmLoading={acting}
        destroyOnClose
      >
        <Typography.Text type="secondary">取消原因（可选）</Typography.Text>
        <Typography.Paragraph>
          <textarea
            value={commentText}
            onChange={(e) => setCommentText(e.target.value)}
            rows={3}
            style={{ width: '100%', marginTop: 4, resize: 'none' }}
          />
        </Typography.Paragraph>
      </Modal>
      <Modal
        title="退回重跑"
        open={rewindOpen}
        onOk={submitRewind}
        onCancel={() => {
          setRewindOpen(false)
          setRewindTarget('')
        }}
        okText="从此节点重跑"
        cancelText="取消"
        okButtonProps={{ disabled: !rewindTarget }}
        confirmLoading={acting}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          选择一个已执行的节点，任务将从该节点开始重新执行（该节点及其所有后续步骤都会重跑），
          当前等待中的审批将作废。
        </Typography.Paragraph>
        <Radio.Group
          value={rewindTarget}
          onChange={(event) => setRewindTarget(event.target.value)}
          style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
        >
          {(ckpt?.completed_nodes ?? [])
            .filter((nodeId) => nodeId !== ckpt?.paused_at_node)
            .map((nodeId) => {
              const node = nodeMap?.get(nodeId)
              const type = node?.type ?? 'agent'
              return (
                <Radio key={nodeId} value={nodeId}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ color: '#8c8c8c' }}>
                      {NODE_TYPE_ICON[type] ?? <ToolOutlined />}
                    </span>
                    <span style={{ fontSize: 13 }}>
                      {node?.label || NODE_TYPE_LABEL[type] || nodeId}
                    </span>
                  </span>
                </Radio>
              )
            })}
        </Radio.Group>
      </Modal>
    </>
  )
}

/** human 节点展开视图：问题（waiting_human 事件的 title/description）+ 审批结果与评论
 * （approve/reject/skip 事件的 data.comment）。任务详情接口的 checkpoint 在恢复后即清空，
 * 这里直接读 timeline 事件 data，已决/未决都能稳定回看。
 * isWaiting=false 且无决策事件 → 任务在审批前已流转（取消后恢复等），显示「未决策」。 */
function HumanDecisionView({
  events,
  isWaiting,
}: {
  events: TimelineEvent[]
  isWaiting: boolean
}) {
  const waiting = events.find((e) => e.event_type === 'waiting_human')
  const decision = events.find(
    (e) => e.event_type === 'approve' || e.event_type === 'reject' || e.event_type === 'skip',
  )
  const title = typeof waiting?.data?.title === 'string' ? waiting.data.title : ''
  const description =
    typeof waiting?.data?.description === 'string' ? waiting.data.description : ''
  const comment =
    typeof decision?.data?.comment === 'string' && decision.data.comment.trim()
      ? decision.data.comment
      : ''
  const decisionTag =
    decision?.event_type === 'approve'
      ? { color: 'success', label: '已通过' }
      : decision?.event_type === 'reject'
        ? { color: 'error', label: '已驳回' }
        : decision?.event_type === 'skip'
          ? { color: 'default', label: '已跳过' }
          : null
  if (!title && !description && !decisionTag) return null
  return (
    <div
      style={{
        background: '#fafafa',
        borderRadius: 6,
        padding: '6px 8px',
        marginTop: 6,
        fontSize: 12,
      }}
    >
      {title ? <div style={{ fontWeight: 600 }}>问题：{title}</div> : null}
      {description ? (
        <div style={{ whiteSpace: 'pre-wrap', color: '#595959', marginTop: 2 }}>{description}</div>
      ) : null}
      <div style={{ ...flex, marginTop: 4 }}>
        {decisionTag ? (
          <>
            <Tag color={decisionTag.color} style={{ margin: 0 }}>
              {decisionTag.label}
            </Tag>
            {comment ? (
              <span style={{ color: '#595959', minWidth: 0, wordBreak: 'break-word' }}>
                评论：{comment}
              </span>
            ) : null}
          </>
        ) : isWaiting ? (
          <Tag color="purple" style={{ margin: 0 }}>
            等待审批
          </Tag>
        ) : (
          <>
            <Tag style={{ margin: 0 }}>未决策</Tag>
            <span style={{ color: '#8c8c8c', fontSize: 11 }}>
              任务在审批前已流转（如取消后恢复继续）
            </span>
          </>
        )}
      </div>
    </div>
  )
}

/** start/end 节点数据视图：工作流输入/输出 JSON（空对象/null 不渲染）。 */
function NodeDataView({ label, value }: { label: string; value: unknown }) {
  if (!value || typeof value !== 'object' || Object.keys(value as object).length === 0) return null
  return (
    <div style={{ marginTop: 6 }}>
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        {label}
      </Typography.Text>
      <JsonText value={value} />
    </div>
  )
}

/** Agent 节点「查看执行详情」：active 时懒加载 getNodeTimeline，简单渲染 thinking/tool_call/text。 */
function NodeAgentDetail({ taskId, nodeId }: { taskId: string; nodeId: string }) {
  const [active, setActive] = useState(false)
  const [loading, setLoading] = useState(false)
  const [entries, setEntries] = useState<NodeTimelineEntry[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  // 仅在 active 打开 / nodeId 变化时请求一次。依赖绝不能含 loading/entries/err
  // ——它们随请求翻转，会把失败（entries 仍为 null + loading 回 false）重新满足 guard，
  // 导致失败→重请求→再失败的无限循环。cancelled 防止组件卸载/重开后的竞态写入。
  useEffect(() => {
    if (!active) return
    let cancelled = false
    setLoading(true)
    setErr(null)
    void tasksApi
      .getNodeTimeline(taskId, nodeId)
      .then((res) => {
        if (!cancelled) setEntries(res.timeline ?? [])
      })
      .catch((e) => {
        if (cancelled) return
        const err = e as { message?: string; status?: number }
        // 404 = 该节点尚未产出 agent trace（未执行到 / 非 agent），语义化为空，不当错误
        if (err.status === 404) setEntries([])
        else setErr(err.message ?? '加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [active, taskId, nodeId])

  return (
    <div style={{ marginTop: 6 }}>
      <Button type="link" size="small" style={{ padding: 0, height: 'auto', fontSize: 11 }} onClick={() => setActive((v) => !v)}>
        {active ? '收起执行详情' : '查看执行详情'}
      </Button>
      {active ? (
        <div style={{ marginTop: 4 }}>
          {loading ? (
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              加载中…
            </Typography.Text>
          ) : err ? (
            <Typography.Text type="danger" style={{ fontSize: 11 }}>
              {err}
            </Typography.Text>
          ) : entries && entries.length ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {entries.map((en, i) => (
                <EntryView key={en.id ?? i} entry={en} />
              ))}
            </div>
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              暂无执行明细
            </Typography.Text>
          )}
        </div>
      ) : null}
    </div>
  )
}

function EntryView({ entry }: { entry: NodeTimelineEntry }) {
  const isTool = entry.type === 'tool_call' || entry.type === 'tool_result' || entry.type === 'tool'
  const label =
    entry.type === 'thinking'
      ? '思考'
      : entry.type === 'tool_call'
        ? `调用工具${entry.tool_name ? ` · ${entry.tool_name}` : ''}`
        : entry.type === 'tool_result'
          ? '工具结果'
          : entry.type === 'tool'
            ? '工具'
            : entry.type === 'user'
              ? '用户'
              : '文本'
  return (
    <div style={{ fontSize: 11, background: '#fafafa', borderRadius: 4, padding: '4px 6px' }}>
      <div style={{ fontWeight: 500, color: isTool ? '#1677ff' : '#8c8c8c', marginBottom: 2 }}>{label}</div>
      {entry.content ? (
        <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: entry.type === 'thinking' ? 60 : 120, overflow: 'auto' }}>
          {entry.content}
        </div>
      ) : null}
      {entry.args ? <JsonText value={entry.args} /> : null}
    </div>
  )
}

import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  DatabaseOutlined,
  FileOutlined,
  FileTextOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { Mermaid } from '@ant-design/x'
import { App, Button, Collapse, Image, Tag, Tooltip, Typography } from 'antd'
import { isValidElement, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { downloadSessionFile, downloadUploadedFile } from '../api/chat'
import {
  DISMISSED_CLARIFICATION_TEXT,
  type AttachmentView,
  type ChatMessage,
  type ContentBlock,
  type ToolRun,
} from '../types'
import { ChartBlock } from './ChartBlock'
import { parseTaskCreated } from './WorkflowTaskCard'
import { WorkflowTaskInlineCard } from './WorkflowTaskInlineCard'

/** 特殊工具:不参与聚合,各自独立渲染(WorkflowTaskCard / chat 层 HITL)。 */
const SPECIAL_TOOLS = new Set(['dispatch_workflow', 'ask_clarification', 'confirm_workflow'])

/** 一段连续的普通工具分组。特殊工具作为独立的 ToolRun 单独渲染,打断聚合。 */
type ToolGroup =
  | { kind: 'group'; tools: ToolRun[] }
  | { kind: 'single'; tool: ToolRun }

/** 渲染段:把 content blocks 拆成有序的渲染单元,保留 text/reasoning/tool 的交错顺序。 */
type RenderSegment =
  | { kind: 'text'; text: string }
  | { kind: 'reasoning'; text: string }
  | { kind: 'tools'; group: ToolGroup }

/** 遍历 content blocks,产出按原始顺序排列的渲染段。
 * - text block → text 段(Markdown)
 * - reasoning block → reasoning 段(思考过程 Collapse)
 * - 连续的 tool block → 用 groupTools 聚合(≥2 个普通工具合成一个卡片)
 * text 和 reasoning 不合并,各自独立成段。 */
function toRenderSegments(blocks: ContentBlock[]): RenderSegment[] {
  const segments: RenderSegment[] = []
  let toolBuffer: ToolRun[] = []
  const flushTools = () => {
    if (!toolBuffer.length) return
    for (const group of groupTools(toolBuffer)) {
      segments.push({ kind: 'tools', group })
    }
    toolBuffer = []
  }
  for (const block of blocks) {
    if (block.type === 'tool') {
      toolBuffer.push(block.tool)
    } else {
      flushTools()
      segments.push(
        block.type === 'text'
          ? { kind: 'text', text: block.text }
          : { kind: 'reasoning', text: block.text },
      )
    }
  }
  flushTools()
  return segments
}

/** 共享的 react-markdown components 配置(无光标)。 */
const MARKDOWN_COMPONENTS: Components = {
  a: ({ href, children: label }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {label}
    </a>
  ),
  code: ({ className, children: codeChildren, ...props }) => {
    const language = /language-([^\s]+)/.exec(className ?? '')?.[1]
    const source = String(codeChildren).replace(/\n$/, '')
    if (language === 'echarts' || language === 'chart') {
      return <ChartBlock source={source} />
    }
    if (language === 'mermaid') {
      return <Mermaid>{source}</Mermaid>
    }
    return (
      <code className={className} {...props}>
        {codeChildren}
      </code>
    )
  },
  pre: ({ children: preChildren, ...props }) =>
    isValidElement(preChildren) &&
    (preChildren.type === ChartBlock || preChildren.type === Mermaid) ? (
      preChildren
    ) : (
      <pre {...props}>{preChildren}</pre>
    ),
  table: ({ children: tableChildren, ...props }) => (
    <div className="markdown-table-wrap">
      <table {...props}>{tableChildren}</table>
    </div>
  ),
}

/** Markdown 渲染(带统一的 components 配置)。 */
function Markdown({ children }: { children: string }) {
  return (
    <div className="md-host">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  )
}

/** 中文"字数"口径（Word/WPS 同款，与 studio countZi 一致）：CJK 字符（含中文
 *  标点）每字计 1，连续的非 CJK 非空白串（英文单词、数字、emoji 等）整体计 1。
 *  思考内容多为英文，逐字符计数会数倍虚高于视觉感知。 */
const countZi = (s: string): number => {
  const cjkRe = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u30ff\uff00-\uffef]/
  let n = 0
  let inRun = false
  for (const ch of s) {
    if (cjkRe.test(ch)) {
      n += 1
      inRun = false
    } else if (/\s/.test(ch)) {
      inRun = false
    } else if (!inRun) {
      n += 1
      inRun = true
    }
  }
  return n
}

/** 思考面板 —— 对齐 studio ThinkingEntryCard 行为：
 * - 流式期间（streaming）：展开 + 头部三点动效 + 内容贴底跟随 + 末尾打字光标；
 * - 本轮思考结束（后续 text/tool 块到达）：自动收起（用户手动操作过则尊重用户），
 *   头部变静态「思考过程」+ 字数；
 * - 内容按纯文本 pre-wrap 渲染（思考是原始文本，流式期间半截 Markdown 会渲染错乱），
 *   限高内部滚动，长思考不撑爆消息流。 */
function ReasoningPanel({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(streaming)
  const [userToggled, setUserToggled] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)

  // 流式状态切换时跟随展开/收起（用户手动操作过则不再自动）
  useEffect(() => {
    if (userToggled) return
    setOpen(streaming)
  }, [streaming, userToggled])

  // 流式期间内容超出限高时，内部滚动贴底跟随最新输出
  useEffect(() => {
    if (streaming && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight
    }
  }, [text, streaming])

  return (
    <Collapse
      className="reasoning-panel"
      ghost
      size="small"
      activeKey={open ? ['reasoning'] : []}
      onChange={(keys) => {
        setUserToggled(true)
        setOpen((Array.isArray(keys) ? keys : [keys]).length > 0)
      }}
      items={[
        {
          key: 'reasoning',
          label: (
            <span className="reasoning-label">
              {streaming ? (
                <>
                  正在思考
                  <span className="streaming-dots" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                </>
              ) : (
                <>
                  思考过程
                  {text ? (
                    <span className="reasoning-count">{countZi(text)} 字</span>
                  ) : null}
                </>
              )}
            </span>
          ),
          children: (
            <div ref={contentRef} className="reasoning-text">
              {text}
              {streaming ? <span className="reasoning-caret">▍</span> : null}
            </div>
          ),
        },
      ]}
    />
  )
}

function promotedCharts(blocks: ContentBlock[]): string[] {
  const seen = new Set<string>()
  const charts: string[] = []
  for (const block of blocks) {
    if (block.type !== 'tool') continue
    const tool = block.tool
    if (tool.name !== 'render_chart' || !tool.result) continue
    const match = tool.result.match(/```(?:echarts|chart)\s*\n([\s\S]*?)\n```/i)
    const source = match?.[1]?.trim()
    if (!source || seen.has(source)) continue
    seen.add(source)
    charts.push(source)
  }
  return charts
}

function statusIcon(tool: ToolRun): ReactNode {
  if (tool.status === 'running') return <ClockCircleOutlined spin />
  if (tool.status === 'error') return <CloseCircleOutlined />
  return <CheckCircleOutlined />
}

/** 从 parse_file 的 args(JSON 字符串)里取展示文件名(path basename 或 file_id)。 */
function parseFileName(args?: string): string {
  try {
    const a: { path?: string; file_id?: string } = args ? JSON.parse(args) : {}
    const raw = String(a.path || a.file_id || '')
    return raw.split('/').pop() || '文件'
  } catch {
    return '文件'
  }
}

/** parse_file 成功结果 → 专属文件解析卡片(FileText 图标 + 文件名 + 元信息 Tag，
 * 内容 Markdown 渲染)。失败([parse_file] 前缀错误)回落到通用工具卡。 */
function ParseFileCard({ tool }: { tool: ToolRun }) {
  const meta = /^# .+（(.+?)）/.exec(tool.result ?? '')
  return (
    <Collapse
      className={`tool-run tool-run-${tool.status}`}
      size="small"
      ghost
      items={[
        {
          key: tool.id,
          label: (
            <div className="tool-title">
              {statusIcon(tool)}
              <FileTextOutlined />
              <span>文件解析 · {parseFileName(tool.args)}</span>
              {meta ? <Tag style={{ margin: 0 }}>{meta[1]}</Tag> : null}
            </div>
          ),
          children: (
            <div className="tool-details">
              <div className="tool-result-body">
                <Markdown>{tool.result ?? ''}</Markdown>
              </div>
            </div>
          ),
        },
      ]}
    />
  )
}

/** 把扁平的 tools 数组按连续性分成"普通工具组"和"特殊工具"交替序列。
 * 特殊工具(dispatch_workflow / ask_clarification / confirm_workflow)独立成项,
 * 打断前后普通工具的聚合;连续的普通工具合并成一个 group。 */
function groupTools(tools: ToolRun[]): ToolGroup[] {
  const groups: ToolGroup[] = []
  let buffer: ToolRun[] = []
  for (const tool of tools) {
    if (SPECIAL_TOOLS.has(tool.name)) {
      if (buffer.length) {
        groups.push({ kind: 'group', tools: buffer })
        buffer = []
      }
      groups.push({ kind: 'single', tool })
    } else {
      buffer.push(tool)
    }
  }
  if (buffer.length) groups.push({ kind: 'group', tools: buffer })
  return groups
}

/** 工具名展示:知识库类工具用更友好的中文名,其余用原名。 */
function toolDisplayName(name: string): { text: string; isKnowledge: boolean } {
  const isKnowledge = name === 'kb_retrieve' || name === 'search_kb'
  return { text: isKnowledge ? '知识库检索' : name, isKnowledge }
}

/** 单个工具节点的详情(请求参数 / 执行结果),timeline 中点击节点后行内展开。 */
function ToolNodeDetails({ tool }: { tool: ToolRun }) {
  return (
    <div className="tool-node-details">
      {tool.args ? (
        <section>
          <Typography.Text type="secondary">请求参数</Typography.Text>
          <pre>{tool.args}</pre>
        </section>
      ) : null}
      {tool.result ? (
        <section>
          <Typography.Text type="secondary">执行结果</Typography.Text>
          <div className="tool-result-body">
            <Markdown>{tool.result}</Markdown>
          </div>
        </section>
      ) : tool.status === 'running' ? (
        <Typography.Text type="secondary">正在执行...</Typography.Text>
      ) : null}
    </div>
  )
}

/** 聚合工具组:一行摘要(数量 + 状态)+ 展开后以 timeline(节点 + 竖线串联)列出工具,
 * 让用户清楚看到执行了哪些工具、进行到第几步。每个 timeline 节点可点击,行内展开
 * 该工具的请求参数/执行结果。折叠/展开交给 antd Collapse,不做「结束自动收缩」。 */
function ToolRunsGroup({ tools }: { tools: ToolRun[] }) {
  const running = tools.filter((t) => t.status === 'running')
  const errored = tools.filter((t) => t.status === 'error')
  const completedCount = tools.length - running.length - errored.length
  const isRunning = running.length > 0
  const hasError = errored.length > 0

  // 行内展开详情的工具 id(null = 都不展开)
  const [expandedToolId, setExpandedToolId] = useState<string | null>(null)

  // 整体状态图标:任一 running→转圈,任一 error→红叉,否则→绿勾
  const overallIcon = isRunning ? (
    <ClockCircleOutlined spin />
  ) : hasError ? (
    <CloseCircleOutlined />
  ) : (
    <CheckCircleOutlined />
  )

  const cls = isRunning ? 'running' : hasError ? 'error' : 'complete'

  // 摘要文案
  const summary = isRunning
    ? running.length === 1
      ? `${completedCount}/${tools.length} 完成 · 正在执行 ${toolDisplayName(running[0].name).text}...`
      : `${completedCount}/${tools.length} 完成 · 正在执行 ${running.length} 个工具...`
    : `使用了 ${tools.length} 个工具`

  return (
    <Collapse
      className={`tool-run tool-run-group tool-run-${cls}`}
      size="small"
      ghost
      items={[
        {
          key: 'group',
          label: (
            <div className="tool-title">
              {overallIcon}
              <ToolOutlined />
              <span>{summary}</span>
            </div>
          ),
          children: (
            <ol className="tool-timeline-list">
              {tools.map((tool, index) => {
                const { text, isKnowledge } = toolDisplayName(tool.name)
                const isLast = index === tools.length - 1
                const isOpen = expandedToolId === tool.id
                return (
                  <li
                    key={tool.id}
                    className={`tool-timeline-node tool-timeline-node-${tool.status}${isOpen ? ' tool-timeline-node-open' : ''}${isLast ? ' tool-timeline-node-last' : ''}`}
                  >
                    <button
                      type="button"
                      className="tool-timeline-node-row"
                      onClick={() => setExpandedToolId(isOpen ? null : tool.id)}
                      aria-expanded={isOpen}
                    >
                      <span className="tool-timeline-node-icon">{statusIcon(tool)}</span>
                      <span className="tool-timeline-node-label">
                        {isKnowledge ? <DatabaseOutlined /> : <ToolOutlined />}
                        <span className="tool-timeline-node-name">{text}</span>
                        {tool.auto ? <Tag className="tool-timeline-node-tag">自动召回</Tag> : null}
                      </span>
                    </button>
                    {isOpen ? (
                      <div className="tool-timeline-node-details-wrap">
                        <ToolNodeDetails tool={tool} />
                      </div>
                    ) : null}
                  </li>
                )
              })}
            </ol>
          ),
        },
      ]}
    />
  )
}

/** 解析 tool.args(JSON 字符串)为对象,失败返回空对象。 */
function parseToolArgs(tool: ToolRun): Record<string, unknown> {
  try {
    return tool.args ? JSON.parse(tool.args) : {}
  } catch {
    return {}
  }
}

/** ask_clarification 已答卡片:显示问题 + 用户的回答(参考 studio ClarificationCard 已答态)。
 * 等待回答时(tool 无 result)显示精简的"等待回答"提示(实际交互在 ChatView HITL Alert)。
 * 配色:蓝色(问答/澄清语义)。 */
function ClarificationAnsweredCard({ tool }: { tool: ToolRun }) {
  const args = parseToolArgs(tool)
  const question = String(args.question ?? '请补充信息后继续。')
  const answered = !!tool.result
  const dismissed = tool.result === DISMISSED_CLARIFICATION_TEXT

  // 解析 fields 拿到 name→label 映射
  const rawFields = args.fields
  let fieldLabels: Record<string, string> = {}
  if (Array.isArray(rawFields)) {
    for (const f of rawFields as Array<Record<string, unknown>>) {
      if (f.name && f.label) fieldLabels[String(f.name)] = String(f.label)
    }
  } else if (typeof rawFields === 'string' && rawFields) {
    try {
      const parsed = JSON.parse(rawFields)
      if (Array.isArray(parsed)) {
        for (const f of parsed) {
          if (f.name && f.label) fieldLabels[f.name] = f.label
        }
      }
    } catch { /* ignore */ }
  }

  // 解析回答结果（JSON 字符串 → 键值对）
  let answerPairs: Array<[string, string]> | null = null
  if (answered && tool.result) {
    try {
      const parsed = JSON.parse(tool.result)
      if (typeof parsed === 'object' && parsed && !Array.isArray(parsed)) {
        answerPairs = Object.entries(parsed).map(([k, v]) => [
          fieldLabels[k] || k,
          typeof v === 'boolean' ? (v ? '是' : '否') : String(v),
        ])
      }
    } catch { /* not JSON, show raw */ }
  }

  return (
    <div className="interactive-card interactive-card-clarification">
      <div className="interactive-card-header">
        <QuestionCircleOutlined />
        <span className="interactive-card-label">澄清提问</span>
        {answered ? (
          dismissed ? (
            <Tag style={{ margin: 0 }}>已忽略</Tag>
          ) : (
            <Tag color="blue" style={{ margin: 0 }}>已回答</Tag>
          )
        ) : (
          <Tag style={{ margin: 0 }}>等待回答</Tag>
        )}
      </div>
      <div className="interactive-card-inner">
        <div className="interactive-card-question">{question}</div>
        {dismissed ? (
          <div className="interactive-card-answer">
            <span>已忽略此问题——可直接在输入框重新描述需求或上传文件</span>
          </div>
        ) : answered && tool.result ? (
          <div className="interactive-card-answer">
            {answerPairs ? (
              <div className="clarification-form-answered">
                {answerPairs.map(([label, val]) => (
                  <div key={label} className="clarification-form-row">
                    <span className="clarification-form-key">{label}</span>
                    <span className="clarification-form-val">{val}</span>
                  </div>
                ))}
              </div>
            ) : (
              <span>{tool.result}</span>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}

/** confirm_workflow 已答卡片:显示工作流信息 + 确认/拒绝结果。
 * 等待回答时显示精简的"等待确认"提示。
 * 配色:紫色(工作流/自动化语义)。 */
function WorkflowConfirmAnsweredCard({ tool }: { tool: ToolRun }) {
  const args = parseToolArgs(tool)
  const workflowName = String(args.workflow_name ?? '')
  const description = String(args.description ?? '')
  const params = (args.params ?? {}) as Record<string, unknown>
  const answered = !!tool.result
  const isConfirmed = answered && !/取消|拒绝|忽略|cancel/i.test(tool.result ?? '')

  return (
    <div className="interactive-card interactive-card-workflow">
      <div className="interactive-card-header">
        <RobotOutlined />
        <span className="interactive-card-label">工作流确认</span>
        {answered ? (
          <Tag color={isConfirmed ? 'success' : 'default'} style={{ margin: 0 }}>
            {isConfirmed ? '✓ 已确认' : '✗ 已取消'}
          </Tag>
        ) : (
          <Tag style={{ margin: 0 }}>等待确认</Tag>
        )}
      </div>
      <div className="interactive-card-inner">
        {workflowName ? (
          <div className="interactive-card-row">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>工作流:</Typography.Text>
            <Tag color="purple" style={{ margin: 0 }}>{workflowName}</Tag>
          </div>
        ) : null}
        {description ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{description}</Typography.Text>
        ) : null}
        {Object.keys(params).length > 0 ? (
          <div className="interactive-card-params">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>输入参数:</Typography.Text>
            {Object.entries(params).map(([key, value]) => (
              <div key={key} className="interactive-card-param">
                <span className="interactive-card-param-key">{key}</span>
                <span className="interactive-card-param-value">
                  {typeof value === 'string' ? value : JSON.stringify(value)}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function ToolResult({
  tool,
  onOpenTaskBoard,
}: {
  tool: ToolRun
  onOpenTaskBoard?: (taskId: string) => void
}) {
  // dispatch_workflow：解析 task_created → 单行状态卡（详情/轮询/操作在任务看板）。
  // 解析失败则落到下方通用工具结果渲染。
  if (tool.name === 'dispatch_workflow') {
    const created = parseTaskCreated(tool.result)
    if (created)
      return <WorkflowTaskInlineCard created={created} onOpenBoard={onOpenTaskBoard} />
  }
  // ask_clarification：已答时显示"问题→回答"卡片(参考 studio ClarificationCard 已答态)。
  if (tool.name === 'ask_clarification') {
    return <ClarificationAnsweredCard tool={tool} />
  }
  // confirm_workflow：已答时显示"工作流确认→确认/拒绝"卡片。
  if (tool.name === 'confirm_workflow') {
    return <WorkflowConfirmAnsweredCard tool={tool} />
  }
  // parse_file：成功解析 → 专属文件解析卡片(文件名 + 元信息 + Markdown 内容)。
  if (
    tool.name === 'parse_file' &&
    tool.result &&
    !tool.result.startsWith('[parse_file]')
  ) {
    return <ParseFileCard tool={tool} />
  }
  const isKnowledge = tool.name === 'kb_retrieve' || tool.name === 'search_kb'
  const title = isKnowledge ? '知识库检索' : tool.name
  return (
    <Collapse
      className={`tool-run tool-run-${tool.status}`}
      size="small"
      ghost
      items={[
        {
          key: tool.id,
          label: (
            <div className="tool-title">
              {statusIcon(tool)}
              {isKnowledge ? <DatabaseOutlined /> : <ToolOutlined />}
              <span>{title}</span>
              {tool.auto ? <Tag>自动召回</Tag> : null}
            </div>
          ),
          children: (
            <div className="tool-details">
              {tool.args ? (
                <section>
                  <Typography.Text type="secondary">请求参数</Typography.Text>
                  <pre>{tool.args}</pre>
                </section>
              ) : null}
              {tool.result ? (
                <section>
                  <Typography.Text type="secondary">执行结果</Typography.Text>
                  <div className="tool-result-body">
                    <Markdown>{tool.result}</Markdown>
                  </div>
                </section>
              ) : tool.status === 'running' ? (
                <Typography.Text type="secondary">正在执行...</Typography.Text>
              ) : null}
            </div>
          ),
        },
      ]}
    />
  )
}

function AttachmentItem({
  attachment,
  sessionId,
}: {
  attachment: AttachmentView
  sessionId: string
}) {
  const { message } = App.useApp()
  if (attachment.kind === 'image' && attachment.url) {
    return (
      <Image
        className="message-image"
        src={attachment.url}
        alt={attachment.name}
        preview
      />
    )
  }
  return (
    <Button
      className="message-file"
      icon={<FileOutlined />}
      onClick={() => {
        const download =
          attachment.source === 'upload'
            ? downloadUploadedFile(attachment.id, attachment.name)
            : downloadSessionFile(sessionId, attachment.name)
        void download.catch(() =>
          message.error('文件下载失败'),
        )
      }}
    >
      {attachment.name}
    </Button>
  )
}

interface MessageContentProps {
  message: ChatMessage
  sessionId: string
  /** dispatch_workflow 一行状态卡点击 → ChatView 打开工作流任务看板并定位。 */
  onOpenTaskBoard?: (taskId: string) => void
}

export function MessageContent({ message, sessionId, onOpenTaskBoard }: MessageContentProps) {
  const { message: toast } = App.useApp()
  // 快捷指令消息：user 气泡只渲染展示文案（label），隐藏实际发送给 AI 的指令。
  // 下游 segments / charts / fullText（复制）全部基于替换后的内容，保证不泄漏。
  const content: ContentBlock[] =
    message.role === 'user' && message.displayText
      ? [{ type: 'text', text: message.displayText }]
      : message.content
  const charts = [...promotedCharts(content), ...message.charts]
  const segments = toRenderSegments(content)
  // 兜底去重:同名附件可能被上游重复收集,按 id 收敛,避免重复渲染 + 重复 React key
  const uniqueAttachments = useMemo(() => {
    const map = new Map<string, AttachmentView>()
    for (const att of message.attachments) map.set(att.id, att)
    return Array.from(map.values())
  }, [message.attachments])
  // 拼接所有 text block 作为复制内容
  const fullText = content
    .filter((b): b is ContentBlock & { type: 'text' } => b.type === 'text')
    .map((b) => b.text)
    .join('')
  return (
    <div className={`message-content message-content-${message.role}`}>
      {segments.map((segment, index) => {
        if (segment.kind === 'reasoning') {
          // 思考进行中 = 整条消息仍在流式 且 该思考块是最后一个内容块
          // （后续 text/tool 块一旦到达，思考即已结束——studio 同语义）
          const streaming = message.status === 'loading' && index === segments.length - 1
          return <ReasoningPanel key={`seg:${index}`} text={segment.text} streaming={streaming} />
        }
        if (segment.kind === 'text') {
          return <Markdown key={`seg:${index}`}>{segment.text}</Markdown>
        }
        // tools segment
        const { group } = segment
        if (group.kind === 'single') {
          // 特殊工具(dispatch_workflow / HITL)走完整操作卡片
          return (
            <ToolResult
              key={group.tool.id}
              tool={group.tool}
              onOpenTaskBoard={onOpenTaskBoard}
            />
          )
        }
        // 普通工具(无论单个还是多个)一律走精简聚合样式
        return <ToolRunsGroup key={`group:${index}`} tools={group.tools} />
      })}
      {uniqueAttachments.length > 0 ? (
        <div className="message-attachments">
          {uniqueAttachments.map((attachment) => (
            <AttachmentItem
              key={attachment.id}
              attachment={attachment}
              sessionId={sessionId}
            />
          ))}
        </div>
      ) : null}
      {charts.map((source, index) => (
        <ChartBlock key={`chart:${index}:${source.slice(0, 32)}`} source={source} />
      ))}
      {/* 执行中动效:三点跳动,放在消息末尾(不碰 markdown 内部,稳定不抖)。
          首 token 前额外显示「正在响应」文字,内容开始后只剩三点。 */}
      {message.role === 'assistant' && message.status === 'loading' ? (
        <div className="streaming-loading">
          {segments.length === 0 ? (
            <Typography.Text type="secondary">正在响应</Typography.Text>
          ) : null}
          <span className="streaming-dots" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        </div>
      ) : null}
      {message.error ? (
        <Typography.Text type="danger">{message.error}</Typography.Text>
      ) : null}
      {message.role === 'assistant' && fullText ? (
        <div className="message-actions">
          <Tooltip title="复制回答">
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined />}
              onClick={() => {
                void navigator.clipboard.writeText(fullText)
                void toast.success('已复制')
              }}
              aria-label="复制回答"
            />
          </Tooltip>
        </div>
      ) : null}
    </div>
  )
}

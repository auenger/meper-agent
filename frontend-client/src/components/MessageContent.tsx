import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  DatabaseOutlined,
  FileOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { Mermaid } from '@ant-design/x'
import { App, Button, Collapse, Image, Tag, Tooltip, Typography } from 'antd'
import { isValidElement, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { downloadSessionFile, downloadUploadedFile } from '../api/chat'
import type { AttachmentView, ChatMessage, ContentBlock, ToolRun } from '../types'
import { ChartBlock } from './ChartBlock'
import { WorkflowTaskCard, parseTaskCreated } from './WorkflowTaskCard'

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

  return (
    <div className="interactive-card interactive-card-clarification">
      <div className="interactive-card-header">
        <QuestionCircleOutlined />
        <span className="interactive-card-label">澄清提问</span>
        {answered ? (
          <Tag color="blue" style={{ margin: 0 }}>已回答</Tag>
        ) : (
          <Tag style={{ margin: 0 }}>等待回答</Tag>
        )}
      </div>
      <div className="interactive-card-inner">
        <div className="interactive-card-question">{question}</div>
        {answered && tool.result ? (
          <div className="interactive-card-answer">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>你的回答:</Typography.Text>
            <span>{tool.result}</span>
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
  const isConfirmed = answered && !/取消|拒绝|cancel/i.test(tool.result ?? '')

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

function ToolResult({ tool }: { tool: ToolRun }) {
  // dispatch_workflow：解析 task_created → 内嵌可展开 task 卡片（自带详情/轮询/操作）。
  // 解析失败则落到下方通用工具结果渲染。
  if (tool.name === 'dispatch_workflow') {
    const created = parseTaskCreated(tool.result)
    if (created) return <WorkflowTaskCard created={created} />
  }
  // ask_clarification：已答时显示"问题→回答"卡片(参考 studio ClarificationCard 已答态)。
  if (tool.name === 'ask_clarification') {
    return <ClarificationAnsweredCard tool={tool} />
  }
  // confirm_workflow：已答时显示"工作流确认→确认/拒绝"卡片。
  if (tool.name === 'confirm_workflow') {
    return <WorkflowConfirmAnsweredCard tool={tool} />
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
}

export function MessageContent({ message, sessionId }: MessageContentProps) {
  const { message: toast } = App.useApp()
  const charts = [...promotedCharts(message.content), ...message.charts]
  const segments = toRenderSegments(message.content)
  // 兜底去重:同名附件可能被上游重复收集,按 id 收敛,避免重复渲染 + 重复 React key
  const uniqueAttachments = useMemo(() => {
    const map = new Map<string, AttachmentView>()
    for (const att of message.attachments) map.set(att.id, att)
    return Array.from(map.values())
  }, [message.attachments])
  // 拼接所有 text block 作为复制内容
  const fullText = message.content
    .filter((b): b is ContentBlock & { type: 'text' } => b.type === 'text')
    .map((b) => b.text)
    .join('')
  return (
    <div className={`message-content message-content-${message.role}`}>
      {segments.map((segment, index) => {
        if (segment.kind === 'reasoning') {
          return (
            <Collapse
              key={`seg:${index}`}
              className="reasoning-panel"
              ghost
              size="small"
              items={[
                {
                  key: 'reasoning',
                  label: message.status === 'loading' ? '正在思考' : '思考过程',
                  children: <Markdown>{segment.text}</Markdown>,
                },
              ]}
            />
          )
        }
        if (segment.kind === 'text') {
          return <Markdown key={`seg:${index}`}>{segment.text}</Markdown>
        }
        // tools segment
        const { group } = segment
        if (group.kind === 'single') {
          // 特殊工具(dispatch_workflow / HITL)走完整操作卡片
          return <ToolResult key={group.tool.id} tool={group.tool} />
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

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  getSessionFile,
  getUploadedFile,
  listMessages,
  stopAgentStream,
  streamConfirmation,
  streamMessage,
  uploadSessionFile,
} from '../api/chat'
import type {
  AttachmentView,
  ChatMessage,
  ContentBlock,
  HitlState,
  MessageRecord,
  StreamEvent,
  ToolRun,
} from '../types'

/** 生成 UUID。crypto.randomUUID 在非安全上下文（如非 localhost 的 HTTP）
 * 下不可用，这里退化为随机 UUID v4，保证发送流程在任何环境都能跑。 */
function genId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

const OUTPUT_PATH_RE = /\boutput\/([^\s"'<>)\\]+\.[A-Za-z0-9]+)/g
const FILE_BLOCK_RE =
  /<file_hint\b[^>]*>[\s\S]*?<\/file_hint>|<file\b[^>]*\/>|<file\b[^>]*>[\s\S]*?<\/file>/g
const FILE_NAME_RE = /<file\s+name="([^"]+)"\s+mime="([^"]+)"/g

function outputAttachments(text: string): AttachmentView[] {
  const seen = new Set<string>()
  const result: AttachmentView[] = []
  for (const match of text.matchAll(OUTPUT_PATH_RE)) {
    const name = match[1]
    if (!name || seen.has(name)) continue
    seen.add(name)
    result.push({
      id: `output:${name}`,
      name,
      contentType: 'application/octet-stream',
      kind: /\.(png|jpe?g|gif|webp|svg)$/i.test(name) ? 'image' : 'file',
      source: 'output',
    })
  }
  return result
}

function userHistoryContent(content: string): {
  text: string
  attachments: AttachmentView[]
} {
  const attachments: AttachmentView[] = []
  let index = 0
  for (const match of content.matchAll(FILE_NAME_RE)) {
    const name = match[1]
    const contentType = match[2]
    if (!name || !contentType) continue
    attachments.push({
      id: `history-file:${index++}:${name}`,
      name,
      contentType,
      kind: contentType.startsWith('image/') ? 'image' : 'file',
      source: 'upload',
    })
  }
  return { text: content.replace(FILE_BLOCK_RE, '').trim(), attachments }
}

function fromHistory(record: MessageRecord): ChatMessage {
  if (record.role === 'user') {
    const parsed = userHistoryContent(record.content ?? '')
    const storedAttachments: AttachmentView[] = (record.files ?? []).map(
      (file, index) => ({
        id: file.id || file._id || `history-file-${index}`,
        name: file.name,
        contentType: file.mime_type || 'application/octet-stream',
        kind:
          file.mime_type?.startsWith('image/') ||
          /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(file.name)
            ? 'image'
            : 'file',
        source: 'upload',
      }),
    )
    return {
      id: record.id,
      role: 'user',
      content: parsed.text ? [{ type: 'text', text: parsed.text }] : [],
      // 快捷指令消息：气泡优先展示 display_text（label），content 仅为回退
      displayText: record.display_text || undefined,
      attachments: storedAttachments.length ? storedAttachments : parsed.attachments,
      charts: [],
      status: 'success',
      createdAt: record.created_at ? new Date(record.created_at) : undefined,
    }
  }
  // 单次线性遍历 timeline_entries,按真实顺序产出 content blocks,
  // 不再用 filter+join(那会丢失 thinking/text/tool 的交错顺序)。
  const entries = record.timeline_entries ?? []
  const blocks: ContentBlock[] = []
  /** key = tool_call id(entry.id),用于和 tool_result(entry.tool_call_id)精确配对。 */
  const pendingById = new Map<string, ToolRun>()
  /** 退化兜底:key = tool_name,用于旧数据(无 id)按名称配对。 */
  const pendingByName = new Map<string, ToolRun>()
  let allTools: ToolRun[] = []
  for (const [index, entry] of entries.entries()) {
    if (entry.type === 'thinking' && entry.content) {
      blocks.push({ type: 'reasoning', text: entry.content })
    } else if (
      (entry.type === 'text' || entry.type === 'final_answer') &&
      entry.content
    ) {
      blocks.push({ type: 'text', text: entry.content })
    } else if (entry.type === 'tool_call' || entry.type === 'tool') {
      const toolCallId = entry.id || entry.tool_call_id || ''
      const tool: ToolRun = {
        id: `history-tool-${index}`,
        toolCallId,
        name: entry.tool_name || 'tool',
        args: entry.args ? JSON.stringify(entry.args, null, 2) : undefined,
        status: entry.type === 'tool' ? 'complete' : 'running',
      }
      blocks.push({ type: 'tool', tool })
      allTools.push(tool)
      if (entry.type === 'tool_call') {
        if (toolCallId) pendingById.set(toolCallId, tool)
        pendingByName.set(entry.tool_name || 'tool', tool)
      }
    } else if (entry.type === 'tool_result') {
      // 优先用 tool_call_id 精确配对;退化兜底用 tool_name
      const resultId = entry.tool_call_id || entry.id || ''
      const matched = (resultId && pendingById.get(resultId)) || pendingByName.get(entry.tool_name || 'tool')
      if (matched) {
        matched.result = entry.content
        // 按 entry.is_error 区分:工具执行失败(ToolMessage.status="error")标 error,
        // 旧数据无该字段按正常完成处理。与流式路径(tool_result 事件 status)对齐。
        const isError = entry.is_error === true
        matched.isError = isError
        matched.status = isError ? 'error' : 'complete'
        if (resultId) pendingById.delete(resultId)
        pendingByName.delete(entry.tool_name || 'tool')
      }
    }
  }
  // 兜底:如果没有任何 text block,用 record.content 作为正文(向后兼容旧数据)
  if (!blocks.some((b) => b.type === 'text') && record.content) {
    blocks.push({ type: 'text', text: record.content })
  }
  // 跨 tool 去重:同名 output 文件常出现在多个 tool_result 里(写文件后被后续
  // tool/agent 回显路径),用 Map 按 id 收敛,与流式路径(acc.attachments.set)对齐。
  const attachmentMap = new Map<string, AttachmentView>()
  for (const tool of allTools) {
    for (const att of outputAttachments(tool.result ?? '')) {
      attachmentMap.set(att.id, att)
    }
  }
  const attachments = Array.from(attachmentMap.values())
  return {
    id: record.id,
    role: 'assistant',
    content: blocks,
    attachments,
    charts: [],
    status: 'success',
    createdAt: record.created_at ? new Date(record.created_at) : undefined,
    requestId: record.request_id ?? undefined,
  }
}

interface AssistantAccumulator {
  id: string
  /** 本轮请求 id——done 事件挂载（消息级反馈轮次键，§8.2） */
  requestId?: string
  /** 按事件到达顺序排列的内容块(保留 text/thinking/tool 的交错顺序)。 */
  blocks: ContentBlock[]
  attachments: Map<string, AttachmentView>
  charts: Map<string, string>
  /** 流内 error 事件记录的错误文本。一旦设置，后续 flush 会保持 error 状态。 */
  errorText?: string
}

/** 从 blocks 中找出所有 tool 块的 tool 对象(用于附件/chart 提取等)。 */
function allToolsFromBlocks(blocks: ContentBlock[]): ToolRun[] {
  return blocks.filter((b): b is ContentBlock & { type: 'tool' } => b.type === 'tool').map((b) => b.tool)
}

function isImageName(name: string): boolean {
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name)
}

function isChartOption(source: string): boolean {
  if (new Blob([source]).size > 1024 * 1024) return false
  try {
    const parsed = JSON.parse(source) as { series?: unknown }
    return Array.isArray(parsed.series) && parsed.series.length > 0
  } catch {
    return false
  }
}

export function useChat(
  agentId: string | null,
  sessionId: string | null,
  onFilesChanged: () => void,
  onSessionChanged?: () => void,
) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [hitl, setHitl] = useState<HitlState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const accRef = useRef<AssistantAccumulator | null>(null)
  const urlsRef = useRef<Set<string>>(new Set())
  // reloadTick: 后台轮询发现后端落库后 +1，触发主加载 effect 重新完整加载（含图片/chart artifacts）。
  const [reloadTick, setReloadTick] = useState(0)
  // 被流式中切换走的会话：后端任务仍会跑完落库，切回时轮询直到落库。
  const pendingBackgroundRef = useRef<Set<string>>(new Set())
  const prevSessionIdRef = useRef<string | null>(null)
  const runningRef = useRef(false)
  runningRef.current = running

  const revokeUrls = useCallback(() => {
    for (const url of urlsRef.current) URL.revokeObjectURL(url)
    urlsRef.current.clear()
  }, [])

  useEffect(() => {
    let cancelled = false
    // 切换会话：旧会话若仍在生成，标记为后台进行中（后端会继续跑完落库）。
    if (
      prevSessionIdRef.current &&
      prevSessionIdRef.current !== sessionId &&
      runningRef.current
    ) {
      pendingBackgroundRef.current.add(prevSessionIdRef.current)
    }
    prevSessionIdRef.current = sessionId
    abortRef.current?.abort()
    accRef.current = null
    revokeUrls()
    setMessages([])
    setHitl(null)
    setLoadError(null)
    if (!sessionId) return
    setLoading(true)
    listMessages(sessionId)
      .then((records) => {
        if (cancelled) return
        const history = records.map(fromHistory)
        setMessages(history)
        const pending = [...history]
          .reverse()
          .flatMap((message) =>
            message.content
              .filter((b): b is ContentBlock & { type: 'tool' } => b.type === 'tool')
              .map((b) => b.tool)
              .reverse(),
          )
          .find(
            (tool) =>
              (tool.name === 'ask_clarification' ||
                tool.name === 'confirm_workflow') &&
              !tool.result,
          )
        if (pending) {
          let args: Record<string, unknown> = {}
          try {
            args = pending.args ? JSON.parse(pending.args) : {}
          } catch {
            args = {}
          }
          if (pending.name === 'confirm_workflow') {
            // confirm_workflow args: workflow_name / description / params.
            const preview = args.params
            setHitl({
              taskId: pending.id,
              kind: 'workflow_confirmation',
              question: '',
              clarificationType: 'missing_info',
              options: [],
              workflowName: String(args.workflow_name ?? ''),
              workflowDescription: String(args.description ?? ''),
              inputPreview:
                preview && typeof preview === 'object' && !Array.isArray(preview)
                  ? (preview as Record<string, unknown>)
                  : undefined,
            })
          } else {
            const rawOptions = args.options
            // 解析 fields（兼容 JSON 字符串）
            const rawFields = args.fields
            let parsedFields: import('../types').ClarificationField[] | undefined
            if (Array.isArray(rawFields)) {
              parsedFields = rawFields as import('../types').ClarificationField[]
            } else if (typeof rawFields === 'string' && rawFields) {
              try { parsedFields = JSON.parse(rawFields) } catch { /* ignore */ }
            }
            setHitl({
              taskId: pending.id,
              kind: 'clarification',
              question: String(args.question || '请补充信息后继续。'),
              clarificationType: String(args.clarification_type || 'missing_info'),
              context: typeof args.context === 'string' ? args.context : undefined,
              options: Array.isArray(rawOptions)
                ? rawOptions.map(String)
                : typeof rawOptions === 'string'
                  ? (() => {
                      try {
                        const parsed = JSON.parse(rawOptions)
                        return Array.isArray(parsed) ? parsed.map(String) : []
                      } catch {
                        return []
                      }
                    })()
                  : [],
              fields: parsedFields,
            })
          }
        }
        void Promise.all(
          history.map(async (message) => {
            const attachments = await Promise.all(
              message.attachments.map(async (attachment) => {
                if (!isImageName(attachment.name)) return attachment
                try {
                  const blob =
                    attachment.source === 'upload'
                      ? await getUploadedFile(attachment.id)
                      : await getSessionFile(sessionId, attachment.name)
                  if (cancelled) return attachment
                  const url = URL.createObjectURL(blob)
                  urlsRef.current.add(url)
                  return { ...attachment, kind: 'image' as const, url }
                } catch {
                  return attachment
                }
              }),
            )
            const charts: string[] = []
            if (message.role === 'assistant') {
              const tools = allToolsFromBlocks(message.content)
              const outputNames = new Set(
                tools.flatMap((tool) =>
                  outputAttachments(tool.result ?? '').map((item) => item.name),
                ),
              )
              for (const name of outputNames) {
                if (!name.toLowerCase().endsWith('.json')) continue
                try {
                  const raw = await (await getSessionFile(sessionId, name)).text()
                  if (isChartOption(raw)) charts.push(raw.trim())
                } catch {
                  // Non-previewable outputs stay available through the file card.
                }
              }
            }
            return { id: message.id, attachments, charts }
          }),
        ).then((artifacts) => {
          if (cancelled) return
          const byId = new Map(artifacts.map((item) => [item.id, item]))
          setMessages((current) =>
            current.map((message) => {
              const artifact = byId.get(message.id)
              return artifact
                ? {
                    ...message,
                    attachments: artifact.attachments,
                    charts: artifact.charts,
                  }
                : message
            }),
          )
        })
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : '历史消息加载失败')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
      abortRef.current?.abort()
    }
  }, [sessionId, revokeUrls, reloadTick])

  // 切回"后台仍在生成"的会话：轮询 listMessages，后端落库 agent 回复后触发主加载
  // effect 重新完整加载（含图片/chart artifacts）。未落库时探针只看 role，不下载附件。
  useEffect(() => {
    if (!sessionId || !pendingBackgroundRef.current.has(sessionId)) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const startedAt = Date.now()
    const tick = () => {
      if (cancelled) return
      listMessages(sessionId)
        .then((records) => {
          if (cancelled) return
          const lastIsAgent =
            records.length > 0 && records[records.length - 1]?.role === 'agent'
          if (lastIsAgent) {
            pendingBackgroundRef.current.delete(sessionId)
            setReloadTick((t) => t + 1)
          } else if (Date.now() - startedAt < 90000) {
            timer = setTimeout(tick, 1500)
          } else {
            pendingBackgroundRef.current.delete(sessionId)
          }
        })
        .catch(() => {
          pendingBackgroundRef.current.delete(sessionId)
        })
    }
    timer = setTimeout(tick, 1500)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [sessionId])

  useEffect(() => revokeUrls, [revokeUrls])

  const flush = useCallback((acc: AssistantAccumulator, status: ChatMessage['status'] = 'loading') => {
    // 流内 error 事件记录了错误文本时，后续 flush 一律保持 error 状态并带上 error 字段，
    // 避免被循环内的普通 flush（默认 loading）覆盖丢失。
    const effectiveStatus: ChatMessage['status'] = acc.errorText ? 'error' : status
    const next: ChatMessage = {
      id: acc.id,
      role: 'assistant',
      content: acc.blocks.map((b) => ({ ...b })),
      attachments: Array.from(acc.attachments.values()),
      charts: Array.from(acc.charts.values()),
      status: effectiveStatus,
      error: acc.errorText,
      createdAt: new Date(),
      requestId: acc.requestId,
    }
    setMessages((current) =>
      current.map((message) => (message.id === acc.id ? next : message)),
    )
  }, [])

  const process = useCallback(
    async (events: AsyncGenerator<StreamEvent>, acc: AssistantAccumulator) => {
      // 当前 LLM 轮的 reasoning/text 块引用。thinking/text 的 final 事件
      // （on_chat_model_end）与其增量版之间隔着其他块（事件序：…text_delta →
      // thinking final → text final），不能靠"末尾块"定位——否则 final 会另起
      // 新块，把整段思考/正文重复一遍。tool_call 到达即本轮结束，重置引用；
      // 同轮交错思考块（thinking→text→thinking）也归并到同一 reasoning 块，
      // 与后端持久化结构（final 合并全部块）一致。
      let reasoningBlock: { type: 'reasoning'; text: string } | null = null
      let textBlock: { type: 'text'; text: string } | null = null
      try {
        for await (const event of events) {
          if ((event.type === 'text_delta' || event.type === 'text') && event.content) {
            // text_delta 是流式增量 → 追加
            // text 是 on_chat_model_end 的完整文本 → 覆盖（不追加，避免重复）
            if (!textBlock) {
              textBlock = { type: 'text', text: '' }
              acc.blocks.push(textBlock)
            }
            if (event.type === 'text') textBlock.text = event.content
            else textBlock.text += event.content
          } else if (
            (event.type === 'thinking' || event.type === 'thinking_delta') &&
            event.content
          ) {
            // 同 text：delta 追加 / final 覆盖，按轮内引用定位。
            if (!reasoningBlock) {
              reasoningBlock = { type: 'reasoning', text: '' }
              acc.blocks.push(reasoningBlock)
            }
            if (event.type === 'thinking') reasoningBlock.text = event.content
            else reasoningBlock.text += event.content
          } else if (event.type === 'tool_call') {
            const toolCallId = event.id || ''
            const id = `tool-${allToolsFromBlocks(acc.blocks).length + 1}`
            acc.blocks.push({
              type: 'tool',
              tool: {
                id,
                toolCallId,
                name: event.tool_name || 'tool',
                args: event.args ? JSON.stringify(event.args, null, 2) : undefined,
                auto: event.auto,
                status: 'running',
              },
            })
            // 本轮 LLM 输出结束（thinking/text final 已在其前到达）：
            // 重置轮内块引用，下一轮思考/正文另起新块。
            reasoningBlock = null
            textBlock = null
          } else if (event.type === 'tool_result') {
            // 优先用 tool_call_id 精确配对;退化兜底:找最后一个 running 的 tool block
            // 注意:不要求 content 非空 —— 工具报错时可能只有 status=error 而 content
            // 为空或很短,仍需据此把工具从 running 改为 error/complete。
            const resultId = event.tool_call_id || ''
            const toolBlock = resultId
              ? [...acc.blocks]
                  .reverse()
                  .find(
                    (b): b is ContentBlock & { type: 'tool' } =>
                      b.type === 'tool' && b.tool.toolCallId === resultId,
                  )
              : [...acc.blocks]
                  .reverse()
                  .find(
                    (b): b is ContentBlock & { type: 'tool' } =>
                      b.type === 'tool' && b.tool.status === 'running',
                  )
            const isError = event.status === 'error'
            if (toolBlock) {
              toolBlock.tool.result = event.content
              toolBlock.tool.isError = isError
              toolBlock.tool.status = isError ? 'error' : 'complete'
            }
            for (const attachment of outputAttachments(event.content ?? '')) {
              acc.attachments.set(attachment.id, attachment)
              if (isImageName(attachment.name)) {
                try {
                  const blob = await getSessionFile(sessionId!, attachment.name)
                  const url = URL.createObjectURL(blob)
                  urlsRef.current.add(url)
                  acc.attachments.set(attachment.id, { ...attachment, url })
                } catch {
                  // The file remains downloadable even when inline preview fails.
                }
              } else if (attachment.name.toLowerCase().endsWith('.json')) {
                try {
                  const raw = await (
                    await getSessionFile(sessionId!, attachment.name)
                  ).text()
                  if (isChartOption(raw)) {
                    acc.charts.set(attachment.name, raw.trim())
                  }
                } catch {
                  // A normal JSON output is still shown as a downloadable file.
                }
              }
            }
            onFilesChanged()
          } else if (event.type === 'interrupt') {
            // The interrupt may come from ask_clarification (kind=clarification)
            // or confirm_workflow (kind=workflow_confirmation). Find the
            // pending tool block that triggered it (either name) to grab its id.
            const interruptTool = [...acc.blocks]
              .reverse()
              .find(
                (b) =>
                  b.type === 'tool' &&
                  (b.tool.name === 'ask_clarification' ||
                    b.tool.name === 'confirm_workflow') &&
                  !b.tool.result,
              )
            const taskId =
              (interruptTool?.type === 'tool' && interruptTool.tool.id) || event.interrupt_id || ''
            if (event.kind === 'workflow_confirmation') {
              setHitl({
                taskId,
                kind: 'workflow_confirmation',
                question: '',
                clarificationType: 'missing_info',
                options: [],
                workflowName: event.workflow_name ?? '',
                workflowDescription: event.workflow_description ?? '',
                inputPreview: event.input_preview ?? undefined,
              })
            } else {
              setHitl({
                taskId,
                kind: 'clarification',
                question: event.question ?? '请补充信息后继续。',
                clarificationType: event.clarification_type ?? 'missing_info',
                context: event.context ?? undefined,
                options: event.options ?? [],
                fields: event.fields ?? undefined,
              })
            }
            flush(acc, 'success')
            setRunning(false)
            return
          } else if (event.done) {
            acc.requestId = event.request_id  // §8.2 消息级反馈轮次键
            // 流结束但仍有工具停在 running:说明没收到它的 tool_result(异常中断)。
            // 诚实地标为 error 并补提示文案,而不是伪装成 complete 误导用户。
            for (const block of acc.blocks) {
              if (block.type === 'tool' && block.tool.status === 'running') {
                block.tool.status = 'error'
                block.tool.isError = true
                if (!block.tool.result) {
                  block.tool.result = '工具执行异常,未收到结果'
                }
              }
            }
            flush(acc, 'success')
            onFilesChanged()
            return
          } else if (event.type === 'error') {
            // 不 throw 中断流：记录错误到当前消息，后续仍可能有事件。
            // errorText 写入 acc，循环内的后续 flush 会保持 error 状态。
            const errContent = event.content || 'Agent 执行失败'
            if (!acc.errorText) acc.errorText = errContent
            flush(acc)
          }
          flush(acc)
        }
        flush(acc, 'success')
      } catch (error: unknown) {
        const aborted = error instanceof DOMException && error.name === 'AbortError'
        setMessages((current) =>
          current.map((message) =>
            message.id === acc.id
              ? {
                  ...message,
                  status: aborted ? 'abort' : 'error',
                  error: aborted
                    ? '已停止生成'
                    : error instanceof Error
                      ? error.message
                      : '生成失败',
                }
              : message,
          ),
        )
      }
    },
    [flush, onFilesChanged, sessionId],
  )

  const send = useCallback(
    async (text: string, files: File[], displayText?: string) => {
      if (!agentId || !sessionId || running || hitl) return
      const trimmed = text.trim()
      if (!trimmed && files.length === 0) return
      setRunning(true)
      const userId = genId()
      const assistantId = genId()
      const attachments: AttachmentView[] = files.map((file) => {
        const url = file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined
        if (url) urlsRef.current.add(url)
        return {
          id: `local:${genId()}`,
          name: file.name,
          contentType: file.type || 'application/octet-stream',
          kind: file.type.startsWith('image/') ? 'image' : 'file',
          url,
          source: 'local',
        }
      })
      const acc: AssistantAccumulator = {
        id: assistantId,
        blocks: [],
        attachments: new Map(),
        charts: new Map(),
      }
      accRef.current = acc
      setMessages((current) => [
        ...current,
        {
          id: userId,
          role: 'user',
          content: trimmed ? [{ type: 'text', text: trimmed }] : [],
          displayText,
          attachments,
          charts: [],
          status: 'success',
          createdAt: new Date(),
        },
        {
          id: assistantId,
          role: 'assistant',
          content: [],
          attachments: [],
          charts: [],
          status: 'loading',
          createdAt: new Date(),
        },
      ])
      const controller = new AbortController()
      abortRef.current = controller
      try {
        const uploaded = await Promise.all(
          files.map((file) => uploadSessionFile(sessionId, file)),
        )
        setMessages((current) =>
          current.map((message) =>
            message.id === userId
              ? {
                  ...message,
                  attachments: message.attachments.map((attachment, index) => {
                    const stored = uploaded[index]
                    return stored
                      ? {
                          ...attachment,
                          id: stored.id,
                          name: stored.name,
                          contentType: stored.mime,
                          source: 'upload' as const,
                        }
                      : attachment
                  }),
                }
              : message,
          ),
        )
        await process(
          streamMessage(
            agentId,
            sessionId,
            trimmed,
            uploaded.map((file) => file.id),
            uploaded.map((file) => file.path),
            controller.signal,
            displayText,
          ),
          acc,
        )
      } catch (error: unknown) {
        setMessages((current) =>
          current.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  status: 'error',
                  error: error instanceof Error ? error.message : '附件上传失败',
                }
              : message,
          ),
        )
      } finally {
        setRunning(false)
        abortRef.current = null
        // 触发会话列表刷新:后端会根据第一条消息生成标题,需要回流到侧边栏
        onSessionChanged?.()
      }
    },
    [agentId, hitl, onSessionChanged, process, running, sessionId],
  )

  const answerClarification = useCallback(
    async (answer: string) => {
      if (!agentId || !sessionId || !hitl || running) return
      const clarificationToolId = hitl.taskId
      const acc = accRef.current ?? {
        id: genId(),
        blocks: [] as ContentBlock[],
        attachments: new Map<string, AttachmentView>(),
        charts: new Map<string, string>(),
      }
      if (!accRef.current) {
        accRef.current = acc
        setMessages((current) => [
          ...current,
          {
            id: acc.id,
            role: 'assistant',
            content: [],
            attachments: [],
            charts: [],
            status: 'loading',
          },
        ])
      }
      const controller = new AbortController()
      abortRef.current = controller
      setMessages((current) =>
        current.map((message) => ({
          ...message,
          content: message.content.map((block) =>
            block.type === 'tool' && block.tool.id === clarificationToolId
              ? { ...block, tool: { ...block.tool, result: answer, status: 'complete' as const } }
              : block,
          ),
        })),
      )
      setHitl(null)
      setRunning(true)
      try {
        await process(
          streamConfirmation(agentId, sessionId, answer, controller.signal),
          acc,
        )
      } finally {
        setRunning(false)
        abortRef.current = null
        // 触发会话列表刷新:后端会根据第一条消息生成标题,需要回流到侧边栏
        onSessionChanged?.()
      }
    },
    [agentId, hitl, onSessionChanged, process, running, sessionId],
  )

  // cancel 需要当前 agentId，但为了保持回调 identity 稳定用 ref 同步（与 runningRef 同模式）。
  const cancelAgentRef = useRef<string | null>(agentId)
  cancelAgentRef.current = agentId

  const cancel = useCallback(() => {
    // 先通知服务端停止生成（mid-stream abort：立即打断 LLM token 流/工具执行，
    // 半截回复不落库，可直接开始新对话），再断本地 SSE 连接。
    // 服务端调用是 fire-and-forget——409（已结束）等失败不影响本地停止。
    // 注意：会话切换时的内部 abort（不走 cancel）不通知服务端，后台任务
    // 跑完落库是刻意语义（切回会话能看到完整回复）。
    if (cancelAgentRef.current) void stopAgentStream(cancelAgentRef.current)
    abortRef.current?.abort()
  }, [])

  // ── 语音桥接：VoiceComposer 的 WS 事件写入同一条消息流 ──────────────
  // 语音期间 running=true（send 的早退条件天然互斥：语音 turn 中不能发文本，
  // 文本流式中麦克风按钮也被禁用）。turn.end 复用 reloadTick 全量重载路径
  // （图片/chart artifacts、hitl 恢复都走它）。
  const voiceAccRef = useRef<AssistantAccumulator | null>(null)

  const voiceAppendUserMessage = useCallback((text: string) => {
    const trimmed = text.trim()
    if (!trimmed) return
    setMessages((current) => [
      ...current,
      {
        id: genId(),
        role: 'user',
        content: [{ type: 'text', text: trimmed }],
        attachments: [],
        charts: [],
        status: 'success',
        createdAt: new Date(),
      },
    ])
  }, [])

  const voiceBeginAssistantTurn = useCallback(() => {
    const acc: AssistantAccumulator = {
      id: genId(),
      blocks: [],
      attachments: new Map(),
      charts: new Map(),
    }
    voiceAccRef.current = acc
    setRunning(true)
    setMessages((current) => [
      ...current,
      {
        id: acc.id,
        role: 'assistant',
        content: [],
        attachments: [],
        charts: [],
        status: 'loading',
        createdAt: new Date(),
      },
    ])
  }, [])

  const voiceAppendDelta = useCallback(
    (delta: string) => {
      const acc = voiceAccRef.current
      if (!acc || !delta) return
      const last = acc.blocks[acc.blocks.length - 1]
      if (last && last.type === 'text') {
        last.text += delta
      } else {
        acc.blocks.push({ type: 'text', text: delta })
      }
      flush(acc)
    },
    [flush],
  )

  const voiceFinishTurn = useCallback(() => {
    const acc = voiceAccRef.current
    if (acc) {
      flush(acc, 'success')
      voiceAccRef.current = null
    }
    setRunning(false)
    onFilesChanged()
    onSessionChanged?.()
    setReloadTick((tick) => tick + 1)
  }, [flush, onFilesChanged, onSessionChanged])

  const voiceAbort = useCallback(() => {
    // 语音出错/退出输入模式：清占位状态，不动消息（下一句会重开 turn）。
    voiceAccRef.current = null
    setRunning(false)
  }, [])

  return {
    messages,
    loading,
    running,
    hitl,
    loadError,
    send,
    cancel,
    answerClarification,
    voiceAppendUserMessage,
    voiceBeginAssistantTurn,
    voiceAppendDelta,
    voiceFinishTurn,
    voiceAbort,
  }
}

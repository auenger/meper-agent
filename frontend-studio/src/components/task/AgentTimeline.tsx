/**
 * AgentTimeline — Agent 节点执行过程渲染组件。
 *
 * 把后端 NodeTimelineEntry（thinking / tool_call / tool_result / text / user）
 * 渲染为可折叠卡片：思考过程、工具调用（参数 + 返回 + 状态）、最终回答、用户输入。
 * 自管理展开状态，供 TaskFlowTimeline 的「执行详情」分区使用。
 *
 * 数据来自 GET /tasks/{id}/nodes/{nodeId}/timeline（按需从 checkpointer 读取）。
 */
import { useState, useMemo, type ReactNode } from 'react'
import { Lightbulb, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'
import type { NodeTimelineEntry } from '../../services/tasks-api'
import { Markdown } from '../Markdown'

/* ─── Types ─── */

type ToolStatus = 'running' | 'success' | 'error'

interface TimelineEntry {
  id: string
  type: 'thinking' | 'tool' | 'text' | 'user' | 'error'
  content: string
  toolName?: string
  args?: Record<string, unknown>
  result?: string
  toolStatus?: ToolStatus
}

/* ─── Converter: 后端 entries → UI entries（合并相邻 tool_call + tool_result） ─── */

function toTimelineEntries(entries: NodeTimelineEntry[]): TimelineEntry[] {
  const result: TimelineEntry[] = []
  const pending = new Map<string, { idx: number; entry: TimelineEntry }>()

  entries.forEach((e, i) => {
    if (e.type === 'tool_call') {
      const entry: TimelineEntry = {
        id: `tc-${i}`, type: 'tool', content: '',
        toolName: e.tool_name, args: e.args, toolStatus: 'running',
      }
      const idx = result.length
      result.push(entry)
      if (e.tool_name) pending.set(e.tool_name, { idx, entry })
    } else if (e.type === 'tool_result') {
      const p = e.tool_name ? pending.get(e.tool_name) : undefined
      // 优先结构化 status；无 status 的旧数据才回退文本嗅探
      // （嗅探只认 "Error" 开头——内容中间含 error 字样的正常数据不得误判）
      const isError =
        e.status != null ? e.status === 'error' : (e.content ?? '').startsWith('Error')
      if (p) {
        result[p.idx] = { ...p.entry, result: e.content, toolStatus: isError ? 'error' : 'success' }
        if (e.tool_name) pending.delete(e.tool_name)
      } else {
        result.push({
          id: `tr-${i}`, type: 'tool', content: '',
          toolName: e.tool_name, result: e.content, toolStatus: isError ? 'error' : 'success',
        })
      }
    } else if (e.type === 'tool') {
      result.push({
        id: `tool-${i}`, type: 'tool', content: '',
        toolName: e.tool_name, args: e.args, result: e.content, toolStatus: 'success',
      })
    } else if (e.type === 'thinking') {
      result.push({ id: `think-${i}`, type: 'thinking', content: e.content ?? '' })
    } else if (e.type === 'text') {
      result.push({ id: `text-${i}`, type: 'text', content: e.content ?? '' })
    } else if (e.type === 'user') {
      result.push({ id: `user-${i}`, type: 'user', content: e.content ?? '' })
    }
    // tool_call_start 等瞬态事件跳过
  })

  return result
}

/* ─── 主题色 token ─── */

interface ThemeTokens {
  card: string
  cardBorder: string
  text: string
  muted: string
  codeBg: string
  codeText: string
}

const TOKENS: Record<'light' | 'dark', ThemeTokens> = {
  dark: {
    card: 'bg-[#09090b]', cardBorder: 'border-[#27272a]',
    text: 'text-[#fafafa]', muted: 'text-[#71717a]',
    codeBg: 'bg-[#18181b]', codeText: 'text-[#a1a1aa]',
  },
  light: {
    card: 'bg-slate-50', cardBorder: 'border-slate-200',
    text: 'text-slate-800', muted: 'text-slate-400',
    codeBg: 'bg-white', codeText: 'text-slate-600',
  },
}

const TOOL_STATUS: Record<ToolStatus, { color: string; icon: ReactNode }> = {
  running: { color: '#D97706', icon: <Loader2 size={12} className="animate-spin" /> },
  success: { color: '#10B981', icon: <CheckCircle2 size={12} /> },
  error: { color: '#EF4444', icon: <AlertCircle size={12} /> },
}

/* ─── 单条卡片（自管理展开状态） ─── */

function EntryCard({ entry, theme }: { entry: TimelineEntry; theme: 'light' | 'dark' }) {
  const [expanded, setExpanded] = useState(false)
  const t = TOKENS[theme]

  switch (entry.type) {
    case 'thinking':
      return (
        <div className={`rounded-lg border ${t.cardBorder} ${t.card} overflow-hidden`}>
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center gap-1.5 px-2.5 py-1.5 bg-transparent cursor-pointer text-left hover:opacity-80 transition-opacity"
          >
            <Lightbulb size={12} className="text-[#3B82F6]" />
            <span className="text-[11px] font-medium text-[#3B82F6]">思考过程</span>
            <span className={`text-[10px] ml-auto ${t.muted}`}>{expanded ? '收起' : '展开'}</span>
          </button>
          {expanded && (
            <div className={`px-2.5 pb-2 pt-1.5 text-[11px] text-[#60A5FA] whitespace-pre-wrap leading-relaxed border-t ${t.cardBorder}`}>
              {entry.content}
            </div>
          )}
        </div>
      )

    case 'tool': {
      const status = entry.toolStatus ?? 'running'
      const sm = TOOL_STATUS[status]
      const hasDetail = (!!entry.args && Object.keys(entry.args).length > 0) || !!entry.result
      return (
        <div className={`rounded-lg border ${t.cardBorder} ${t.card} overflow-hidden`}>
          <button
            onClick={hasDetail ? () => setExpanded(!expanded) : undefined}
            className={`w-full flex items-center gap-1.5 px-2.5 py-1.5 bg-transparent text-left transition-opacity ${hasDetail ? 'cursor-pointer hover:opacity-80' : 'cursor-default'}`}
          >
            <span style={{ color: sm.color }}>{sm.icon}</span>
            <span className="text-[11px] font-medium" style={{ color: sm.color }}>
              {entry.toolName || 'unknown_tool'}
            </span>
            {status === 'running' && (
              <span className="text-[10px] opacity-70" style={{ color: sm.color }}>执行中…</span>
            )}
            {hasDetail && (
              <span className={`text-[10px] ml-auto ${t.muted}`}>{expanded ? '收起' : '详情'}</span>
            )}
          </button>
          {expanded && hasDetail && (
            <div className={`px-2.5 pb-2 pt-1.5 space-y-2 border-t ${t.cardBorder}`}>
              {!!entry.args && Object.keys(entry.args).length > 0 && (
                <div>
                  <div className={`text-[10px] font-medium mb-1 ${t.muted}`}>请求参数</div>
                  <pre className={`text-[11px] whitespace-pre-wrap break-all leading-relaxed rounded p-2 max-h-40 overflow-y-auto ${t.codeBg} ${t.codeText}`}>
                    {JSON.stringify(entry.args, null, 2)}
                  </pre>
                </div>
              )}
              {!!entry.result && (
                <div>
                  <div className={`text-[10px] font-medium mb-1 ${t.muted}`}>返回结果</div>
                  <pre className={`text-[11px] whitespace-pre-wrap break-all leading-relaxed rounded p-2 max-h-48 overflow-y-auto ${t.codeBg} ${t.codeText}`}>
                    {entry.result}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      )
    }

    case 'user':
      return (
        <div className={`rounded-lg border ${t.cardBorder} px-2.5 py-1.5 ${theme === 'dark' ? 'bg-[#18181b]' : 'bg-slate-100'}`}>
          <div className={`text-[10px] mb-0.5 ${t.muted}`}>用户输入</div>
          <div className={`text-[12px] whitespace-pre-wrap leading-relaxed ${t.text}`}>{entry.content}</div>
        </div>
      )

    case 'error':
      return (
        <div className="rounded-lg border border-[#EF4444]/30 bg-[#EF4444]/5 px-2.5 py-1.5">
          <div className="text-[12px] text-[#EF4444] whitespace-pre-wrap">{entry.content}</div>
        </div>
      )

    case 'text':
    default:
      // agent 最终回答：天然含 Markdown 格式（列表/代码/标题），直接 md 渲染（参考 silieco）。
      return (
        <div className={`rounded-lg border ${t.cardBorder} ${t.card} px-2.5 py-1.5`}>
          <Markdown content={entry.content} />
        </div>
      )
  }
}

/* ─── 公共组件 ─── */

export interface AgentTimelineProps {
  entries: NodeTimelineEntry[]
  theme?: 'light' | 'dark'
}

export default function AgentTimeline({ entries, theme = 'dark' }: AgentTimelineProps) {
  const items = useMemo(() => toTimelineEntries(entries), [entries])
  if (items.length === 0) {
    return <div className={`text-xs text-center py-4 ${TOKENS[theme].muted}`}>暂无执行过程数据</div>
  }
  return (
    <div className="flex flex-col gap-1.5">
      {items.map((entry) => (
        <EntryCard key={entry.id} entry={entry} theme={theme} />
      ))}
    </div>
  )
}

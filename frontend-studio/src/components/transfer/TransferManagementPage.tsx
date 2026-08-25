/**
 * TransferManagementPage — 数据导入导出中心（admin 专属独立页）。
 *
 * 两 Tab：
 *  - 导出：按类型分组浏览并勾选资源 → 「包含依赖」开关 → 导出 afpkg 包
 *  - 导入：TransferImportPanel（上传 → dry_run 预检 → 确认导入）
 *
 * 由原先散落在各资源页的行内入口收拢而来（2026-08-25）：统一入口、统一
 * 权限视图，跨类型混选导出（如一次带走 2 个智能体 + 1 个工作流）成为
 * 页面原生能力。后端零改动（/transfer/export 支持任意 resources 组合）。
 */
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FileUp, ArrowLeftRight, Loader2, BookOpen } from 'lucide-react'
import { agentApi, agentKeys } from '../../services/agent-api'
import { workflowsApi, workflowKeys } from '../../services/workflows-api'
import { toolsApi, toolKeys } from '../../services/tools-api'
import { mcpApi, mcpKeys } from '../../services/mcp-api'
import { knowledgeApi, knowledgeKeys } from '../../services/knowledge-api'
import { modelApi, modelKeys } from '../../services/model-api'
import {
  exportPackage,
  type TransferKind,
  type TransferResourceRef,
} from '../../services/transfer-api'
import { toast } from '../ui/toast'
import { getErrorMessage } from '../../lib/api-client'
import { TransferImportPanel } from './TransferImportPanel'

/** 一个可勾选资源行的最小形状。 */
interface PickRow {
  id: string
  name: string
  desc?: string
  /** 禁选原因（如 vector KB 不支持导出）；可选项正常勾选 */
  disabledReason?: string
}

/** 可勾选导出的类型（mcp_category 由 MCP 连接的依赖闭包自动携带，不单独勾选）。 */
type SelectableKind = Exclude<TransferKind, 'mcp_category'>

/** 类型分组：label 同时用于勾选汇总与 manifest 展示。 */
const GROUPS: { kind: SelectableKind; label: string; hint: string }[] = [
  { kind: 'agent', label: '智能体', hint: '含提示词卡槽 / 工具绑定 / 模型引用' },
  { kind: 'workflow', label: '工作流', hint: '图结构 + 节点配置' },
  { kind: 'skill', label: '技能', hint: 'SKILL.md 整目录（官方技能）' },
  { kind: 'tool', label: '自定义工具', hint: 'OpenAPI / 代码 / 预置工具' },
  { kind: 'mcp_connection', label: 'MCP 连接', hint: '连接配置（凭证不随包迁移）' },
  { kind: 'knowledge_base', label: '知识库', hint: '仅文档树（Wiki）型；向量库不支持' },
  { kind: 'model', label: '模型配置', hint: '接口配置（密钥不随包迁移）' },
]

export function TransferManagementPage({ theme = 'dark' }: { theme?: 'dark' | 'light' }) {
  const [tab, setTab] = useState<'export' | 'import'>('export')
  const box = theme === 'dark' ? 'bg-[#18181b] border-[#27272a]' : 'bg-white border-slate-200'
  const muted = theme === 'dark' ? 'text-[#71717a]' : 'text-slate-400'

  return (
    <div className="space-y-5">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <ArrowLeftRight className="w-5 h-5 text-indigo-400" />
            数据导入导出
          </h2>
          <p className={`text-xs mt-1 ${muted}`}>
            将智能体、工作流、技能、MCP 连接、知识库、模型配置打包迁移到其他环境，或从包恢复。敏感凭证（模型密钥 / MCP 认证）不随包迁移。
          </p>
        </div>
        <div className={`flex items-center gap-1 p-1 rounded-lg border ${box}`}>
          {([
            { key: 'export', label: '导出', icon: Download },
            { key: 'import', label: '导入', icon: FileUp },
          ] as const).map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-semibold transition cursor-pointer ${
                tab === t.key
                  ? 'bg-indigo-600 text-white'
                  : theme === 'dark'
                    ? 'text-[#a1a1aa] hover:text-white hover:bg-[#27272a]'
                    : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100'
              }`}
            >
              <t.icon className="w-4 h-4" />
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className={`rounded-xl border p-6 ${box}`}>
        {tab === 'export' ? <ExportTab theme={theme} /> : <ImportTab />}
      </div>
    </div>
  )
}

/* ══════════════ 导出 Tab ══════════════ */

function ExportTab({ theme }: { theme: 'dark' | 'light' }) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set()) // key = `${kind}:${id}`
  const [includeDeps, setIncludeDeps] = useState(true)
  const [busy, setBusy] = useState(false)
  const [groupFilter, setGroupFilter] = useState<SelectableKind | 'all'>('all')

  const muted = theme === 'dark' ? 'text-[#71717a]' : 'text-slate-400'

  // 各类型资源清单（一次一页 100/200 条，管理页足够；更多时分页可后续加）
  const agentsQ = useQuery({
    queryKey: agentKeys.list({ page: 1, page_size: 100, status: 'all' }),
    queryFn: () => agentApi.list({ page: 1, page_size: 100, status: 'all' }),
  })
  const workflowsQ = useQuery({
    queryKey: workflowKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => workflowsApi.list({ page: 1, page_size: 100 }),
  })
  // tools 一次拉全量（含 mcp 产物），前端按 source 分组
  const toolsQ = useQuery({
    queryKey: toolKeys.list({ page: 1, page_size: 200 }),
    queryFn: () => toolsApi.list({ page: 1, page_size: 200 }),
  })
  const mcpQ = useQuery({
    queryKey: mcpKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => mcpApi.list({ page: 1, page_size: 100 }),
  })
  const kbQ = useQuery({
    queryKey: knowledgeKeys.list({ page: 1, page_size: 100 }),
    queryFn: () => knowledgeApi.list({ page: 1, page_size: 100 }),
  })
  const modelsQ = useQuery({
    queryKey: modelKeys.list({ page_size: 100 }),
    queryFn: () => modelApi.list({ page_size: 100 }),
  })

  // 组装各分组行（KB 的 vector 型禁选；tools 按 source 分组）
  const rowsByKind = useMemo(() => {
    const allTools = toolsQ.data?.items ?? []
    const skills = allTools.filter((t) => t.source === 'markdown')
    const customTools = allTools.filter((t) => ['openapi', 'code', 'prebuilt'].includes(t.source))
    return {
      agent: (agentsQ.data?.items ?? []).map((a): PickRow => ({ id: a.id, name: a.name, desc: a.description })),
      workflow: (workflowsQ.data?.items ?? []).map((w): PickRow => ({ id: w.id, name: w.name, desc: w.description })),
      skill: skills.map((t): PickRow => ({ id: t.id, name: t.name, desc: t.description })),
      tool: customTools.map((t): PickRow => ({ id: t.id, name: t.name, desc: t.description })),
      mcp_connection: (mcpQ.data?.items ?? []).map((c): PickRow => ({ id: c.id, name: c.name, desc: c.description })),
      knowledge_base: (kbQ.data?.items ?? []).map((k): PickRow => ({
        id: k.id,
        name: k.name,
        desc: k.description,
        disabledReason: k.type !== 'tree' ? '向量库不支持导出' : undefined,
      })),
      model: (modelsQ.data?.items ?? []).map((m): PickRow => ({ id: m.id, name: m.name, desc: m.model_id })),
    } satisfies Record<SelectableKind, PickRow[]>
  }, [agentsQ.data, workflowsQ.data, toolsQ.data, mcpQ.data, kbQ.data, modelsQ.data])

  const loading =
    agentsQ.isLoading || workflowsQ.isLoading || toolsQ.isLoading ||
    mcpQ.isLoading || kbQ.isLoading || modelsQ.isLoading

  const toggle = (kind: SelectableKind, id: string, disabled?: boolean) => {
    if (disabled) return
    const key = `${kind}:${id}`
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const toggleGroup = (kind: SelectableKind) => {
    const rows = rowsByKind[kind].filter((r) => !r.disabledReason)
    const keys = rows.map((r) => `${kind}:${r.id}`)
    const allSelected = keys.every((k) => selected.has(k))
    setSelected((prev) => {
      const next = new Set(prev)
      keys.forEach((k) => (allSelected ? next.delete(k) : next.add(k)))
      return next
    })
  }

  const selectedCount = selected.size
  const selectedByKind = useMemo(() => {
    const m = new Map<SelectableKind, number>()
    selected.forEach((k) => {
      const kind = k.split(':')[0] as SelectableKind
      m.set(kind, (m.get(kind) ?? 0) + 1)
    })
    return m
  }, [selected])

  const runExport = async () => {
    if (selectedCount === 0) return
    const resources: TransferResourceRef[] = [...selected].map((k) => {
      const [kind, ...rest] = k.split(':')
      return { kind: kind as TransferKind, id: rest.join(':') }
    })
    setBusy(true)
    try {
      await exportPackage(resources, includeDeps)
      toast.success(`已导出 ${resources.length} 项资源，开始下载`)
    } catch (e) {
      toast.error(getErrorMessage(e, '导出失败'))
    } finally {
      setBusy(false)
    }
  }

  const refreshLists = () => {
    void queryClient.invalidateQueries({ queryKey: agentKeys.all })
    void queryClient.invalidateQueries({ queryKey: workflowKeys.all })
    void queryClient.invalidateQueries({ queryKey: toolKeys.all })
    void queryClient.invalidateQueries({ queryKey: mcpKeys.all })
    void queryClient.invalidateQueries({ queryKey: knowledgeKeys.all })
    void queryClient.invalidateQueries({ queryKey: modelKeys.all })
  }

  const rowBorder = theme === 'dark' ? 'border-[#27272a]' : 'border-slate-200'
  const hoverBg = theme === 'dark' ? 'hover:bg-[#121214]' : 'hover:bg-slate-50'

  return (
    <div className="space-y-4">
      {/* 类型筛选 */}
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          onClick={() => setGroupFilter('all')}
          className={`px-2.5 py-1 rounded-full border text-xs font-semibold transition cursor-pointer ${
            groupFilter === 'all'
              ? 'border-indigo-600 bg-indigo-950/30 text-indigo-300'
              : theme === 'dark'
                ? 'border-[#27272a] text-[#a1a1aa] hover:text-white'
                : 'border-slate-200 text-slate-500 hover:text-slate-800'
          }`}
        >
          全部类型
        </button>
        {GROUPS.map((g) => {
          const count = rowsByKind[g.kind].filter((r) => !r.disabledReason).length
          return (
            <button
              key={g.kind}
              onClick={() => setGroupFilter(g.kind)}
              className={`px-2.5 py-1 rounded-full border text-xs font-semibold transition cursor-pointer ${
                groupFilter === g.kind
                  ? 'border-indigo-600 bg-indigo-950/30 text-indigo-300'
                  : theme === 'dark'
                    ? 'border-[#27272a] text-[#a1a1aa] hover:text-white'
                    : 'border-slate-200 text-slate-500 hover:text-slate-800'
              }`}
            >
              {g.label} {count}
            </button>
          )
        })}
      </div>

      {/* 分组列表 */}
      {loading ? (
        <div className="flex items-center justify-center gap-2 py-12 text-sm text-[#71717a]">
          <Loader2 className="w-4 h-4 animate-spin" /> 加载资源清单…
        </div>
      ) : (
        <div className="space-y-4 max-h-[52vh] overflow-y-auto pr-1">
          {GROUPS.filter((g) => groupFilter === 'all' || groupFilter === g.kind).map((g) => {
            const rows = rowsByKind[g.kind]
            const pickable = rows.filter((r) => !r.disabledReason)
            const allSelected = pickable.length > 0 && pickable.every((r) => selected.has(`${g.kind}:${r.id}`))
            return (
              <div key={g.kind} className={`rounded-lg border ${rowBorder} overflow-hidden`}>
                <div className={`flex items-center justify-between px-3 py-2 border-b ${rowBorder} ${theme === 'dark' ? 'bg-[#121214]' : 'bg-slate-50'}`}>
                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={() => toggleGroup(g.kind)}
                      disabled={pickable.length === 0}
                      className="accent-indigo-600"
                    />
                    <span className="text-xs font-bold text-white">{g.label}</span>
                    <span className={`text-[10px] ${muted}`}>{rows.length} 项 · {g.hint}</span>
                  </label>
                  {selectedByKind.get(g.kind) && (
                    <span className="text-[10px] text-indigo-400 font-mono">已选 {selectedByKind.get(g.kind)}</span>
                  )}
                </div>
                {rows.length === 0 ? (
                  <p className={`px-3 py-3 text-xs ${muted}`}>暂无资源</p>
                ) : (
                  <ul className="divide-y" style={{ borderColor: 'transparent' }}>
                    {rows.map((r) => {
                      const key = `${g.kind}:${r.id}`
                      const checked = selected.has(key)
                      return (
                        <li key={key} className={`px-3 py-2`}>
                          <label
                            className={`flex items-center gap-2.5 ${r.disabledReason ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'} rounded px-1 py-0.5 -mx-1 ${r.disabledReason ? '' : hoverBg}`}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={!!r.disabledReason}
                              onChange={() => toggle(g.kind, r.id, !!r.disabledReason)}
                              className="accent-indigo-600"
                            />
                            <span className="text-xs text-white font-medium truncate">{r.name}</span>
                            {r.disabledReason && (
                              <span className="text-[10px] text-amber-400 flex items-center gap-0.5">
                                <BookOpen className="w-3 h-3" />{r.disabledReason}
                              </span>
                            )}
                            {r.desc && <span className={`text-[10px] truncate flex-1 text-right ${muted}`}>{r.desc}</span>}
                            <span className={`text-[10px] font-mono shrink-0 ${muted}`}>{r.id.slice(0, 14)}…</span>
                          </label>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* 底部操作栏 */}
      <div className={`flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pt-4 border-t ${rowBorder}`}>
        <label className="flex items-start gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={includeDeps}
            onChange={(e) => setIncludeDeps(e.target.checked)}
            className="mt-0.5 accent-indigo-600"
          />
          <span className="text-xs text-[#a1a1aa] leading-relaxed">
            包含依赖资源
            <span className={`block text-[10px] ${muted}`}>
              自动携带绑定：技能 / MCP 连接 / 文档树知识库 / 模型 / 子工作流等（向量知识库除外）
            </span>
          </span>
        </label>
        <div className="flex items-center gap-3">
          <span className="text-xs text-[#a1a1aa] font-mono">
            已选 {selectedCount} 项
            {selectedCount > 0 && (
              <span className="text-[#71717a]">
                （{[...selectedByKind.entries()].map(([k, n]) => `${GROUPS.find((g) => g.kind === k)?.label}×${n}`).join(' ')}）
              </span>
            )}
          </span>
          <button
            onClick={runExport}
            disabled={busy || selectedCount === 0}
            className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold cursor-pointer disabled:opacity-60 flex items-center gap-2"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
            {busy ? '导出中…' : '导出并下载'}
          </button>
        </div>
      </div>
    </div>
  )
}

/* ══════════════ 导入 Tab ══════════════ */

function ImportTab() {
  const queryClient = useQueryClient()
  const refresh = () => {
    // 导入可能落到任意集合 — 全部资源清单一起失效
    void queryClient.invalidateQueries({ queryKey: agentKeys.all })
    void queryClient.invalidateQueries({ queryKey: workflowKeys.all })
    void queryClient.invalidateQueries({ queryKey: toolKeys.all })
    void queryClient.invalidateQueries({ queryKey: mcpKeys.all })
    void queryClient.invalidateQueries({ queryKey: knowledgeKeys.all })
    void queryClient.invalidateQueries({ queryKey: modelKeys.all })
  }
  return <TransferImportPanel onImported={refresh} />
}

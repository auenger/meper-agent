/**
 * Transfer API — 资源导入导出（afpkg 包）。
 *
 * 统一走 POST /api/v1/transfer/export 与 /import（admin/developer），
 * 格式/权限/报告只有一份实现，各列表页只是组装 resources 参数。
 */
import { apiClient, getErrorMessage } from '../lib/api-client'

export type TransferKind =
  | 'agent'
  | 'workflow'
  | 'skill'
  | 'tool'
  | 'mcp_connection'
  | 'mcp_category'
  | 'model'
  | 'knowledge_base'

export interface TransferResourceRef {
  kind: TransferKind
  id: string
}

export interface ImportItem {
  kind: string
  name: string
  original_id: string
  new_id: string
  existing_id: string
  renamed_from: string
}

export interface ImportWarningItem {
  kind: string
  name: string
  field: string
  message: string
}

export interface ImportErrorItem {
  kind: string
  name: string
  message: string
}

export interface ImportReport {
  dry_run: boolean
  summary: { created: number; reused: number; skipped: number; warnings: number; errors: number }
  created: ImportItem[]
  reused: ImportItem[]
  skipped: ImportItem[]
  warnings: ImportWarningItem[]
  errors: ImportErrorItem[]
}

/** 资源类型中文标签（报告展示用）。 */
export const TRANSFER_KIND_LABELS: Record<string, string> = {
  agent: '智能体',
  workflow: '工作流',
  skill: '技能',
  tool: '自定义工具',
  mcp_connection: 'MCP 连接',
  mcp_category: 'MCP 分组',
  model: '模型',
  knowledge_base: '知识库',
}

/** 导出资源为 afpkg 包并触发浏览器下载。 */
export async function exportPackage(
  resources: TransferResourceRef[],
  includeDependencies = true,
): Promise<void> {
  const res = await apiClient.post('/api/v1/transfer/export', {
    resources,
    include_dependencies: includeDependencies,
  }, { responseType: 'blob' })
  const blob = res.data instanceof Blob ? res.data : new Blob([res.data])
  const disposition = (res.headers?.['content-disposition'] as string | undefined) ?? ''
  // 优先 filename*（RFC 5987），回退 filename
  let filename = `agentflow-export-${new Date().toISOString().slice(0, 10)}.zip`
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(disposition)
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  if (star?.[1]) filename = decodeURIComponent(star[1].trim())
  else if (plain?.[1]) filename = plain[1].trim()

  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } finally {
    URL.revokeObjectURL(url)
  }
}

export interface ImportOptions {
  dryRun?: boolean
  reuseExisting?: boolean
  conflict?: 'rename' | 'skip'
  autoDiscover?: boolean
}

/** 导入 afpkg 包（dryRun=true 时仅预检，返回同构报告）。 */
export async function importPackage(file: File, opts: ImportOptions = {}): Promise<ImportReport> {
  const form = new FormData()
  form.append('file', file)
  form.append('dry_run', String(opts.dryRun ?? false))
  form.append('reuse_existing', String(opts.reuseExisting ?? true))
  form.append('conflict', opts.conflict ?? 'rename')
  form.append('auto_discover', String(opts.autoDiscover ?? true))
  try {
    const res = await apiClient.post<ImportReport>('/api/v1/transfer/import', form, {
      // apiClient 实例默认头是 application/json，FormData 必须显式声明 multipart
      // （与 tools-api / knowledge-api 的上传同款模式），否则后端按 JSON 解析 → 422。
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300_000,
    })
    return res.data
  } catch (e) {
    throw new Error(getErrorMessage(e, '导入失败'))
  }
}

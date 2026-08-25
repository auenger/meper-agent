/**
 * TransferImportPanel — afpkg 导入面板（导入导出中心的「导入」Tab 内嵌版）。
 *
 * 流程：选 zip → 用当前选项跑 dry_run 预检 → 展示报告（新建/复用/跳过/
 * 警告/错误）→ 确认导入 → onImported 回调（刷新列表）。
 * 由 TransferImportDialog 演化而来（去弹窗外壳，成为页面 Tab 内容）。
 */
import { useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, FileArchive, Loader2, Upload, XCircle } from 'lucide-react'
import {
  importPackage,
  TRANSFER_KIND_LABELS,
  type ImportReport,
} from '../../services/transfer-api'
import { toast } from '../ui/toast'

type Phase = 'select' | 'previewing' | 'report' | 'importing' | 'done'

export function TransferImportPanel({ onImported }: { onImported: () => void }) {
  const [phase, setPhase] = useState<Phase>('select')
  const [file, setFile] = useState<File | null>(null)
  const [reuseExisting, setReuseExisting] = useState(true)
  const [conflict, setConflict] = useState<'rename' | 'skip'>('rename')
  const [report, setReport] = useState<ImportReport | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const runPreview = (f: File) => {
    setFile(f)
    setPhase('previewing')
    setReport(null)
    importPackage(f, { dryRun: true, reuseExisting, conflict })
      .then((r) => {
        setReport(r)
        setPhase('report')
      })
      .catch((e: Error) => {
        toast.error(e.message)
        setPhase('select')
      })
  }

  const runImport = () => {
    if (!file) return
    setPhase('importing')
    importPackage(file, { dryRun: false, reuseExisting, conflict })
      .then((r) => {
        setReport(r)
        setPhase('done')
        toast.success(
          `导入完成：新建 ${r.summary.created}，复用 ${r.summary.reused}` +
            (r.summary.errors ? `，失败 ${r.summary.errors}` : ''),
        )
        onImported()
      })
      .catch((e: Error) => {
        toast.error(e.message)
        setPhase('report')
      })
  }

  const busy = phase === 'previewing' || phase === 'importing'
  const kindLabel = (k: string) => TRANSFER_KIND_LABELS[k] ?? k

  return (
    <div className="space-y-4">
      {/* 文件选择 */}
      <div
        onClick={() => !busy && phase !== 'done' && inputRef.current?.click()}
        className={`border-2 border-dashed rounded-xl p-8 text-center transition ${
          busy || phase === 'done'
            ? 'border-[#27272a] cursor-default'
            : 'border-[#3f3f46] hover:border-indigo-600 cursor-pointer'
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) runPreview(f)
            e.target.value = ''
          }}
        />
        {phase === 'previewing' ? (
          <div className="flex items-center justify-center gap-2 text-[#a1a1aa] text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> 正在解析包并预检…
          </div>
        ) : file ? (
          <div className="space-y-1">
            <p className="text-sm text-[#a1a1aa] flex items-center justify-center gap-1.5">
              <FileArchive className="w-4 h-4 text-indigo-400" />
              {file.name}（{(file.size / 1024 / 1024).toFixed(2)} MB）
            </p>
            {(phase === 'select' || phase === 'report') && (
              <p className="text-[10px] text-[#71717a]">点击重新选择</p>
            )}
          </div>
        ) : (
          <div className="space-y-1.5">
            <Upload className="w-6 h-6 text-[#52525b] mx-auto" />
            <p className="text-sm text-[#a1a1aa]">点击选择 afpkg 资源包（.zip）</p>
            <p className="text-[10px] text-[#71717a]">从本平台或其他实例导出的包，导入前会先预检并生成报告</p>
          </div>
        )}
      </div>

      {/* 导入选项（预检/报告阶段可调整，调整后重新预检更准确） */}
      {(phase === 'select' || phase === 'report') && (
        <div className="grid sm:grid-cols-2 gap-3">
          <label className="flex items-start gap-2 cursor-pointer select-none p-3 rounded-lg border border-[#27272a] bg-[#121214]">
            <input
              type="checkbox"
              checked={reuseExisting}
              onChange={(e) => setReuseExisting(e.target.checked)}
              className="mt-0.5 accent-indigo-600"
            />
            <span className="text-xs text-[#a1a1aa] leading-relaxed">
              复用已有同名资源
              <span className="block text-[10px] text-[#71717a]">命中同名时直接把引用指向本实例已有资源，不重复创建</span>
            </span>
          </label>
          <div className="p-3 rounded-lg border border-[#27272a] bg-[#121214] space-y-1.5">
            <span className="text-xs text-[#a1a1aa]">名称冲突时</span>
            <div className="flex gap-2">
              {(['rename', 'skip'] as const).map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setConflict(c)}
                  className={`px-2.5 py-1 rounded border text-xs transition cursor-pointer ${
                    conflict === c
                      ? 'border-indigo-600 bg-indigo-950/30 text-white'
                      : 'border-[#27272a] text-[#71717a] hover:text-white'
                  }`}
                >
                  {c === 'rename' ? '自动改名' : '跳过该项'}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-[#71717a]">自动改名生成「原名-imported」；模型的技术标识冲突时始终跳过</p>
          </div>
          {phase === 'report' && (
            <p className="sm:col-span-2 text-[10px] text-amber-400/80">
              修改选项后请点击下方「重新预检」以获得准确的报告。
            </p>
          )}
        </div>
      )}

      {/* 预检 / 导入报告 */}
      {report && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="px-2 py-1 rounded bg-indigo-950/40 border border-indigo-700/40 text-indigo-300 font-semibold">
              {phase === 'done' ? '已新建' : '将新建'} {report.summary.created}
            </span>
            <span className="px-2 py-1 rounded bg-[#27272a] text-zinc-300 font-semibold">复用 {report.summary.reused}</span>
            {report.summary.skipped > 0 && (
              <span className="px-2 py-1 rounded bg-[#27272a] text-zinc-400 font-semibold">跳过 {report.summary.skipped}</span>
            )}
            {report.summary.warnings > 0 && (
              <span className="px-2 py-1 rounded bg-amber-950/40 border border-amber-700/40 text-amber-300 font-semibold flex items-center gap-1">
                <AlertTriangle className="w-3 h-3" /> 提示 {report.summary.warnings}
              </span>
            )}
            {report.summary.errors > 0 && (
              <span className="px-2 py-1 rounded bg-rose-950/40 border border-rose-700/40 text-rose-300 font-semibold flex items-center gap-1">
                <XCircle className="w-3 h-3" /> 失败 {report.summary.errors}
              </span>
            )}
          </div>

          {(['created', 'reused', 'skipped'] as const).map((section) =>
            report[section].length > 0 && (
              <div key={section} className="rounded-lg border border-[#27272a] bg-[#121214] overflow-hidden">
                <div className="px-3 py-1.5 border-b border-[#27272a] text-[10px] font-bold uppercase tracking-wide text-[#71717a]">
                  {section === 'created' ? (phase === 'done' ? '已新建' : '将新建') : section === 'reused' ? '复用已有' : '跳过'}
                </div>
                <ul className="divide-y divide-[#27272a]/60 max-h-40 overflow-y-auto">
                  {report[section].map((it, i) => (
                    <li key={`${section}-${i}`} className="px-3 py-1.5 flex items-center gap-2 text-xs">
                      {section === 'created' ? (
                        <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0" />
                      ) : (
                        <span className="w-3 h-3 shrink-0" />
                      )}
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#27272a] text-zinc-300 font-semibold shrink-0">
                        {kindLabel(it.kind)}
                      </span>
                      <span className="text-white truncate">
                        {it.name}
                        {it.renamed_from && (
                          <span className="text-[10px] text-amber-400 ml-1">（由 {it.renamed_from} 改名）</span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ),
          )}

          {report.warnings.length > 0 && (
            <div className="rounded-lg border border-amber-700/40 bg-amber-950/20 overflow-hidden">
              <div className="px-3 py-1.5 border-b border-amber-700/30 text-[10px] font-bold uppercase tracking-wide text-amber-400 flex items-center gap-1">
                <AlertTriangle className="w-3 h-3" /> 需要注意（敏感凭证不随包迁移，导入后请补填）
              </div>
              <ul className="divide-y divide-amber-700/20 max-h-48 overflow-y-auto">
                {report.warnings.map((w, i) => (
                  <li key={i} className="px-3 py-1.5 text-xs text-[#d4d4d8] leading-relaxed">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#27272a] text-zinc-300 font-semibold mr-1.5">
                      {kindLabel(w.kind)}{w.name ? ` · ${w.name}` : ''}
                    </span>
                    {w.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.errors.length > 0 && (
            <div className="rounded-lg border border-rose-700/40 bg-rose-950/20 overflow-hidden">
              <div className="px-3 py-1.5 border-b border-rose-700/30 text-[10px] font-bold uppercase tracking-wide text-rose-400 flex items-center gap-1">
                <XCircle className="w-3 h-3" /> 导入失败项
              </div>
              <ul className="divide-y divide-rose-700/20">
                {report.errors.map((er, i) => (
                  <li key={i} className="px-3 py-1.5 text-xs text-[#d4d4d8] leading-relaxed">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#27272a] text-zinc-300 font-semibold mr-1.5">
                      {kindLabel(er.kind)}{er.name ? ` · ${er.name}` : ''}
                    </span>
                    {er.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {phase === 'importing' && (
        <div className="flex items-center justify-center gap-2 text-[#a1a1aa] text-sm py-4">
          <Loader2 className="w-4 h-4 animate-spin" /> 正在导入（含 MCP 连接 discover，可能较慢）…
        </div>
      )}

      {/* 操作栏 */}
      <div className="flex justify-end gap-3 pt-2 border-t border-[#27272a]">
        {phase === 'done' ? (
          <button
            onClick={() => { setPhase('select'); setFile(null); setReport(null) }}
            className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold cursor-pointer"
          >
            继续导入下一个包
          </button>
        ) : phase === 'report' ? (
          <>
            <button
              onClick={() => file && runPreview(file)}
              disabled={busy}
              className="px-4 py-2 border border-[#27272a] hover:bg-[#18181b] text-slate-400 hover:text-white rounded-lg text-xs font-semibold cursor-pointer disabled:opacity-60"
            >
              重新预检
            </button>
            <button
              onClick={runImport}
              disabled={busy || !report || report.summary.created + report.summary.reused === 0}
              className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold cursor-pointer disabled:opacity-60 flex items-center gap-2"
            >
              确认导入
            </button>
          </>
        ) : null}
      </div>
    </div>
  )
}

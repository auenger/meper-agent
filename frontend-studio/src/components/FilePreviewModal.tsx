/**
 * FilePreviewModal — 可拖拽缩放的文件预览弹窗（共享件）。
 *
 * 从 SessionFilesPanel 抽出的公共预览容器，对话与任务产物共用同一套预览体验：
 * - 居中弹窗 + backdrop blur + Esc 关闭
 * - 右下角拖拽手柄调整宽高（最小 360×240）
 * - 头部：文件名 + 下载按钮 + 关闭
 * - 主体：嵌 <FilePreview>（md/html/json/image/text 富渲染）
 *
 * 纯展示：不负责拉取文件内容。父组件把已加载好的 text / imageUrl 喂进来，
 * 与 FilePreview 的「父加载、子渲染」哲学一致。
 */
import { useState, useEffect, useRef } from 'react'
import { Download, X } from 'lucide-react'
import { FilePreview } from './FilePreview'

/** 预览弹窗可拖拽调整的最小宽高（px） */
const MIN_W = 360
const MIN_H = 240

export interface FilePreviewModalProps {
  /** 是否打开（关闭时返回 null） */
  open: boolean
  /** 文件名（含扩展名），用于标题与渲染类型判定 */
  filename: string
  /** mime 类型（可选，辅助判定） */
  mime?: string
  /** 文本类内容（md/html/json/text）；图片类为 undefined */
  text?: string
  /** 图片 dataURL / blobURL */
  imageUrl?: string
  /** 关闭回调 */
  onClose: () => void
  /** 下载回调（可选，不传则隐藏下载按钮） */
  onDownload?: () => void
}

export function FilePreviewModal({
  open,
  filename,
  mime,
  text,
  imageUrl,
  onClose,
  onDownload,
}: FilePreviewModalProps) {
  // 预览弹窗尺寸（可手动拖拽调整）；null = 用默认 CSS 约束（max-w-3xl / max-h-[85vh]）
  const [previewSize, setPreviewSize] = useState<{ width: number; height: number } | null>(null)
  const resizingRef = useRef(false)
  const previewPanelRef = useRef<HTMLDivElement>(null)

  // 重置尺寸：每次打开新文件时回到默认
  useEffect(() => {
    if (open) setPreviewSize(null)
  }, [open, filename])

  // Esc 键关闭预览弹窗（弹窗打开时监听，关闭后移除）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 拖拽右下角手柄调整宽高：mousemove 计算新尺寸，mouseup 结束
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!resizingRef.current) return
      const panel = previewPanelRef.current
      if (!panel) return
      const rect = panel.getBoundingClientRect()
      // 以弹窗左上角为基准，鼠标坐标即新的右下角
      const width = Math.min(Math.max(e.clientX - rect.left, MIN_W), window.innerWidth - 32)
      const height = Math.min(Math.max(e.clientY - rect.top, MIN_H), window.innerHeight - 32)
      setPreviewSize({ width, height })
    }
    const onUp = () => {
      resizingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  if (!open) return null

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    resizingRef.current = true
    document.body.style.cursor = 'nwse-resize'
    document.body.style.userSelect = 'none'
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        ref={previewPanelRef}
        className="bg-[#18181b] rounded-xl shadow-2xl flex flex-col border border-[#27272a] relative"
        style={{
          width: previewSize?.width ?? 'min(48rem, calc(100vw - 2rem))',
          height: previewSize?.height ?? 'min(85vh, calc(100vh - 2rem))',
          maxWidth: 'calc(100vw - 2rem)',
          maxHeight: 'calc(100vh - 2rem)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#27272a]">
          <span className="text-xs font-bold text-white truncate">{filename}</span>
          <div className="flex items-center gap-2 shrink-0">
            {onDownload && (
              <button
                onClick={onDownload}
                className="flex items-center gap-1 px-2 py-1 rounded-md bg-[#1E5EFF] hover:bg-[#1a4fd6] text-white text-[11px] font-semibold cursor-pointer"
              >
                <Download className="w-3 h-3" /> 下载
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1 text-[#71717a] hover:text-white cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
        <div className="p-4 flex-1 scrollbar-custom min-h-0 flex flex-col">
          <FilePreview filename={filename} mime={mime} text={text} imageUrl={imageUrl} />
        </div>
        {/* 右下角拖拽手柄：手动调整弹窗宽高 */}
        <div
          onMouseDown={startResize}
          className="absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize flex items-end justify-end"
          title="拖拽调整大小"
        >
          <svg className="w-3 h-3 text-[#52525b] hover:text-[#a1a1aa] transition-colors" viewBox="0 0 12 12" fill="none">
            <path d="M11 4L4 11M11 8L8 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </div>
      </div>
    </div>
  )
}

export default FilePreviewModal

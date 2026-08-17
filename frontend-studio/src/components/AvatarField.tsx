/**
 * AvatarField — agent 头像上传字段（AgentEditorPage）。
 *
 * - 预览（AvatarRender）+ 「上传图片」按钮（隐藏 input file）+ 「移除」按钮
 * - 选图 → react-easy-crop 裁剪（1:1）→ canvas 切 256×256 PNG → uploadAvatar → onChange(url)
 * - 移除 → onChange('')（空串 → PUT 提交 → 后端清空 → 渲染回 logo）
 * - agentId 缺失时禁用上传（先保存 Agent 拿到 id 再传）
 */
import { useState, useCallback, useRef, type FC } from 'react'
import Cropper from 'react-easy-crop'
import { Upload, Trash2, Loader2, X } from 'lucide-react'
import { agentApi } from '../services/agent-api'
import { toast } from './ui/toast'
import AvatarRender from './AvatarRender'

const AVATAR_OUTPUT_SIZE = 256
const MAX_BYTES = 2 * 1024 * 1024

interface PixelCrop {
  x: number
  y: number
  width: number
  height: number
}

interface AvatarFieldProps {
  value: string
  /** 实体 ID（agent / tool 等）。未保存（空）时禁用上传。 */
  entityId: string
  onChange: (avatar: string) => void
  disabled?: boolean
  /** 上传回调，默认 agentApi.uploadAvatar；其他实体（如 Skill）传自己的。 */
  upload?: (id: string, file: Blob) => Promise<string>
}

/** 读图片为 dataURL（喂给 Cropper）。 */
function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

/** canvas 裁剪：把图片按 pixelCrop 区域缩放到 size×size，输出 PNG Blob。 */
async function cropImageToBlob(imageSrc: string, pixelCrop: PixelCrop, size: number): Promise<Blob> {
  const image = await new Promise<HTMLImageElement>((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = reject
    img.src = imageSrc
  })
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  ctx.drawImage(image, pixelCrop.x, pixelCrop.y, pixelCrop.width, pixelCrop.height, 0, 0, size, size)
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob 失败'))), 'image/png')
  })
}

const AvatarField: FC<AvatarFieldProps> = ({ value, entityId, onChange, disabled, upload }) => {
  const doUpload = upload ?? ((id, file) => agentApi.uploadAvatar(id, file))
  const [imageSrc, setImageSrc] = useState<string | null>(null)
  const [crop, setCrop] = useState({ x: 0, y: 0 })
  const [zoom, setZoom] = useState(1)
  const [croppedAreaPixels, setCroppedAreaPixels] = useState<PixelCrop | null>(null)
  const [uploading, setUploading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const onFile = useCallback(async (file: File) => {
    if (!file.type.startsWith('image/')) {
      toast.error('请选择图片文件')
      return
    }
    if (file.size > MAX_BYTES) {
      toast.error('图片不超过 2MB')
      return
    }
    setImageSrc(await readFileAsDataURL(file))
  }, [])

  const onCropComplete = useCallback((_: unknown, pixels: PixelCrop) => {
    setCroppedAreaPixels(pixels)
  }, [])

  const confirmCrop = useCallback(async () => {
    if (!imageSrc || !croppedAreaPixels) return
    try {
      setUploading(true)
      const blob = await cropImageToBlob(imageSrc, croppedAreaPixels, AVATAR_OUTPUT_SIZE)
      const url = await doUpload(entityId, blob)
      onChange(`${url}?t=${Date.now()}`) // 破缓存：同名文件重传后强制刷新
      setImageSrc(null)
      toast.success('头像已更新')
    } catch (e) {
      toast.error(`上传失败：${(e as Error).message}`)
    } finally {
      setUploading(false)
    }
  }, [imageSrc, croppedAreaPixels, entityId, onChange, doUpload])

  const canUpload = !!entityId && !disabled

  return (
    <div className="flex items-center gap-3">
      <div className="w-16 h-16 rounded-xl bg-[#121214] border border-[#27272a] overflow-hidden flex items-center justify-center shrink-0">
        <AvatarRender value={value} className="w-full h-full" />
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!canUpload || uploading}
          onClick={() => inputRef.current?.click()}
          className="px-2.5 py-1.5 rounded-md border border-[#27272a] bg-[#121214] text-xs text-[#a1a1aa] hover:text-white hover:border-[#3f3f46] transition cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
          title={canUpload ? '上传图片' : '请先保存后再上传头像'}
        >
          {uploading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Upload className="w-3 h-3" />}
          上传图片
        </button>
        {value && (
          <button
            type="button"
            onClick={() => onChange('')}
            className="px-2.5 py-1.5 rounded-md border border-[#27272a] bg-[#121214] text-xs text-[#a1a1aa] hover:text-rose-400 hover:border-rose-500/30 transition cursor-pointer flex items-center gap-1.5"
          >
            <Trash2 className="w-3 h-3" />
            移除
          </button>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
          e.target.value = ''
        }}
      />

      {/* 裁剪 modal */}
      {imageSrc && (
        <div className="fixed inset-0 flex items-center justify-center z-50 p-4" role="dialog">
          <div className="w-full max-w-sm bg-[#18181b] border border-[#27272a] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-white">裁剪头像</span>
              <button onClick={() => setImageSrc(null)} className="text-[#71717a] hover:text-white cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="relative w-full h-64 bg-[#0c0c0e] rounded-lg overflow-hidden">
              <Cropper
                image={imageSrc}
                crop={crop}
                zoom={zoom}
                aspect={1}
                onCropChange={setCrop}
                onZoomChange={setZoom}
                onCropComplete={onCropComplete}
              />
            </div>
            <input type="range" min={1} max={3} step={0.1} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} className="w-full" />
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setImageSrc(null)} className="px-3 py-1.5 text-xs text-[#a1a1aa] hover:text-white cursor-pointer">
                取消
              </button>
              <button
                onClick={confirmCrop}
                disabled={uploading}
                className="px-3 py-1.5 text-xs rounded-md bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-50 cursor-pointer flex items-center gap-1.5"
              >
                {uploading ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                确认上传
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default AvatarField

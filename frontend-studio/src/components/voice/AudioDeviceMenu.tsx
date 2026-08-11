import { Loader2, Mic, Settings2, Volume2 } from 'lucide-react'
import type { AudioDeviceOption } from '../../hooks/voice/useAudioDevices'

interface AudioDeviceMenuProps {
  theme: 'dark' | 'light'
  inputDevices: AudioDeviceOption[]
  outputDevices: AudioDeviceOption[]
  inputDeviceId: string
  outputDeviceId: string
  onInputChange: (deviceId: string) => void
  onOutputChange: (deviceId: string) => void
  loading: boolean
  recording: boolean
  outputSelectionSupported: boolean
  notice?: string | null
  error?: string | null
}

export function AudioDeviceMenu(props: AudioDeviceMenuProps) {
  const dark = props.theme === 'dark'
  const inputKnown = props.inputDevices.some((device) => device.deviceId === props.inputDeviceId)
  const outputKnown = props.outputDevices.some((device) => device.deviceId === props.outputDeviceId)
  const selectClass = `h-11 w-full rounded-lg border px-3 text-xs outline-none transition-colors focus:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/30 disabled:cursor-not-allowed disabled:opacity-50 ${
    dark
      ? 'border-[#3f3f46] bg-[#111113] text-[#f4f4f5]'
      : 'border-slate-300 bg-white text-slate-800'
  }`

  return (
    <details className="group relative">
      <summary
        className={`flex min-h-11 cursor-pointer list-none items-center gap-1.5 rounded-lg px-3 text-[11px] font-semibold transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 [&::-webkit-details-marker]:hidden ${
          dark
            ? 'text-[#d4d4d8] hover:bg-white/8'
            : 'text-slate-700 hover:bg-slate-100'
        }`}
        aria-label="选择音频输入和输出设备"
      >
        <Settings2 className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">音频设备</span>
      </summary>

      <div
        className={`absolute right-0 top-[calc(100%+8px)] z-40 w-[min(320px,calc(100vw-48px))] rounded-xl border p-3 shadow-2xl ${
          dark
            ? 'border-[#3f3f46] bg-[#18181b] text-[#f4f4f5] shadow-black/50'
            : 'border-slate-200 bg-white text-slate-800 shadow-slate-900/15'
        }`}
      >
        <div className="mb-3 flex items-center justify-between">
          <div>
            <div className="text-xs font-semibold">浏览器音频设备</div>
            <p className={`mt-0.5 text-[11px] ${dark ? 'text-[#a1a1aa]' : 'text-slate-500'}`}>
              默认跟随操作系统，选择结果仅保存在当前浏览器。
            </p>
          </div>
          {props.loading && <Loader2 className="h-4 w-4 animate-spin text-indigo-500" aria-label="正在读取设备" />}
        </div>

        <div className="space-y-3">
          <label className="block space-y-1.5">
            <span className="flex items-center gap-1.5 text-[11px] font-medium">
              <Mic className="h-3.5 w-3.5" /> 输入设备
            </span>
            <select
              value={props.inputDeviceId}
              onChange={(event) => props.onInputChange(event.target.value)}
              disabled={props.loading || props.recording}
              className={selectClass}
            >
              <option value="">跟随系统（默认麦克风）</option>
              {props.inputDeviceId && !inputKnown && (
                <option value={props.inputDeviceId}>已保存的麦克风（等待授权识别）</option>
              )}
              {props.inputDevices.map((device) => (
                <option key={device.deviceId} value={device.deviceId}>{device.label}</option>
              ))}
            </select>
            {props.recording && (
              <span className={`block text-[10px] ${dark ? 'text-amber-300' : 'text-amber-700'}`}>
                暂停语音后可以切换麦克风。
              </span>
            )}
          </label>

          <label className="block space-y-1.5">
            <span className="flex items-center gap-1.5 text-[11px] font-medium">
              <Volume2 className="h-3.5 w-3.5" /> 输出设备
            </span>
            <select
              value={props.outputDeviceId}
              onChange={(event) => props.onOutputChange(event.target.value)}
              disabled={props.loading || !props.outputSelectionSupported}
              className={selectClass}
            >
              <option value="">跟随系统（默认扬声器）</option>
              {props.outputDeviceId && !outputKnown && (
                <option value={props.outputDeviceId}>已保存的扬声器（等待授权识别）</option>
              )}
              {props.outputDevices.map((device) => (
                <option key={device.deviceId} value={device.deviceId}>{device.label}</option>
              ))}
            </select>
            {!props.outputSelectionSupported && (
              <span className={`block text-[10px] ${dark ? 'text-[#a1a1aa]' : 'text-slate-500'}`}>
                当前浏览器仅支持跟随系统输出。
              </span>
            )}
          </label>
        </div>

        {(props.notice || props.error) && (
          <p
            className={`mt-3 rounded-lg px-2.5 py-2 text-[10px] ${
              props.error
                ? dark ? 'bg-rose-500/10 text-rose-300' : 'bg-rose-50 text-rose-700'
                : dark ? 'bg-amber-500/10 text-amber-200' : 'bg-amber-50 text-amber-800'
            }`}
            role={props.error ? 'alert' : 'status'}
          >
            {props.error || props.notice}
          </p>
        )}
      </div>
    </details>
  )
}

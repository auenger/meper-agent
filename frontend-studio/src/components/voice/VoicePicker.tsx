import { useEffect, useMemo, useState } from 'react'
import {
  Check,
  ChevronDown,
  Loader2,
  Search,
  SlidersHorizontal,
  Square,
  Volume2,
} from 'lucide-react'
import { VOICE_OPTIONS, type VoiceCategory } from './voice-options'

type PreviewPhase = 'loading' | 'playing'

interface VoicePickerProps {
  value: string
  onChange: (value: string) => void
  theme: 'dark' | 'light'
  preview: { voiceType: string; phase: PreviewPhase } | null
  previewDisabled?: boolean
  onPreview: (voiceType: string) => void
}

const categories: Array<{ value: 'all' | VoiceCategory; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'general', label: '通用' },
  { value: 'dubbing', label: '视频配音' },
  { value: 'roleplay', label: '角色扮演' },
]

export function VoicePicker({
  value,
  onChange,
  theme,
  preview,
  previewDisabled,
  onPreview,
}: VoicePickerProps) {
  const dark = theme === 'dark'
  const selectedPreset = VOICE_OPTIONS.find((voice) => voice.id === value)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<'all' | VoiceCategory>('all')
  const [customValue, setCustomValue] = useState(selectedPreset ? '' : value)

  useEffect(() => {
    if (!VOICE_OPTIONS.some((voice) => voice.id === value)) setCustomValue(value)
  }, [value])

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase()
    return VOICE_OPTIONS.filter((voice) => {
      if (category !== 'all' && voice.category !== category) return false
      if (!term) return true
      return [voice.name, voice.id, voice.gender, voice.categoryLabel, voice.description]
        .some((item) => item.toLowerCase().includes(term))
    })
  }, [category, search])

  const muted = dark ? 'text-[#a1a1aa]' : 'text-slate-500'
  const subtle = dark ? 'text-[#71717a]' : 'text-slate-400'
  const border = dark ? 'border-[#3f3f46]' : 'border-slate-200'
  const input = dark
    ? 'bg-[#09090b] border-[#3f3f46] text-[#fafafa] placeholder:text-[#52525b]'
    : 'bg-white border-slate-300 text-slate-800 placeholder:text-slate-400'

  const previewIcon = (voiceType: string) => {
    const active = preview?.voiceType === voiceType
    if (active && preview.phase === 'loading') return <Loader2 className="h-4 w-4 animate-spin" />
    if (active && preview.phase === 'playing') return <Square className="h-3.5 w-3.5 fill-current" />
    return <Volume2 className="h-4 w-4" />
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-medium">选择音色</div>
          <p className={`mt-0.5 text-xs ${muted}`}>
            {selectedPreset
              ? `当前：${selectedPreset.name} · ${selectedPreset.description}`
              : `当前：自定义音色 · ${value || '尚未填写'}`}
          </p>
        </div>
        <span className={`shrink-0 rounded-full border px-2 py-1 text-[11px] ${border} ${subtle}`}>
          Seed TTS 2.0
        </span>
      </div>

      <label className="relative block">
        <span className="sr-only">搜索音色</span>
        <Search className={`absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 ${subtle}`} />
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索名称、类型或 voice_type"
          className={`h-11 w-full rounded-lg border pl-9 pr-3 text-sm outline-none transition-colors focus:border-indigo-500 ${input}`}
        />
      </label>

      <div className="flex flex-wrap gap-2" aria-label="音色分类">
        {categories.map((item) => {
          const active = category === item.value
          return (
            <button
              key={item.value}
              type="button"
              onClick={() => setCategory(item.value)}
              aria-pressed={active}
              className={`min-h-9 rounded-full border px-3 text-xs font-medium transition-colors ${
                active
                  ? 'border-indigo-500 bg-indigo-500 text-white'
                  : dark
                    ? 'border-[#3f3f46] text-[#d4d4d8] hover:bg-[#27272a]'
                    : 'border-slate-200 text-slate-600 hover:bg-slate-100'
              }`}
            >
              {item.label}
            </button>
          )
        })}
      </div>

      {filtered.length > 0 ? (
        <div className="grid max-h-[360px] grid-cols-1 gap-2 overflow-y-auto pr-1 md:grid-cols-2">
          {filtered.map((voice) => {
            const selected = voice.id === value
            const activePreview = preview?.voiceType === voice.id
            return (
              <div
                key={voice.id}
                className={`flex min-h-[104px] items-stretch overflow-hidden rounded-lg border transition-colors ${
                  selected
                    ? dark
                      ? 'border-indigo-400 bg-indigo-500/10'
                      : 'border-indigo-500 bg-indigo-50'
                    : dark
                      ? 'border-[#27272a] bg-[#111113] hover:border-[#52525b]'
                      : 'border-slate-200 bg-white hover:border-slate-300'
                }`}
              >
                <button
                  type="button"
                  onClick={() => onChange(voice.id)}
                  className="min-w-0 flex-1 p-3 text-left"
                  aria-pressed={selected}
                >
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{voice.name}</span>
                    {selected && <Check className="h-4 w-4 shrink-0 text-indigo-500" />}
                  </span>
                  <span className={`mt-1 block text-xs ${muted}`}>{voice.description}</span>
                  <span className={`mt-2 flex flex-wrap gap-x-2 text-[11px] ${subtle}`}>
                    <span>{voice.gender}</span>
                    <span>{voice.categoryLabel}</span>
                    <span>{voice.languages}</span>
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => onPreview(voice.id)}
                  disabled={previewDisabled || (!!preview && !activePreview)}
                  aria-label={`${activePreview && preview.phase === 'playing' ? '停止' : '试听'}${voice.name}`}
                  title={previewDisabled ? '请先配置 API Key 并填写试听文本' : '试听音色'}
                  className={`flex w-11 shrink-0 items-center justify-center border-l transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                    dark
                      ? 'border-[#27272a] text-[#a1a1aa] hover:bg-[#27272a] hover:text-white'
                      : 'border-slate-200 text-slate-500 hover:bg-slate-100 hover:text-slate-800'
                  }`}
                >
                  {previewIcon(voice.id)}
                </button>
              </div>
            )
          })}
        </div>
      ) : (
        <div className={`rounded-lg border border-dashed px-4 py-8 text-center text-xs ${border} ${muted}`}>
          没有找到匹配的音色，可以在下方使用自定义 voice_type。
        </div>
      )}

      <details className={`group rounded-lg border ${border}`} open={!selectedPreset}>
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 text-xs font-medium [&::-webkit-details-marker]:hidden">
          <SlidersHorizontal className={`h-4 w-4 ${subtle}`} />
          使用自定义 voice_type
          <ChevronDown className={`ml-auto h-4 w-4 transition-transform group-open:rotate-180 ${subtle}`} />
        </summary>
        <div className={`space-y-2 border-t p-3 ${border}`}>
          <p className={`text-xs ${muted}`}>
            用于接入不在内置列表中的官方或复刻音色。请确保该 ID 可用于 Seed TTS 2.0。
          </p>
          <div className="flex gap-2">
            <label className="min-w-0 flex-1">
              <span className="sr-only">自定义 voice_type</span>
              <input
                value={customValue}
                onChange={(event) => {
                  setCustomValue(event.target.value)
                  onChange(event.target.value)
                }}
                placeholder="输入自定义 voice_type"
                className={`h-11 w-full rounded-lg border px-3 font-mono text-xs outline-none transition-colors focus:border-indigo-500 ${input}`}
              />
            </label>
            <button
              type="button"
              onClick={() => onPreview(customValue.trim())}
              disabled={previewDisabled || !customValue.trim() || (!!preview && preview.voiceType !== customValue.trim())}
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                dark
                  ? 'border-[#3f3f46] text-[#d4d4d8] hover:bg-[#27272a]'
                  : 'border-slate-300 text-slate-600 hover:bg-slate-100'
              }`}
              aria-label={preview?.voiceType === customValue.trim() && preview.phase === 'playing' ? '停止试听' : '试听自定义音色'}
              title={previewDisabled ? '请先配置 API Key 并填写试听文本' : '试听自定义音色'}
            >
              {previewIcon(customValue.trim())}
            </button>
          </div>
        </div>
      </details>
    </div>
  )
}

/**
 * VoiceConfigPage — admin page for the火山 ASR/TTS config (DB singleton).
 *
 * Loads the singleton config, edits fields inline; the Agent Plan API Key shows
 * the masked value as placeholder; blank means "don't change". Save +
 * connectivity-test buttons. Matches ModelsPage's form + tanstack-query style.
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  voiceConfigApi,
  voiceConfigKeys,
} from '../../services/voice-config-api'
import { VoicePicker } from './VoicePicker'
import { getErrorMessage } from '../../lib/api-client'

export function VoiceConfigPage({ theme }: { theme: 'dark' | 'light' }) {
  const qc = useQueryClient()
  const { data: cfg, isLoading } = useQuery({
    queryKey: voiceConfigKeys.detail,
    queryFn: voiceConfigApi.get,
  })

  const [apiKey, setApiKey] = useState('')
  const [ttsVoice, setTtsVoice] = useState('zh_female_vv_uranus_bigtts')
  const [inputRate, setInputRate] = useState(16000)
  const [outputRate, setOutputRate] = useState(24000)
  const [vadMode, setVadMode] = useState('energy')
  const [vadThreshold, setVadThreshold] = useState(0.12)
  const [vadSilence, setVadSilence] = useState(600)
  const [status, setStatus] = useState<{ type: 'success' | 'error' | 'info'; msg: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [previewText, setPreviewText] = useState('你好，我是你的智能语音助手，很高兴和你对话。')
  const [preview, setPreview] = useState<{ voiceType: string; phase: 'loading' | 'playing' } | null>(null)
  const [previewError, setPreviewError] = useState('')
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const audioUrlRef = useRef('')
  const previewRequestRef = useRef(0)

  useEffect(() => {
    if (!cfg) return
    setTtsVoice(cfg.tts.voice_type)
    setInputRate(cfg.audio.input_rate); setOutputRate(cfg.audio.output_rate)
    setVadMode(cfg.vad.mode); setVadThreshold(cfg.vad.threshold); setVadSilence(cfg.vad.silence_ms)
  }, [cfg])

  const stopPreview = () => {
    previewRequestRef.current += 1
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current)
      audioUrlRef.current = ''
    }
    setPreview(null)
  }

  useEffect(() => () => {
    audioRef.current?.pause()
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current)
  }, [])

  const playPreview = async (voiceType: string) => {
    if (!voiceType.trim() || !previewText.trim()) return
    if (preview?.voiceType === voiceType) {
      if (preview.phase === 'playing') stopPreview()
      return
    }

    stopPreview()
    const requestId = previewRequestRef.current
    setPreviewError('')
    setPreview({ voiceType, phase: 'loading' })
    try {
      const audioBlob = await voiceConfigApi.preview(voiceType, previewText.trim())
      if (requestId !== previewRequestRef.current) return
      const objectUrl = URL.createObjectURL(audioBlob)
      const audio = new Audio(objectUrl)
      audioRef.current = audio
      audioUrlRef.current = objectUrl
      audio.onended = stopPreview
      audio.onerror = () => {
        stopPreview()
        setPreviewError('试听音频播放失败')
      }
      await audio.play()
      if (requestId !== previewRequestRef.current) return
      setPreview({ voiceType, phase: 'playing' })
    } catch (error) {
      if (requestId !== previewRequestRef.current) return
      stopPreview()
      setPreviewError(getErrorMessage(error, '操作失败'))
    }
  }

  const save = async () => {
    setSaving(true); setStatus(null)
    try {
      await voiceConfigApi.save({
        api_key: apiKey || null,
        asr: {},
        tts: { voice_type: ttsVoice },
        audio: { input_rate: inputRate, output_rate: outputRate },
        vad: { mode: vadMode, threshold: vadThreshold, silence_ms: vadSilence },
      })
      setApiKey('')
      qc.invalidateQueries({ queryKey: voiceConfigKeys.detail })
      qc.invalidateQueries({ queryKey: voiceConfigKeys.status })
      setStatus({ type: 'success', msg: '已保存' })
    } catch (e) {
      setStatus({ type: 'error', msg: getErrorMessage(e, '操作失败') })
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setStatus({ type: 'info', msg: '测试中…' })
    try {
      const r = await voiceConfigApi.test()
      setStatus({ type: r.success ? 'success' : 'error', msg: r.message })
    } catch (e) {
      setStatus({ type: 'error', msg: getErrorMessage(e, '操作失败') })
    }
  }

  const dark = theme === 'dark'
  const card = dark ? 'bg-[#18181b] border-[#27272a]' : 'bg-white border-slate-200'
  const inputCls = `px-2 py-1.5 rounded border text-sm outline-none focus:border-indigo-500 ${
    dark ? 'bg-[#09090b] border-[#27272a] text-[#fafafa]' : 'bg-slate-50 border-slate-200'
  }`

  const Field = ({ label, value, onChange, placeholder, type = 'text' }: {
    label: string; value: string | number; onChange: (v: string) => void; placeholder?: string; type?: string
  }) => (
    <label className="flex flex-col gap-1 text-xs">
      <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>{label}</span>
      <input type={type} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} className={inputCls} />
    </label>
  )

  const FixedValue = ({ label, value }: { label: string; value: string }) => (
    <div className="flex flex-col gap-1 text-xs">
      <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>{label}</span>
      <code className={`px-2 py-1.5 rounded border break-all ${
        dark ? 'bg-[#09090b] border-[#27272a] text-[#d4d4d8]' : 'bg-slate-50 border-slate-200 text-slate-600'
      }`}>{value}</code>
    </div>
  )

  if (isLoading) return <div className="p-6 text-sm opacity-60">加载语音配置…</div>

  return (
    <div className={`max-w-3xl mx-auto p-6 space-y-4 ${dark ? 'text-[#fafafa]' : 'text-slate-800'}`}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">语音设置</h2>
          <p className={`text-xs mt-1 ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>
            方舟 Agent Plan 豆包语音 2.0。使用专属 API Key，凭证加密存储。
          </p>
        </div>
        {cfg?.last_test_at && (
          <span className={`text-xs ${cfg.last_test_success ? 'text-emerald-500' : 'text-rose-500'}`}>
            上次测试：{cfg.last_test_success ? '通过' : '失败'}
          </span>
        )}
      </div>

      <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
        <div>
          <h3 className="text-sm font-medium">Agent Plan 凭证</h3>
          <p className={`text-xs mt-1 ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>
            无需 App ID 或 Access Token；ASR 与 TTS 共用一个专属 API Key。
          </p>
        </div>
        <Field
          label="专属 API Key"
          value={apiKey}
          onChange={setApiKey}
          placeholder={cfg?.api_key_masked ? `已配置：${cfg.api_key_masked}（留空不改）` : 'Agent Plan 专属 API Key'}
          type="password"
        />
        <a
          href="https://console.volcengine.com/ark/region:cn-beijing/openManagement?LLM=%7B%7D&OpenModelVisible=false&advancedActiveKey=agentPlan"
          target="_blank"
          rel="noreferrer"
          className="inline-block text-xs text-indigo-500 hover:text-indigo-400"
        >
          前往火山方舟获取专属 API Key
        </a>
      </section>

      {/* ASR */}
      <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
        <h3 className="text-sm font-medium">语音识别 ASR</h3>
        <FixedValue label="Resource-Id（固定）" value={cfg?.asr.resource_id ?? 'volc.seedasr.sauc.duration'} />
        <FixedValue label="WebSocket URL（双流）" value={cfg?.asr.url ?? 'wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async'} />
      </section>

      {/* TTS */}
      <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
        <h3 className="text-sm font-medium">语音合成 TTS</h3>
        <FixedValue label="Resource-Id（固定）" value={cfg?.tts.resource_id ?? 'seed-tts-2.0'} />
        <FixedValue label="WebSocket URL（双向流式）" value={cfg?.tts.url ?? 'wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection'} />
        <label className="flex flex-col gap-1 text-xs">
          <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>试听文本</span>
          <div className="relative">
            <input
              value={previewText}
              onChange={(event) => setPreviewText(event.target.value.slice(0, 120))}
              placeholder="输入一小段用于试听的文字"
              className={`${inputCls} h-10 w-full pr-14`}
            />
            <span className={`absolute right-2 top-1/2 -translate-y-1/2 text-[10px] ${dark ? 'text-[#52525b]' : 'text-slate-400'}`}>
              {previewText.length}/120
            </span>
          </div>
        </label>
        <VoicePicker
          value={ttsVoice}
          onChange={(voiceType) => {
            stopPreview()
            setPreviewError('')
            setTtsVoice(voiceType)
          }}
          theme={theme}
          preview={preview}
          previewDisabled={!cfg?.api_key_masked || !previewText.trim()}
          onPreview={playPreview}
        />
        {!cfg?.api_key_masked && (
          <p className={`text-xs ${dark ? 'text-amber-400' : 'text-amber-700'}`}>
            保存 Agent Plan 专属 API Key 后即可试听音色。
          </p>
        )}
        {previewError && <p className="text-xs text-rose-500">{previewError}</p>}
      </section>

      {/* Audio + VAD */}
      <section className={`rounded-lg border p-4 grid grid-cols-2 md:grid-cols-3 gap-3 ${card}`}>
        <Field label="上行采样率" value={inputRate} onChange={(v) => setInputRate(Number(v) || 16000)} type="number" />
        <Field label="下行采样率" value={outputRate} onChange={(v) => setOutputRate(Number(v) || 24000)} type="number" />
        <label className="flex flex-col gap-1 text-xs">
          <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>VAD 模式</span>
          <select value={vadMode} onChange={(e) => setVadMode(e.target.value)} className={inputCls}>
            <option value="energy">energy（无依赖）</option>
            <option value="silero">silero（更准，需装库）</option>
            <option value="off">off（仅 ASR 端点）</option>
          </select>
        </label>
        <Field label="VAD 阈值" value={vadThreshold} onChange={(v) => setVadThreshold(Number(v) || 0.12)} type="number" />
        <Field label="静音时长(ms)" value={vadSilence} onChange={(v) => setVadSilence(Number(v) || 600)} type="number" />
      </section>

      {/* Actions */}
      <div className="flex items-center gap-3">
        <button onClick={save} disabled={saving} className="px-4 py-2 rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white text-sm font-medium cursor-pointer disabled:opacity-50">
          {saving ? '保存中…' : '保存配置'}
        </button>
        <button onClick={test} className="px-4 py-2 rounded-lg border border-slate-400/40 text-sm font-medium cursor-pointer hover:bg-slate-500/10">
          测试连通
        </button>
        {status && (
          <span className={`text-xs ${status.type === 'success' ? 'text-emerald-500' : status.type === 'error' ? 'text-rose-500' : 'text-amber-500'}`}>
            {status.msg}
          </span>
        )}
      </div>
      <p className={`text-xs ${dark ? 'text-[#52525b]' : 'text-slate-400'}`}>
        Agent Plan 语音模型不参与 Auto 调度，也不能在控制台切换模型；此页固定使用文档指定的 Resource-Id。
      </p>
    </div>
  )
}

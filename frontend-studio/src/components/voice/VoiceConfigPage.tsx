/**
 * VoiceConfigPage — admin page for voice ASR/TTS config (DB singleton).
 *
 * Providers keep independent credentials and settings. Switching the active
 * provider only changes which saved provider is used by the unified voice path.
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  voiceConfigApi,
  voiceConfigKeys,
  type VoiceProvider,
} from '../../services/voice-config-api'
import { VoicePicker } from './VoicePicker'
import { getErrorMessage } from '../../lib/api-client'

const ZHIPU_VOICES = [
  { id: 'tongtong', name: '彤彤' },
  { id: 'chuichui', name: '锤锤' },
  { id: 'xiaochen', name: '小陈' },
  { id: 'jam', name: 'jam' },
  { id: 'kazi', name: 'kazi' },
  { id: 'douji', name: 'douji' },
  { id: 'luodo', name: 'luodo' },
]

const ALIYUN_VOICES = [
  { id: 'Cherry', name: 'Cherry' },
  { id: 'Serena', name: 'Serena' },
  { id: 'Ethan', name: 'Ethan' },
  { id: 'Chelsie', name: 'Chelsie' },
  { id: 'Dylan', name: 'Dylan' },
]

const PROVIDERS: Array<{ id: VoiceProvider; label: string; detail: string }> = [
  { id: 'volcano', label: '火山 Agent Plan', detail: '保持现有 ASR/TTS 接入方式' },
  { id: 'zhipu', label: '智谱 GLM', detail: 'GLM-ASR-2512 + GLM-TTS' },
  { id: 'aliyun', label: '阿里百炼', detail: 'Qwen ASR + Qwen TTS' },
]

export function VoiceConfigPage({ theme }: { theme: 'dark' | 'light' }) {
  const qc = useQueryClient()
  const { data: cfg, isLoading } = useQuery({
    queryKey: voiceConfigKeys.detail,
    queryFn: voiceConfigApi.get,
  })

  const [activeProvider, setActiveProvider] = useState<VoiceProvider>('volcano')
  const [ttsEnabled, setTtsEnabled] = useState(true)
  const [apiKey, setApiKey] = useState('')
  const [ttsVoice, setTtsVoice] = useState('zh_female_vv_uranus_bigtts')

  const [zhipuApiKey, setZhipuApiKey] = useState('')
  const [zhipuAsrModel, setZhipuAsrModel] = useState('glm-asr-2512')
  const [zhipuAsrUrl, setZhipuAsrUrl] = useState('https://open.bigmodel.cn/api/paas/v4/audio/transcriptions')
  const [zhipuTtsModel, setZhipuTtsModel] = useState('glm-tts')
  const [zhipuTtsUrl, setZhipuTtsUrl] = useState('https://open.bigmodel.cn/api/paas/v4/audio/speech')
  const [zhipuVoice, setZhipuVoice] = useState('tongtong')
  const [zhipuSpeed, setZhipuSpeed] = useState(1)
  const [zhipuVolume, setZhipuVolume] = useState(1)

  const [aliyunApiKey, setAliyunApiKey] = useState('')
  const [aliyunAsrModel, setAliyunAsrModel] = useState('qwen3-asr-flash')
  const [aliyunAsrUrl, setAliyunAsrUrl] = useState('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
  const [aliyunTtsModel, setAliyunTtsModel] = useState('qwen3-tts-flash')
  const [aliyunTtsUrl, setAliyunTtsUrl] = useState('https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation')
  const [aliyunVoice, setAliyunVoice] = useState('Cherry')
  const [aliyunLanguage, setAliyunLanguage] = useState('Chinese')

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
    setActiveProvider(cfg.active_provider ?? 'volcano')
    setTtsEnabled(cfg.tts_enabled ?? true)
    setTtsVoice(cfg.tts.voice_type)
    setZhipuAsrModel(cfg.zhipu.asr_model)
    setZhipuAsrUrl(cfg.zhipu.asr_url)
    setZhipuTtsModel(cfg.zhipu.tts_model)
    setZhipuTtsUrl(cfg.zhipu.tts_url)
    setZhipuVoice(cfg.zhipu.voice_type)
    setZhipuSpeed(cfg.zhipu.speed)
    setZhipuVolume(cfg.zhipu.volume)
    setAliyunAsrModel(cfg.aliyun.asr_model)
    setAliyunAsrUrl(cfg.aliyun.asr_url)
    setAliyunTtsModel(cfg.aliyun.tts_model)
    setAliyunTtsUrl(cfg.aliyun.tts_url)
    setAliyunVoice(cfg.aliyun.voice_type)
    setAliyunLanguage(cfg.aliyun.language_type)
    setInputRate(cfg.audio.input_rate)
    setOutputRate(cfg.audio.output_rate)
    setVadMode(cfg.vad.mode)
    setVadThreshold(cfg.vad.threshold)
    setVadSilence(cfg.vad.silence_ms)
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
    setSaving(true)
    setStatus(null)
    try {
      const saved = await voiceConfigApi.save({
        active_provider: activeProvider,
        tts_enabled: ttsEnabled,
        api_key: apiKey || null,
        asr: {},
        tts: { voice_type: ttsVoice },
        zhipu: {
          api_key: zhipuApiKey || null,
          asr_model: zhipuAsrModel,
          asr_url: zhipuAsrUrl,
          tts_model: zhipuTtsModel,
          tts_url: zhipuTtsUrl,
          voice_type: zhipuVoice,
          speed: zhipuSpeed,
          volume: zhipuVolume,
        },
        aliyun: {
          api_key: aliyunApiKey || null,
          asr_model: aliyunAsrModel,
          asr_url: aliyunAsrUrl,
          tts_model: aliyunTtsModel,
          tts_url: aliyunTtsUrl,
          voice_type: aliyunVoice,
          language_type: aliyunLanguage,
        },
        audio: { input_rate: inputRate, output_rate: outputRate },
        vad: { mode: vadMode, threshold: vadThreshold, silence_ms: vadSilence },
      })
      setApiKey('')
      setZhipuApiKey('')
      setAliyunApiKey('')
      qc.setQueryData(voiceConfigKeys.detail, saved)
      qc.invalidateQueries({ queryKey: voiceConfigKeys.detail })
      qc.invalidateQueries({ queryKey: voiceConfigKeys.status })
      setStatus({ type: 'success', msg: '已保存；新建或重新连接语音会话后生效。' })
    } catch (e) {
      setStatus({ type: 'error', msg: getErrorMessage(e, '操作失败') })
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    if (cfg?.active_provider !== activeProvider) {
      setStatus({ type: 'info', msg: '切换供应商后请先保存配置，再测试连通。' })
      return
    }
    if (cfg?.tts_enabled !== ttsEnabled) {
      setStatus({ type: 'info', msg: '修改语音播报开关后请先保存，再测试连通。' })
      return
    }
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

  const onProviderChange = (provider: VoiceProvider) => {
    stopPreview()
    setPreviewError('')
    setActiveProvider(provider)
  }

  const previewVoice = activeProvider === 'zhipu' ? zhipuVoice : activeProvider === 'aliyun' ? aliyunVoice : ttsVoice
  const providerSaved = cfg?.active_provider === activeProvider
  const keyConfigured = activeProvider === 'zhipu'
    ? !!cfg?.zhipu.api_key_masked
    : activeProvider === 'aliyun'
      ? !!cfg?.aliyun.api_key_masked
      : !!cfg?.api_key_masked
  const previewDisabled = !providerSaved || !keyConfigured || !previewText.trim()

  if (isLoading) return <div className="p-6 text-sm opacity-60">加载语音配置…</div>

  return (
    <div className={`max-w-3xl mx-auto p-6 space-y-4 ${dark ? 'text-[#fafafa]' : 'text-slate-800'}`}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">语音设置</h2>
          <p className={`text-xs mt-1 ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>
            选择语音供应商后配置对应 API Key。凭证加密存储，切换后保存生效。
          </p>
        </div>
        {cfg?.last_test_at && (
          <span className={`text-xs ${cfg.last_test_success ? 'text-emerald-500' : 'text-rose-500'}`}>
            上次测试：{cfg.last_test_success ? '通过' : '失败'}
          </span>
        )}
      </div>

      <section className={`rounded-lg border p-4 ${card}`}>
        <button
          type="button"
          role="switch"
          aria-checked={ttsEnabled}
          onClick={() => setTtsEnabled((enabled) => !enabled)}
          className="flex min-h-11 w-full items-center justify-between gap-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 cursor-pointer"
        >
          <span className="min-w-0">
            <span className="block text-sm font-medium">语音播报</span>
            <span className={`mt-1 block text-xs leading-relaxed ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>
              关闭后语音对话仅做语音识别，智能体回复以文字显示，不再合成播报。
            </span>
          </span>
          <span
            className={`relative h-6 w-11 shrink-0 rounded-full border transition-colors duration-200 ${
              ttsEnabled
                ? 'border-indigo-400 bg-indigo-500'
                : dark
                  ? 'border-[#52525b] bg-[#27272a]'
                  : 'border-slate-300 bg-slate-200'
            }`}
            aria-hidden="true"
          >
            <span
              className={`absolute top-0.5 h-4.5 w-4.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                ttsEnabled ? 'translate-x-5' : 'translate-x-0.5'
              }`}
            />
          </span>
        </button>
      </section>

      <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
        <h3 className="text-sm font-medium">语音供应商</h3>
        <label className="flex flex-col gap-1 text-xs">
          <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>当前供应商</span>
          <select value={activeProvider} onChange={(e) => onProviderChange(e.target.value as VoiceProvider)} className={`${inputCls} h-10`}>
            {PROVIDERS.map((provider) => (
              <option key={provider.id} value={provider.id}>{provider.label} - {provider.detail}</option>
            ))}
          </select>
        </label>
      </section>

      {activeProvider === 'volcano' && (
        <>
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
            <a href="https://console.volcengine.com/ark/region:cn-beijing/openManagement?LLM=%7B%7D&OpenModelVisible=false&advancedActiveKey=agentPlan" target="_blank" rel="noreferrer" className="inline-block text-xs text-indigo-500 hover:text-indigo-400">
              前往火山方舟获取专属 API Key
            </a>
          </section>

          <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
            <h3 className="text-sm font-medium">语音识别 ASR</h3>
            <FixedValue label="Resource-Id（固定）" value={cfg?.asr.resource_id ?? 'volc.seedasr.sauc.duration'} />
            <FixedValue label="WebSocket URL（双流）" value={cfg?.asr.url ?? 'wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async'} />
          </section>

          <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
            <h3 className="text-sm font-medium">语音合成 TTS</h3>
            <FixedValue label="Resource-Id（固定）" value={cfg?.tts.resource_id ?? 'seed-tts-2.0'} />
            <FixedValue label="WebSocket URL（双向流式）" value={cfg?.tts.url ?? 'wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection'} />
          </section>
        </>
      )}

      {activeProvider === 'zhipu' && (
        <>
          <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
            <div>
              <h3 className="text-sm font-medium">智谱凭证</h3>
              <p className={`text-xs mt-1 ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>
                ASR 与 TTS 共用智谱开放平台 API Key。
              </p>
            </div>
            <Field label="智谱 API Key" value={zhipuApiKey} onChange={setZhipuApiKey} placeholder={cfg?.zhipu.api_key_masked ? `已配置：${cfg.zhipu.api_key_masked}（留空不改）` : '智谱 API Key'} type="password" />
            <a href="https://bigmodel.cn/console/modelcenter/square" target="_blank" rel="noreferrer" className="inline-block text-xs text-indigo-500 hover:text-indigo-400">
              前往智谱模型广场
            </a>
          </section>

          <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
            <h3 className="text-sm font-medium">语音识别 ASR</h3>
            <Field label="模型" value={zhipuAsrModel} onChange={setZhipuAsrModel} />
            <Field label="HTTP URL" value={zhipuAsrUrl} onChange={setZhipuAsrUrl} />
            <p className={`text-xs ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>智谱 ASR 第一版在松开说话后提交本段录音识别。</p>
          </section>

          <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
            <h3 className="text-sm font-medium">语音合成 TTS</h3>
            <Field label="模型" value={zhipuTtsModel} onChange={setZhipuTtsModel} />
            <Field label="HTTP URL" value={zhipuTtsUrl} onChange={setZhipuTtsUrl} />
            <label className="flex flex-col gap-1 text-xs">
              <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>系统音色</span>
              <select value={zhipuVoice} onChange={(e) => setZhipuVoice(e.target.value)} className={inputCls}>
                {ZHIPU_VOICES.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}（{voice.id}）</option>)}
              </select>
            </label>
            <Field label="自定义/当前 voice" value={zhipuVoice} onChange={setZhipuVoice} />
            <div className="grid grid-cols-2 gap-3">
              <Field label="语速" value={zhipuSpeed} onChange={(v) => setZhipuSpeed(Number(v) || 1)} type="number" />
              <Field label="音量" value={zhipuVolume} onChange={(v) => setZhipuVolume(Number(v) || 1)} type="number" />
            </div>
          </section>
        </>
      )}

      {activeProvider === 'aliyun' && (
        <>
          <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
            <div>
              <h3 className="text-sm font-medium">阿里百炼凭证</h3>
              <p className={`text-xs mt-1 ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>
                ASR 与 TTS 共用百炼 API Key。北京地域模型请使用北京地域 API Key。
              </p>
            </div>
            <Field label="百炼 API Key" value={aliyunApiKey} onChange={setAliyunApiKey} placeholder={cfg?.aliyun.api_key_masked ? `已配置：${cfg.aliyun.api_key_masked}（留空不改）` : '阿里百炼 API Key'} type="password" />
            <a href="https://bailian.console.aliyun.com/cn-beijing?tab=model#/model-market" target="_blank" rel="noreferrer" className="inline-block text-xs text-indigo-500 hover:text-indigo-400">
              前往阿里百炼模型广场
            </a>
          </section>

          <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
            <h3 className="text-sm font-medium">语音识别 ASR</h3>
            <Field label="模型" value={aliyunAsrModel} onChange={setAliyunAsrModel} />
            <Field label="HTTP URL" value={aliyunAsrUrl} onChange={setAliyunAsrUrl} />
            <p className={`text-xs ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>阿里百炼 ASR 第一版在松开说话后提交本段录音识别。</p>
          </section>

          <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
            <h3 className="text-sm font-medium">语音合成 TTS</h3>
            <Field label="模型" value={aliyunTtsModel} onChange={setAliyunTtsModel} />
            <Field label="HTTP URL" value={aliyunTtsUrl} onChange={setAliyunTtsUrl} />
            <label className="flex flex-col gap-1 text-xs">
              <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>系统音色</span>
              <select value={aliyunVoice} onChange={(e) => setAliyunVoice(e.target.value)} className={inputCls}>
                {ALIYUN_VOICES.map((voice) => <option key={voice.id} value={voice.id}>{voice.name}</option>)}
              </select>
            </label>
            <Field label="自定义/当前 voice" value={aliyunVoice} onChange={setAliyunVoice} />
            <label className="flex flex-col gap-1 text-xs">
              <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>语言</span>
              <select value={aliyunLanguage} onChange={(e) => setAliyunLanguage(e.target.value)} className={inputCls}>
                <option value="Chinese">Chinese</option>
                <option value="English">English</option>
                <option value="Auto">Auto</option>
              </select>
            </label>
          </section>
        </>
      )}

      <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
        <label className="flex flex-col gap-1 text-xs">
          <span className={dark ? 'text-[#a1a1aa]' : 'text-slate-500'}>试听文本</span>
          <div className="relative">
            <input value={previewText} onChange={(event) => setPreviewText(event.target.value.slice(0, 120))} placeholder="输入一小段用于试听的文字" className={`${inputCls} h-10 w-full pr-14`} />
            <span className={`absolute right-2 top-1/2 -translate-y-1/2 text-[10px] ${dark ? 'text-[#52525b]' : 'text-slate-400'}`}>{previewText.length}/120</span>
          </div>
        </label>
        {activeProvider === 'volcano' ? (
          <VoicePicker
            value={ttsVoice}
            onChange={(voiceType) => {
              stopPreview()
              setPreviewError('')
              setTtsVoice(voiceType)
            }}
            theme={theme}
            preview={preview}
            previewDisabled={previewDisabled}
            onPreview={playPreview}
          />
        ) : (
          <div className="flex items-center gap-3">
            <button onClick={() => playPreview(previewVoice)} disabled={previewDisabled} className="px-4 py-2 rounded-lg border border-slate-400/40 text-sm font-medium cursor-pointer hover:bg-slate-500/10 disabled:opacity-50">
              {preview?.voiceType === previewVoice && preview.phase === 'playing' ? '停止试听' : '试听音色'}
            </button>
            <span className={`text-xs ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>当前：{previewVoice}</span>
          </div>
        )}
        {previewDisabled && <p className={`text-xs ${dark ? 'text-amber-400' : 'text-amber-700'}`}>保存当前供应商的 API Key 后即可试听音色。</p>}
        {previewError && <p className="text-xs text-rose-500">{previewError}</p>}
      </section>

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

      <div className="flex items-center gap-3">
        <button onClick={save} disabled={saving} className="px-4 py-2 rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white text-sm font-medium cursor-pointer disabled:opacity-50">
          {saving ? '保存中…' : '保存配置'}
        </button>
        <button onClick={test} className="px-4 py-2 rounded-lg border border-slate-400/40 text-sm font-medium cursor-pointer hover:bg-slate-500/10">测试连通</button>
        {status && <span className={`text-xs ${status.type === 'success' ? 'text-emerald-500' : status.type === 'error' ? 'text-rose-500' : 'text-amber-500'}`}>{status.msg}</span>}
      </div>
      <p className={`text-xs ${dark ? 'text-[#52525b]' : 'text-slate-400'}`}>切换供应商后需保存配置；已保存的其他供应商凭证会保留，方便随时切回。</p>
    </div>
  )
}

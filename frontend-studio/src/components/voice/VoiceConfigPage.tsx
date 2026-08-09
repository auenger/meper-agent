/**
 * VoiceConfigPage — admin page for the火山 ASR/TTS config (DB singleton).
 *
 * Loads the singleton config, edits fields inline; access_token fields show
 * the masked value as placeholder and are blank = "don't change". Save +
 * connectivity-test buttons. Matches ModelsPage's form + tanstack-query style.
 */
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  voiceConfigApi,
  voiceConfigKeys,
  type VoiceConfig,
} from '../../services/voice-config-api'

function errMsg(e: unknown): string {
  return (e as { message?: string })?.message ?? '操作失败'
}

export function VoiceConfigPage({ theme }: { theme: 'dark' | 'light' }) {
  const qc = useQueryClient()
  const { data: cfg, isLoading } = useQuery({
    queryKey: voiceConfigKeys.detail,
    queryFn: voiceConfigApi.get,
  })

  const [asrAppid, setAsrAppid] = useState('')
  const [asrRid, setAsrRid] = useState('volc.seedasr.sauc.duration')
  const [asrUrl, setAsrUrl] = useState('wss://openspeech.bytedance.com/api/v3/sauc/bigmodel')
  const [asrToken, setAsrToken] = useState('')
  const [ttsAppid, setTtsAppid] = useState('')
  const [ttsRid, setTtsRid] = useState('seed-tts-2.0')
  const [ttsUrl, setTtsUrl] = useState('wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection')
  const [ttsVoice, setTtsVoice] = useState('zh_female_wanwanxiaohe_moon_bigtts')
  const [ttsToken, setTtsToken] = useState('')
  const [inputRate, setInputRate] = useState(16000)
  const [outputRate, setOutputRate] = useState(24000)
  const [vadMode, setVadMode] = useState('energy')
  const [vadThreshold, setVadThreshold] = useState(0.12)
  const [vadSilence, setVadSilence] = useState(600)
  const [status, setStatus] = useState<{ type: 'success' | 'error' | 'info'; msg: string } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!cfg) return
    setAsrAppid(cfg.asr.appid); setAsrRid(cfg.asr.resource_id); setAsrUrl(cfg.asr.url)
    setTtsAppid(cfg.tts.appid); setTtsRid(cfg.tts.resource_id); setTtsUrl(cfg.tts.url); setTtsVoice(cfg.tts.voice_type)
    setInputRate(cfg.audio.input_rate); setOutputRate(cfg.audio.output_rate)
    setVadMode(cfg.vad.mode); setVadThreshold(cfg.vad.threshold); setVadSilence(cfg.vad.silence_ms)
  }, [cfg])

  const save = async () => {
    setSaving(true); setStatus(null)
    try {
      await voiceConfigApi.save({
        asr: { appid: asrAppid, access_token: asrToken || null, resource_id: asrRid, url: asrUrl },
        tts: { appid: ttsAppid, access_token: ttsToken || null, resource_id: ttsRid, url: ttsUrl, voice_type: ttsVoice },
        audio: { input_rate: inputRate, output_rate: outputRate },
        vad: { mode: vadMode, threshold: vadThreshold, silence_ms: vadSilence },
      })
      setAsrToken(''); setTtsToken('')
      qc.invalidateQueries({ queryKey: voiceConfigKeys.detail })
      setStatus({ type: 'success', msg: '已保存' })
    } catch (e) {
      setStatus({ type: 'error', msg: errMsg(e) })
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
      setStatus({ type: 'error', msg: errMsg(e) })
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

  if (isLoading) return <div className="p-6 text-sm opacity-60">加载语音配置…</div>

  return (
    <div className={`max-w-3xl mx-auto p-6 space-y-4 ${dark ? 'text-[#fafafa]' : 'text-slate-800'}`}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">语音设置</h2>
          <p className={`text-xs mt-1 ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>
            火山引擎豆包 2.0（方舟 v3）流式 ASR/TTS 配置。Resource-Id 指定模型，凭证加密存储。
          </p>
        </div>
        {cfg?.last_test_at && (
          <span className={`text-xs ${cfg.last_test_success ? 'text-emerald-500' : 'text-rose-500'}`}>
            上次测试：{cfg.last_test_success ? '通过' : '失败'}
          </span>
        )}
      </div>

      {/* ASR */}
      <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
        <h3 className="text-sm font-medium">语音识别 ASR</h3>
        <Field label="App ID" value={asrAppid} onChange={setAsrAppid} placeholder="火山方舟 AppID" />
        <Field label="Access Token" value={asrToken} onChange={setAsrToken} placeholder={cfg ? `已配置：${cfg.asr.access_token_masked}（留空不改）` : 'Access Token'} type="password" />
        <Field label="Resource-Id" value={asrRid} onChange={setAsrRid} placeholder="volc.seedasr.sauc.duration" />
        <Field label="WebSocket URL" value={asrUrl} onChange={setAsrUrl} placeholder="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel" />
      </section>

      {/* TTS */}
      <section className={`rounded-lg border p-4 space-y-3 ${card}`}>
        <h3 className="text-sm font-medium">语音合成 TTS</h3>
        <Field label="App ID" value={ttsAppid} onChange={setTtsAppid} placeholder="火山方舟 AppID" />
        <Field label="Access Token" value={ttsToken} onChange={setTtsToken} placeholder={cfg ? `已配置：${cfg.tts.access_token_masked}（留空不改）` : 'Access Token'} type="password" />
        <Field label="Resource-Id" value={ttsRid} onChange={setTtsRid} placeholder="seed-tts-2.0" />
        <Field label="WebSocket URL" value={ttsUrl} onChange={setTtsUrl} placeholder="wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection" />
        <Field label="音色 voice_type" value={ttsVoice} onChange={setTtsVoice} placeholder="zh_female_wanwanxiaohe_moon_bigtts" />
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
        ⚠️ v3 协议字节细节基于公开资料推断，若连通失败请对照火山官方 protocols.py（文档 82379/2516286）校准 volcano.py。
      </p>
    </div>
  )
}

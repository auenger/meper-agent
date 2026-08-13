/**
 * VoiceHomepage — stage-6: agent picker + StateOrb + mic + manual barge-in
 * + live transcript / agent reply bubbles.
 *
 * Wiring summary:
 *   - voice.start carries the chosen agent_id (backend binds the brain to it)
 *   - TTS audio frames arrive on the WS binary channel → useVoicePlayer
 *   - `interrupt` button sends a manual barge-in (also triggered by VAD)
 *   - turn.started/end frame the agent reply accumulation
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Hand, Mic } from 'lucide-react'
import { usePttSpaceTrigger } from '../../hooks/voice/usePttSpaceTrigger'
import { voiceWs } from '../../lib/voice/voice-ws-client'
import { useVoiceRecorder } from '../../hooks/voice/useVoiceRecorder'
import { useVoicePlayer } from '../../hooks/voice/useVoicePlayer'
import { useAudioDevices } from '../../hooks/voice/useAudioDevices'
import { useAuthStore } from '../../stores/auth-store'
import { StateOrb } from './StateOrb'
import { AudioDeviceMenu } from './AudioDeviceMenu'
import type { Agent } from '../../types'

interface TurnMsg {
  key: string
  role: 'user' | 'agent'
  text: string
}

export function VoiceHomepage({ agents, theme }: { agents: Agent[]; theme: 'dark' | 'light' }) {
  const [voiceState, setVoiceState] = useState<string>('idle')
  const [connected, setConnected] = useState(false)
  const [partial, setPartial] = useState('')
  const [turns, setTurns] = useState<TurnMsg[]>([])
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [agentId, setAgentId] = useState<string>('')
  const audioDevices = useAudioDevices()
  const recorder = useVoiceRecorder()
  const { enqueue, clear, outputError } = useVoicePlayer(
    audioDevices.outputDeviceId,
    () => audioDevices.selectOutputDevice(''),
  )
  const agentBufRef = useRef('')
  const turnSeq = useRef(0)
  const [, force] = useState(0)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Default to the first agent once the list loads.
  useEffect(() => {
    const selected = agents.find((agent) => agent.id === agentId)
    if ((!selected || !selected.voiceEnabled) && agents.some((agent) => agent.voiceEnabled)) {
      setAgentId(agents.find((agent) => agent.voiceEnabled)?.id ?? '')
    }
  }, [agents, agentId])

  // Auto-scroll the transcript to the latest entry.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns, agentBufRef.current, partial])

  useEffect(() => {
    const token = useAuthStore.getState().accessToken
    if (!token) return
    voiceWs.resume()
    voiceWs.connect()

    const offState = voiceWs.on('voice.state', (m: { state: string }) => setVoiceState(m.state))
    const offPartial = voiceWs.on('transcript.delta', (m: { content?: string }) => setPartial(m.content || ''))
    const offFinal = voiceWs.on('transcript.final', (m: { content?: string }) => {
      const t = (m.content || '').trim()
      if (t) setTurns((prev) => [...prev, { key: `u${turnSeq.current++}`, role: 'user', text: t }])
      setPartial('')
    })
    const offTurnStart = voiceWs.on('turn.started', () => {
      agentBufRef.current = ''
      force((n) => n + 1)
    })
    const offAgentDelta = voiceWs.on('agent_text_delta', (m: { content?: string }) => {
      agentBufRef.current += m.content || ''
      force((n) => n + 1)
    })
    const offTurnEnd = voiceWs.on('turn.end', () => {
      const t = agentBufRef.current.trim()
      if (t) setTurns((prev) => [...prev, { key: `a${turnSeq.current++}`, role: 'agent', text: t }])
      agentBufRef.current = ''
    })
    const offErr = voiceWs.on('error', (m: { content?: string }) => setErrorMsg(m.content || '出错了'))
    const unsub = useAuthStore.subscribe((s) => {
      if (s.accessToken) voiceWs.reconnectWithFreshToken(s.accessToken)
    })
    const tick = setInterval(() => setConnected(voiceWs.connected), 500)
    return () => {
      offState(); offPartial(); offFinal(); offTurnStart(); offAgentDelta(); offTurnEnd(); offErr()
      clearInterval(tick)
      unsub()
    }
  }, [])

  useEffect(() => { voiceWs.onBinary((buf) => enqueue(buf)) }, [enqueue])
  useEffect(() => {
    const off = voiceWs.on('playback.clear', () => clear())
    return () => off()
  }, [clear])

  const press = useCallback(async () => {
    if (recorder.recording) return  // already in a press (button + space race)
    setErrorMsg(null)
    if (!agentId) {
      setErrorMsg('请先选择一个智能体')
      return
    }
    if (!agents.find((agent) => agent.id === agentId)?.voiceEnabled) {
      setErrorMsg('当前 Agent 未开启语音对话能力')
      return
    }
    clear()  // barge-in: drop any queued TTS playback
    voiceWs.sendJson({ type: 'voice.start', mode: 'ptt', agent_id: agentId })
    const started = await recorder.start(audioDevices.inputDeviceId, audioDevices.fallbackInput)
    if (started) await audioDevices.refreshAfterPermission()
    else voiceWs.sendJson({ type: 'voice.stop' })
  }, [recorder.recording, agentId, agents, clear, audioDevices.inputDeviceId, audioDevices.fallbackInput, audioDevices.refreshAfterPermission])

  const release = useCallback(() => {
    if (!recorder.recording) return
    voiceWs.sendJson({ type: 'voice.release' })
    recorder.stop()
  }, [recorder.recording])

  usePttSpaceTrigger(
    !!connected && !!agentId && !!agents.find((agent) => agent.id === agentId)?.voiceEnabled,
    () => void press(),
    release,
  )

  const dark = theme === 'dark'
  const card = dark ? 'bg-[#18181b] border-[#27272a]' : 'bg-white border-slate-200'
  const canInterrupt = voiceState === 'speaking' || voiceState === 'thinking'

  const bubble = (t: TurnMsg) => (
    <div key={t.key} className={`mb-2 flex ${t.role === 'user' ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[80%] px-3 py-2 rounded-lg text-sm leading-relaxed whitespace-pre-wrap break-words ${
        t.role === 'user'
          ? 'bg-indigo-500 text-white'
          : (dark ? 'bg-[#27272a] text-[#fafafa]' : 'bg-slate-100 text-slate-800')
      }`}>{t.text}</div>
    </div>
  )

  return (
    <div className={`h-full w-full flex flex-col p-4 gap-3 ${dark ? 'text-[#fafafa]' : 'text-slate-800'}`}>
      {/* Top bar: agent picker + connection */}
      <div className={`flex items-center gap-3 px-3 py-2 rounded-lg border ${card}`}>
        <span className="text-xs opacity-60">智能体</span>
        <select
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          disabled={recorder.recording}
          className={`text-sm px-2 py-1 rounded border outline-none cursor-pointer disabled:opacity-50 ${
            dark ? 'bg-[#09090b] border-[#27272a] text-[#fafafa]' : 'bg-slate-50 border-slate-200'
          }`}
        >
          {agents.length === 0 && <option value="">（暂无智能体）</option>}
          {agents.map((a) => (
            <option key={a.id} value={a.id} disabled={!a.voiceEnabled}>
              {a.name}{a.voiceEnabled ? '' : '（未开启语音）'}
            </option>
          ))}
        </select>
        <div className="flex-1" />
        <AudioDeviceMenu
          theme={theme}
          inputDevices={audioDevices.inputDevices}
          outputDevices={audioDevices.outputDevices}
          inputDeviceId={audioDevices.inputDeviceId}
          outputDeviceId={audioDevices.outputDeviceId}
          onInputChange={audioDevices.selectInputDevice}
          onOutputChange={audioDevices.selectOutputDevice}
          loading={audioDevices.loading}
          recording={recorder.recording}
          outputSelectionSupported={audioDevices.outputSelectionSupported}
          notice={audioDevices.notice}
          error={audioDevices.error || outputError}
        />
        <span className={`text-xs ${connected ? 'text-emerald-500' : 'text-amber-500'}`}>
          {connected ? '● 已连接' : '○ 连接中…'}
        </span>
      </div>

      {/* Stage: orb + mic + interrupt */}
      <div className={`flex-1 flex flex-col items-center justify-center gap-5 rounded-lg border ${card}`}>
        <StateOrb state={voiceState} />

        <div className="flex items-center gap-4">
          <button
            onPointerDown={(e) => {
              e.preventDefault()
              try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* ignore */ }
              void press()
            }}
            onPointerUp={() => release()}
            onPointerCancel={() => release()}
            disabled={!connected || !agentId || !agents.find((agent) => agent.id === agentId)?.voiceEnabled}
            className={`w-16 h-16 rounded-full flex items-center justify-center transition-all shadow-lg cursor-pointer text-white disabled:opacity-40 disabled:cursor-not-allowed ${
              recorder.recording ? 'bg-rose-500 hover:bg-rose-600' : 'bg-indigo-500 hover:bg-indigo-600'
            }`}
            aria-label={recorder.recording ? '松开结束说话' : '按住说话'}
          >
            <Mic className="w-7 h-7" />
          </button>

          {canInterrupt && (
            <button
              onClick={() => voiceWs.sendJson({ type: 'interrupt' })}
              className="px-3 py-2 rounded-lg text-xs font-medium border border-amber-500/40 text-amber-500 hover:bg-amber-500/10 cursor-pointer flex items-center gap-1"
            >
              <Hand className="w-3.5 h-3.5" /> 打断
            </button>
          )}
        </div>

        <div className={`text-xs ${dark ? 'text-[#71717a]' : 'text-slate-400'}`}>
          {recorder.recording ? '松开结束，Agent 会语音回复' : '按住麦克风（或空格）说话，松开自动发送（需配置火山 ASR/TTS 凭证）'}
        </div>
        {(recorder.error || errorMsg) && (
          <div className="text-xs text-rose-500">{recorder.error || errorMsg}</div>
        )}
      </div>

      {/* Transcript */}
      <div ref={scrollRef} className={`h-56 overflow-y-auto rounded-lg border p-3 ${card}`}>
        {turns.map(bubble)}
        {agentBufRef.current && bubble({ key: 'cur', role: 'agent', text: agentBufRef.current })}
        {partial && <div className="text-xs text-indigo-400 mt-1">{partial}…</div>}
        {!turns.length && !partial && !agentBufRef.current && (
          <div className={`text-xs ${dark ? 'text-[#52525b]' : 'text-slate-400'}`}>
            对话转写会显示在这里
          </div>
        )}
      </div>
    </div>
  )
}

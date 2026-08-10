import { Hand, Keyboard, Mic, Pause } from 'lucide-react'
import { useVoiceConversation } from '../../hooks/voice/useVoiceConversation'
import { StateOrb } from './StateOrb'
import { AudioDeviceMenu } from './AudioDeviceMenu'

interface ChatVoiceComposerProps {
  agentId?: string
  sessionId?: string
  theme: 'dark' | 'light'
  onExit: () => void
  onTranscriptFinal: (text: string) => void
  onTurnStarted: (message: { request_id?: string; session_id?: string }) => void
  onAgentDelta: (text: string) => void
  onTurnEnd: (message: { request_id?: string; session_id?: string }) => void
  onInterruptRequest: (question: string) => void
  onError: (message: string) => void
}

const STATE_COPY: Record<string, string> = {
  idle: '已暂停，点击开始继续语音对话',
  listening: '正在聆听，说完后会自动发送',
  thinking: 'Agent 正在思考，可直接说话打断',
  speaking: 'Agent 正在回复，可直接说话打断',
  error: '语音服务出现异常',
}

export function ChatVoiceComposer(props: ChatVoiceComposerProps) {
  const dark = props.theme === 'dark'
  const voice = useVoiceConversation({
    enabled: true,
    agentId: props.agentId,
    sessionId: props.sessionId,
    onTranscriptFinal: props.onTranscriptFinal,
    onTurnStarted: props.onTurnStarted,
    onAgentDelta: props.onAgentDelta,
    onTurnEnd: props.onTurnEnd,
    onInterruptRequest: props.onInterruptRequest,
    onError: props.onError,
  })
  const canInterrupt = voice.voiceState === 'thinking' || voice.voiceState === 'speaking'

  const exitToText = () => {
    voice.pause()
    props.onExit()
  }

  return (
    <section
      className={`relative min-h-[218px] overflow-visible rounded-xl border px-4 py-3 ${
        dark ? 'border-[#27272a] bg-[#18181b]' : 'border-slate-200 bg-white'
      }`}
      aria-label="语音输入"
    >
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-sky-400/50 to-transparent" />
      <div className="relative z-10 flex items-center justify-between">
        <div className={`flex items-center gap-2 text-[10px] font-medium ${dark ? 'text-[#a1a1aa]' : 'text-slate-600'}`}>
          <span
            className={`h-2 w-2 rounded-full ${voice.connected ? 'bg-emerald-400' : 'bg-amber-400'}`}
            aria-hidden="true"
          />
          {voice.connected ? '语音已连接' : '正在连接语音服务…'}
        </div>
        <div className="flex items-center gap-1">
          <AudioDeviceMenu
            theme={props.theme}
            inputDevices={voice.audioDevices.inputDevices}
            outputDevices={voice.audioDevices.outputDevices}
            inputDeviceId={voice.audioDevices.inputDeviceId}
            outputDeviceId={voice.audioDevices.outputDeviceId}
            onInputChange={voice.audioDevices.selectInputDevice}
            onOutputChange={voice.audioDevices.selectOutputDevice}
            loading={voice.audioDevices.loading}
            recording={voice.recording}
            outputSelectionSupported={voice.audioDevices.outputSelectionSupported}
            notice={voice.audioDevices.notice}
            error={voice.audioDeviceError}
          />
          <button
            type="button"
            onClick={exitToText}
            className={`flex min-h-11 items-center gap-1.5 rounded-lg px-3 text-[11px] font-semibold transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 cursor-pointer ${
              dark
                ? 'text-[#e4e4e7] hover:bg-white/8'
                : 'text-slate-700 hover:bg-slate-100 hover:text-slate-950'
            }`}
            aria-label="暂停语音并切换到文字输入"
          >
            <Keyboard className="h-3.5 w-3.5" />
            文字输入
          </button>
        </div>
      </div>

      <div className="relative z-10 -mt-2 flex flex-col items-center">
        <StateOrb state={voice.voiceState} compact />
        <p className={`mt-1 min-h-5 max-w-full truncate px-4 text-center text-[11px] ${dark ? 'text-[#a1a1aa]' : 'text-slate-600'}`} aria-live="polite">
          {voice.partial ? `“${voice.partial}”` : STATE_COPY[voice.voiceState]}
        </p>

        <div className="mt-2 flex items-center gap-2">
          {voice.recording ? (
            <button
              type="button"
              onClick={voice.pause}
              className={`flex min-h-11 items-center gap-2 rounded-full border border-rose-400/35 bg-rose-500/15 px-4 text-[11px] font-semibold transition-colors duration-200 hover:bg-rose-500/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400 cursor-pointer ${dark ? 'text-rose-200' : 'text-rose-700'}`}
              aria-label="暂停语音输入"
            >
              <Pause className="h-3.5 w-3.5 fill-current" />
              暂停
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void voice.start()}
              disabled={!voice.connected || !props.agentId || !props.sessionId}
              className="flex min-h-11 items-center gap-2 rounded-full bg-indigo-600 px-4 text-[11px] font-semibold text-white shadow-lg shadow-indigo-950/30 transition-colors duration-200 hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed disabled:opacity-40 cursor-pointer"
              aria-label="开始语音输入"
            >
              <Mic className="h-3.5 w-3.5" />
              开始语音
            </button>
          )}

          {canInterrupt && (
            <button
              type="button"
              onClick={voice.interrupt}
              className={`flex min-h-11 items-center gap-1.5 rounded-full border border-amber-400/35 bg-amber-500/10 px-4 text-[11px] font-semibold transition-colors duration-200 hover:bg-amber-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 cursor-pointer ${dark ? 'text-amber-200' : 'text-amber-700'}`}
              aria-label="打断 Agent 回复"
            >
              <Hand className="h-3.5 w-3.5" />
              打断
            </button>
          )}
        </div>

        {voice.error && (
          <p className={`mt-2 text-center text-[11px] ${dark ? 'text-rose-300' : 'text-rose-600'}`} role="alert">
            {voice.error}
          </p>
        )}
      </div>
    </section>
  )
}

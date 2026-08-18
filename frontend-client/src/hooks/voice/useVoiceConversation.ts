import { useCallback, useEffect, useRef, useState } from 'react'
import { AUTH_MODE } from '../../api/client'
import { useAuthStore } from '../../store/auth'
import { voiceWs } from '../../lib/voice/voice-ws-client'
import { useVoicePlayer } from './useVoicePlayer'
import { useVoiceRecorder } from './useVoiceRecorder'
import { useAudioDevices } from './useAudioDevices'

export type VoiceConversationState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'error'

interface VoiceEventCallbacks {
  onTranscriptFinal?: (text: string) => void
  onTurnStarted?: (message: { request_id?: string; session_id?: string }) => void
  onAgentDelta?: (text: string) => void
  onTurnEnd?: (message: { request_id?: string; session_id?: string }) => void
  onInterruptRequest?: (question: string) => void
  onError?: (message: string) => void
}

interface UseVoiceConversationOptions extends VoiceEventCallbacks {
  enabled: boolean
  agentId?: string
  sessionId?: string
}

/** Shared realtime voice lifecycle for chat surfaces (embed-client edition). */
export function useVoiceConversation(options: UseVoiceConversationOptions) {
  const { enabled, agentId, sessionId } = options
  const callbacksRef = useRef<VoiceEventCallbacks>(options)
  callbacksRef.current = options

  const [voiceState, setVoiceState] = useState<VoiceConversationState>('idle')
  const [connected, setConnected] = useState(false)
  const [partial, setPartial] = useState('')
  const [error, setError] = useState<string | null>(null)
  const audioDevices = useAudioDevices()
  const recorder = useVoiceRecorder()
  const player = useVoicePlayer(
    audioDevices.outputDeviceId,
    () => audioDevices.selectOutputDevice(''),
  )

  useEffect(() => {
    if (!enabled) {
      setConnected(false)
      setVoiceState('idle')
      setPartial('')
      return
    }

    voiceWs.resume()
    voiceWs.connect()
    const offState = voiceWs.on('voice.state', (message: { state?: VoiceConversationState }) => {
      setVoiceState(message.state ?? 'idle')
    })
    const offPartial = voiceWs.on('transcript.delta', (message: { content?: string }) => {
      setPartial(message.content ?? '')
    })
    const offFinal = voiceWs.on('transcript.final', (message: { content?: string }) => {
      const text = (message.content ?? '').trim()
      if (text) callbacksRef.current.onTranscriptFinal?.(text)
      setPartial('')
    })
    const offTurnStarted = voiceWs.on(
      'turn.started',
      (message: { request_id?: string; session_id?: string }) => {
        callbacksRef.current.onTurnStarted?.(message)
      },
    )
    const offAgentDelta = voiceWs.on('agent_text_delta', (message: { content?: string }) => {
      if (message.content) callbacksRef.current.onAgentDelta?.(message.content)
    })
    const offTurnEnd = voiceWs.on(
      'turn.end',
      (message: { request_id?: string; session_id?: string }) => {
        callbacksRef.current.onTurnEnd?.(message)
      },
    )
    const offInterrupt = voiceWs.on('interrupt.request', (message: { question?: string }) => {
      const question = (message.question ?? '').trim()
      if (question) callbacksRef.current.onInterruptRequest?.(question)
    })
    const offError = voiceWs.on('error', (message: { content?: string; code?: string }) => {
      const detail = message.content || '语音对话发生错误'
      setError(detail)
      callbacksRef.current.onError?.(detail)
    })
    const offPlaybackClear = voiceWs.on('playback.clear', player.clear)
    const offBinary = voiceWs.onBinary(player.enqueue)
    const offAuthFailed = voiceWs.onAuthFailed(() => {
      const detail = '语音连接鉴权失败，请重新进入或刷新页面重试'
      setError(detail)
      callbacksRef.current.onError?.(detail)
    })
    // JWT mode: a refreshed access token reconnects the voice channel.
    const unsubscribeAuth = AUTH_MODE === 'jwt'
      ? useAuthStore.subscribe((state) => {
          if (state.accessToken) voiceWs.reconnectWithFreshToken()
        })
      : () => {}
    const connectionTimer = window.setInterval(() => {
      setConnected(voiceWs.connected)
    }, 300)

    return () => {
      voiceWs.sendJson({ type: 'voice.stop' })
      recorder.stop()
      player.clear()
      offState()
      offPartial()
      offFinal()
      offTurnStarted()
      offAgentDelta()
      offTurnEnd()
      offInterrupt()
      offError()
      offPlaybackClear()
      offBinary()
      offAuthFailed()
      unsubscribeAuth()
      window.clearInterval(connectionTimer)
    }
  }, [enabled, player.clear, player.enqueue, recorder.stop])

  const start = useCallback(async () => {
    setError(null)
    if (!agentId || !sessionId) {
      const detail = '请先选择或新建一个会话'
      setError(detail)
      return false
    }
    if (!voiceWs.connected) {
      const detail = '语音连接尚未就绪，请稍后重试'
      setError(detail)
      return false
    }
    voiceWs.sendJson({ type: 'voice.start', mode: 'ptt', agent_id: agentId, session_id: sessionId })
    const started = await recorder.start(
      audioDevices.inputDeviceId,
      audioDevices.fallbackInput,
    )
    if (started) await audioDevices.refreshAfterPermission()
    if (!started) voiceWs.sendJson({ type: 'voice.stop' })
    return started
  }, [
    agentId,
    audioDevices.fallbackInput,
    audioDevices.inputDeviceId,
    audioDevices.refreshAfterPermission,
    recorder.start,
    sessionId,
  ])

  const pause = useCallback(() => {
    voiceWs.sendJson({ type: 'voice.stop' })
    recorder.stop()
    player.clear()
    setPartial('')
    setVoiceState('idle')
  }, [player.clear, recorder.stop])

  const interrupt = useCallback(() => {
    voiceWs.sendJson({ type: 'interrupt' })
    player.clear()
  }, [player.clear])

  const press = useCallback(async () => {
    // Drop any locally queued playback, then listen; backend voice.start
    // barge-ins if the agent is still mid-turn.
    player.clear()
    await start()
  }, [player.clear, start])

  const release = useCallback(() => {
    voiceWs.sendJson({ type: 'voice.release' })
    recorder.stop()
  }, [recorder.stop])

  const retryConnect = useCallback(() => {
    setError(null)
    voiceWs.reconnectWithFreshCredentials()
  }, [])

  return {
    voiceState,
    connected,
    partial,
    error: recorder.error || error,
    recording: recorder.recording,
    playing: player.playing,
    audioDevices,
    audioDeviceError: audioDevices.error || player.outputError,
    start,
    pause,
    interrupt,
    press,
    release,
    retryConnect,
  }
}

/**
 * useVoiceRecorder — capture mic, encode to PCM16@16kHz,
 * push 20ms binary frames to the voice WS.
 *
 * ScriptProcessorNode is intentionally used here: AudioWorklet received
 * all-zero MediaStreamSource input on the target Chromium/macOS environment.
 * The context stays at the device rate; each callback is resampled and framed.
 */
import { useCallback, useRef, useState } from 'react'
import { voiceWs } from '../../lib/voice/voice-ws-client'
import { float32ToPcm16, resampleFloat32 } from '../../lib/voice/audio-codec'

const SAMPLE_RATE = 16000
const FRAME_SAMPLES = SAMPLE_RATE * 0.02

export function useVoiceRecorder() {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const nodeRef = useRef<ScriptProcessorNode | null>(null)

  const start = useCallback(async (inputDeviceId = '', onFallback?: () => void) => {
    setError(null)
    try {
      const audioConstraints: MediaTrackConstraints = {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: { ideal: 1 },
        ...(inputDeviceId ? { deviceId: { exact: inputDeviceId } } : {}),
      }
      let stream: MediaStream
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints })
      } catch (cause) {
        if (!inputDeviceId || !(cause instanceof DOMException) || cause.name !== 'OverconstrainedError') throw cause
        onFallback?.()
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { ...audioConstraints, deviceId: undefined },
        })
      }
      streamRef.current = stream

      const audioTrack = stream.getAudioTracks()[0]
      if (!audioTrack) throw new Error('浏览器没有返回麦克风音轨')
      audioTrack.onended = () => {
        onFallback?.()
        voiceWs.sendJson({ type: 'voice.stop' })
        setRecording(false)
        setError('麦克风已断开，已切换为跟随系统，请重新开始语音')
      }
      const trackSettings = audioTrack.getSettings()
      const captureRate = trackSettings.sampleRate || 48000
      const ctx = new AudioContext({ sampleRate: captureRate })
      ctxRef.current = ctx

      const source = ctx.createMediaStreamSource(stream)
      const captureChannels = Math.max(1, Math.min(trackSettings.channelCount || 1, 2))
      const node = ctx.createScriptProcessor(2048, captureChannels, 1)
      const pending: number[] = []
      node.onaudioprocess = (event) => {
        const channelCount = event.inputBuffer.numberOfChannels
        const sampleLength = event.inputBuffer.length
        const input = new Float32Array(sampleLength)
        for (let channel = 0; channel < channelCount; channel++) {
          const channelData = event.inputBuffer.getChannelData(channel)
          for (let index = 0; index < sampleLength; index++) {
            input[index] += channelData[index] / channelCount
          }
        }
        const targetCount = Math.round(input.length * SAMPLE_RATE / ctx.sampleRate)
        const resampled = resampleFloat32(input, targetCount)
        for (let i = 0; i < resampled.length; i++) pending.push(resampled[i])
        while (pending.length >= FRAME_SAMPLES) {
          const frame = new Float32Array(pending.splice(0, FRAME_SAMPLES))
          voiceWs.sendBinary(float32ToPcm16(frame))
        }
        event.outputBuffer.getChannelData(0).fill(0)
      }

      // Connected processors are pulled by the audio graph; gain=0 prevents echo.
      const sink = ctx.createGain()
      sink.gain.value = 0
      source.connect(node)
      node.connect(sink)
      sink.connect(ctx.destination)

      await ctx.resume()
      if (ctx.state !== 'running') {
        throw new Error('浏览器音频采集未启动，请检查麦克风权限或浏览器自动播放设置')
      }
      voiceWs.sendJson({
        type: 'audio.info',
        context_sample_rate: ctx.sampleRate,
        track_sample_rate: trackSettings.sampleRate,
        channel_count: trackSettings.channelCount,
        track_enabled: audioTrack.enabled,
        track_muted: audioTrack.muted,
        track_state: audioTrack.readyState,
        capture_backend: 'script_processor',
      })

      nodeRef.current = node
      setRecording(true)
      return true
    } catch (e) {
      streamRef.current?.getTracks().forEach((track) => track.stop())
      streamRef.current = null
      ctxRef.current?.close().catch(() => { /* ignore */ })
      ctxRef.current = null
      setError(e instanceof Error ? e.message : '麦克风获取失败')
      return false
    }
  }, [])

  const stop = useCallback(() => {
    if (nodeRef.current) nodeRef.current.onaudioprocess = null
    try { nodeRef.current?.disconnect() } catch { /* ignore */ }
    nodeRef.current = null
    streamRef.current?.getAudioTracks().forEach((track) => {
      track.onended = null
      track.stop()
    })
    streamRef.current?.getVideoTracks().forEach((track) => track.stop())
    streamRef.current = null
    ctxRef.current?.close().catch(() => { /* ignore */ })
    ctxRef.current = null
    setRecording(false)
  }, [])

  return { recording, error, start, stop }
}

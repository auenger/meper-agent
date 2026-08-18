/**
 * useVoicePlayer — play incoming PCM16@24kHz binary frames seamlessly.
 *
 * Schedules AudioBufferSourceNodes back-to-back using a running cursor so
 * consecutive frames are gapless. `clear()` stops everything immediately
 * (called on barge-in `playback.clear`).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { pcm16ToFloat32 } from '../../lib/voice/audio-codec'

const SAMPLE_RATE = 24000

type AudioContextWithSink = AudioContext & {
  setSinkId?: (sinkId: string) => Promise<void>
}

export function useVoicePlayer(outputDeviceId = '', onOutputFallback?: () => void) {
  const ctxRef = useRef<AudioContext | null>(null)
  const outputDeviceRef = useRef(outputDeviceId)
  const outputFallbackRef = useRef(onOutputFallback)
  outputFallbackRef.current = onOutputFallback
  const sinkReadyRef = useRef<Promise<void>>(Promise.resolve())
  const playbackGenerationRef = useRef(0)
  const cursorRef = useRef(0) // next scheduled start time (gapless)
  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set())
  const [playing, setPlaying] = useState(false)
  const [outputError, setOutputError] = useState<string | null>(null)

  const setOutput = useCallback(async (ctx: AudioContext, deviceId: string) => {
    const sinkContext = ctx as AudioContextWithSink
    if (!sinkContext.setSinkId) {
      if (deviceId) {
        setOutputError('当前浏览器不支持选择扬声器，已使用系统默认输出')
        outputFallbackRef.current?.()
      }
      return
    }
    try {
      await sinkContext.setSinkId(deviceId)
      setOutputError(null)
    } catch (cause) {
      setOutputError(cause instanceof Error ? cause.message : '切换扬声器失败')
      if (deviceId) {
        await sinkContext.setSinkId('').catch(() => { /* keep current sink */ })
        outputFallbackRef.current?.()
      }
    }
  }, [])

  const ensureCtx = useCallback(() => {
    if (!ctxRef.current) {
      ctxRef.current = new AudioContext({ sampleRate: SAMPLE_RATE })
      sinkReadyRef.current = setOutput(ctxRef.current, outputDeviceRef.current)
    }
    return ctxRef.current
  }, [setOutput])

  useEffect(() => {
    outputDeviceRef.current = outputDeviceId
    if (ctxRef.current) sinkReadyRef.current = setOutput(ctxRef.current, outputDeviceId)
  }, [outputDeviceId, setOutput])

  const enqueue = useCallback((pcm: ArrayBuffer) => {
    const ctx = ensureCtx()
    const generation = playbackGenerationRef.current
    const schedule = async () => {
      let pending: Promise<void>
      do {
        pending = sinkReadyRef.current
        await pending
      } while (pending !== sinkReadyRef.current)
      if (generation !== playbackGenerationRef.current) return

      if (ctx.state === 'suspended') await ctx.resume().catch(() => { /* ignore */ })
      if (generation !== playbackGenerationRef.current) return
      const float = pcm16ToFloat32(pcm)
      const buf = ctx.createBuffer(1, float.length, SAMPLE_RATE)
      buf.copyToChannel(float, 0)

      const src = ctx.createBufferSource()
      src.buffer = buf
      src.connect(ctx.destination)
      const start = Math.max(ctx.currentTime, cursorRef.current)
      src.start(start)
      cursorRef.current = start + buf.duration
      sourcesRef.current.add(src)
      src.onended = () => {
        sourcesRef.current.delete(src)
        if (sourcesRef.current.size === 0) setPlaying(false)
      }
      setPlaying(true)
    }
    void schedule()
  }, [ensureCtx])

  const clear = useCallback(() => {
    playbackGenerationRef.current += 1
    sourcesRef.current.forEach((s) => {
      try { s.stop() } catch { /* already stopped */ }
    })
    sourcesRef.current.clear()
    const ctx = ctxRef.current
    // Dropping scheduled sources must also drop their future time reservation;
    // otherwise the next reply waits behind audio that was already interrupted.
    cursorRef.current = ctx?.currentTime ?? 0
    setPlaying(false)
  }, [])

  useEffect(() => () => { ctxRef.current?.close().catch(() => { /* ignore */ }) }, [])

  return { enqueue, clear, playing, outputError }
}

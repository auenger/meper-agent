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

export function useVoicePlayer() {
  const ctxRef = useRef<AudioContext | null>(null)
  const cursorRef = useRef(0) // next scheduled start time (gapless)
  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set())
  const [playing, setPlaying] = useState(false)

  const ensureCtx = useCallback(() => {
    if (!ctxRef.current) ctxRef.current = new AudioContext({ sampleRate: SAMPLE_RATE })
    return ctxRef.current
  }, [])

  const enqueue = useCallback((pcm: ArrayBuffer) => {
    const ctx = ensureCtx()
    if (ctx.state === 'suspended') ctx.resume().catch(() => { /* ignore */ })

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
  }, [ensureCtx])

  const clear = useCallback(() => {
    sourcesRef.current.forEach((s) => {
      try { s.stop() } catch { /* already stopped */ }
    })
    sourcesRef.current.clear()
    const ctx = ctxRef.current
    cursorRef.current = ctx ? Math.max(ctx.currentTime, cursorRef.current) : 0
    setPlaying(false)
  }, [])

  useEffect(() => () => { ctxRef.current?.close().catch(() => { /* ignore */ }) }, [])

  return { enqueue, clear, playing }
}

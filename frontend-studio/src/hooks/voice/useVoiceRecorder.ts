/**
 * useVoiceRecorder — capture mic, encode to PCM16@16kHz via AudioWorklet,
 * push 20ms binary frames to the voice WS.
 *
 * The AudioContext is created at 16kHz so the browser resamples the mic for
 * us; the worklet only encodes + frames. A zero-gain sink keeps the worklet
 * graph "alive" (browsers may suspend disconnected nodes) without leaking mic
 * audio to the speakers.
 */
import { useCallback, useRef, useState } from 'react'
import { voiceWs } from '../../lib/voice/voice-ws-client'

const WORKLET_URL = '/voice/pcm-capture-processor.js'
const SAMPLE_RATE = 16000

export function useVoiceRecorder() {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const nodeRef = useRef<AudioWorkletNode | null>(null)

  const start = useCallback(async () => {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      })
      streamRef.current = stream

      const ctx = new AudioContext({ sampleRate: SAMPLE_RATE })
      ctxRef.current = ctx
      await ctx.audioWorklet.addModule(WORKLET_URL)

      const source = ctx.createMediaStreamSource(stream)
      const node = new AudioWorkletNode(ctx, 'pcm-capture-processor')
      node.port.onmessage = (e: MessageEvent) => voiceWs.sendBinary(e.data as ArrayBuffer)

      // Zero-gain sink keeps the worklet processing without echoing to speakers.
      const sink = ctx.createGain()
      sink.gain.value = 0
      source.connect(node)
      node.connect(sink)
      sink.connect(ctx.destination)

      nodeRef.current = node
      setRecording(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : '麦克风获取失败')
    }
  }, [])

  const stop = useCallback(() => {
    try { nodeRef.current?.disconnect() } catch { /* ignore */ }
    nodeRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    ctxRef.current?.close().catch(() => { /* ignore */ })
    ctxRef.current = null
    setRecording(false)
  }, [])

  return { recording, error, start, stop }
}

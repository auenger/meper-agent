/**
 * AudioWorklet processor: captures mic input as Float32 @16kHz, encodes to
 * PCM16 little-endian, accumulates into 20ms frames (320 samples), and posts
 * each frame out as a transferable ArrayBuffer.
 *
 * Runs off the main thread. Registered as 'pcm-capture-processor'.
 * The AudioContext is created at sampleRate 16000 so input is already at the
 * target rate (modern browsers honor an explicit context sampleRate; the
 * encoder is rate-agnostic anyway).
 *
 * Note: AudioWorklet scope cannot import ES modules, so the PCM16 encoder is
 * inlined here (mirrors src/lib/voice/audio-codec.ts).
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._frameSamples = Math.round(16000 * 0.02) // 20ms = 320 samples
    this._buffer = []
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0) return true
    const channel = input[0]
    if (!channel) return true

    for (let i = 0; i < channel.length; i++) this._buffer.push(channel[i])

    while (this._buffer.length >= this._frameSamples) {
      const frame = this._buffer.splice(0, this._frameSamples)
      const pcm = float32ToPcm16(frame)
      // Transfer the underlying buffer (zero-copy) — frame won't be reused.
      this.port.postMessage(pcm.buffer, [pcm.buffer])
    }
    return true
  }
}

function float32ToPcm16(samples) {
  const buf = new ArrayBuffer(samples.length * 2)
  const view = new DataView(buf)
  for (let i = 0; i < samples.length; i++) {
    let s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Int16Array(buf)
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor)

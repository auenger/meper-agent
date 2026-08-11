/**
 * AudioWorklet processor: captures mic input as Float32, resamples it to
 * 16kHz, encodes to PCM16 little-endian, accumulates into 20ms frames
 * (320 samples), and posts each frame out as a transferable ArrayBuffer.
 *
 * Runs off the main thread. Registered as 'pcm-capture-processor'.
 * Browsers usually honor AudioContext({sampleRate: 16000}), but this isn't
 * guaranteed. The explicit resampler also handles a 44.1kHz/48kHz context.
 *
 * Note: AudioWorklet scope cannot import ES modules, so the PCM16 encoder is
 * inlined here (mirrors src/lib/voice/audio-codec.ts).
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this._targetSampleRate = options?.processorOptions?.targetSampleRate || 16000
    this._frameSamples = Math.round(this._targetSampleRate * 0.02)
    this._ratio = sampleRate / this._targetSampleRate
    this._sourceBuffer = []
    this._sourceOffset = 0
    this._targetBuffer = []
  }

  process(inputs, outputs) {
    const input = inputs[0]
    if (!input || input.length === 0) return true
    const channel = input[0]
    if (!channel) return true

    // Explicit pass-through keeps Chromium from treating a side-effect-only
    // worklet as a permanently silent node. The downstream gain is zero, so
    // microphone audio still never reaches the speakers.
    const output = outputs[0]
    if (output && output[0]) output[0].set(channel)

    for (let i = 0; i < channel.length; i++) this._sourceBuffer.push(channel[i])

    while (this._sourceOffset + 1 < this._sourceBuffer.length) {
      const left = Math.floor(this._sourceOffset)
      const fraction = this._sourceOffset - left
      const sample = this._sourceBuffer[left] * (1 - fraction)
        + this._sourceBuffer[left + 1] * fraction
      this._targetBuffer.push(sample)
      this._sourceOffset += this._ratio
    }

    const consumed = Math.floor(this._sourceOffset)
    if (consumed > 0) {
      this._sourceBuffer.splice(0, consumed)
      this._sourceOffset -= consumed
    }

    while (this._targetBuffer.length >= this._frameSamples) {
      const frame = this._targetBuffer.splice(0, this._frameSamples)
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

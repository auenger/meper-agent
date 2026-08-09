/**
 * PCM16 audio codec + resampling helpers for the voice module.
 *
 * - Upstream: mic Float32 @16kHz → PCM16 little-endian (binary WS frame)
 * - Downstream: PCM16 LE @24kHz (TTS) → Float32 → AudioBuffer for playback
 */

/** Convert mono PCM16 little-endian bytes → Float32 samples (-1..1). */
export function pcm16ToFloat32(data: ArrayBuffer): Float32Array {
  const view = new Int16Array(data)
  const out = new Float32Array(view.length)
  for (let i = 0; i < view.length; i++) out[i] = view[i] / 32768
  return out
}

/** Linear-interpolation resample Float32 to a target sample count. */
export function resampleFloat32(samples: Float32Array, dstCount: number): Float32Array {
  if (samples.length === dstCount || dstCount <= 0) return samples
  const out = new Float32Array(dstCount)
  const ratio = (samples.length - 1) / dstCount
  for (let i = 0; i < dstCount; i++) {
    const pos = i * ratio
    const i0 = Math.floor(pos)
    const i1 = Math.min(i0 + 1, samples.length - 1)
    const frac = pos - i0
    out[i] = samples[i0] * (1 - frac) + samples[i1] * frac
  }
  return out
}

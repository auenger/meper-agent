/**
 * Voice config API — get / save / connectivity-test for /voice/config.
 *
 * Uses the shared apiClient (auto auth header + 401 refresh). Field names are
 * snake_case to match the backend schemas. `api_key` is masked in
 * responses; on save, null/empty means "keep existing" (don't change).
 */
import { apiClient } from '../lib/api-client'

export interface VoiceASRConfig {
  resource_id: string
  url: string
}
export interface VoiceTTSConfig {
  resource_id: string
  url: string
  voice_type: string
}
export interface VoiceAudioConfig { input_rate: number; output_rate: number }
export interface VoiceVADConfig { mode: string; threshold: number; silence_ms: number }

export interface VoiceConfig {
  api_key_masked: string
  asr: VoiceASRConfig
  tts: VoiceTTSConfig
  audio: VoiceAudioConfig
  vad: VoiceVADConfig
  last_test_success: boolean | null
  last_test_at: string
}

export interface VoiceConfigInput {
  api_key: string | null
  asr: Record<string, never>
  tts: { voice_type: string }
  audio: { input_rate: number; output_rate: number }
  vad: { mode: string; threshold: number; silence_ms: number }
}

export const voiceConfigKeys = {
  detail: ['voice-config'] as const,
  status: ['voice-config-status'] as const,
}

export const voiceConfigApi = {
  status: () =>
    apiClient.get<{ configured: boolean }>('/api/v1/voice/status').then((r) => r.data),
  get: () => apiClient.get<VoiceConfig>('/api/v1/voice/config').then((r) => r.data),
  save: (body: VoiceConfigInput) =>
    apiClient.put<VoiceConfig>('/api/v1/voice/config', body).then((r) => r.data),
  test: () =>
    apiClient.post<{ success: boolean; message: string }>('/api/v1/voice/config/test').then((r) => r.data),
  preview: (voiceType: string, text: string) =>
    apiClient.post<Blob>(
      '/api/v1/voice/config/preview',
      { voice_type: voiceType, text },
      { responseType: 'blob' },
    ).then((r) => r.data),
}

/**
 * Voice config API — get / save / connectivity-test for /voice/config.
 *
 * Uses the shared apiClient (auto auth header + 401 refresh). Field names are
 * snake_case to match the backend schemas. `access_token` is masked in
 * responses; on save, null/empty means "keep existing" (don't change).
 */
import { apiClient } from '../lib/api-client'

export interface VoiceASRConfig {
  appid: string
  access_token_masked: string
  resource_id: string
  url: string
}
export interface VoiceTTSConfig {
  appid: string
  access_token_masked: string
  resource_id: string
  url: string
  voice_type: string
}
export interface VoiceAudioConfig { input_rate: number; output_rate: number }
export interface VoiceVADConfig { mode: string; threshold: number; silence_ms: number }

export interface VoiceConfig {
  asr: VoiceASRConfig
  tts: VoiceTTSConfig
  audio: VoiceAudioConfig
  vad: VoiceVADConfig
  last_test_success: boolean | null
  last_test_at: string
}

export interface VoiceConfigInput {
  asr: { appid: string; access_token: string | null; resource_id: string; url: string }
  tts: { appid: string; access_token: string | null; resource_id: string; url: string; voice_type: string }
  audio: { input_rate: number; output_rate: number }
  vad: { mode: string; threshold: number; silence_ms: number }
}

export const voiceConfigKeys = {
  detail: ['voice-config'] as const,
}

export const voiceConfigApi = {
  get: () => apiClient.get<VoiceConfig>('/api/v1/voice/config').then((r) => r.data),
  save: (body: VoiceConfigInput) =>
    apiClient.put<VoiceConfig>('/api/v1/voice/config', body).then((r) => r.data),
  test: () =>
    apiClient.post<{ success: boolean; message: string }>('/api/v1/voice/config/test').then((r) => r.data),
}

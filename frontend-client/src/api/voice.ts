/** Voice availability for the embed client — dual-mode URLs. */
import { AUTH_MODE, apiRequest } from './client'

export function fetchVoiceStatus(): Promise<{ configured: boolean }> {
  const path =
    AUTH_MODE === 'apikey' ? '/v1/ext/voice/status' : '/v1/voice/status'
  return apiRequest<{ configured: boolean }>(path)
}

/**
 * Dedicated WebSocket for the voice realtime channel (embed-client edition).
 *
 * Mirrors studio's voice-ws-client.ts (binary audio + JSON dispatch, 4401
 * auth-fail pause, exponential backoff, ping/pong) with dual-mode auth:
 *
 * - jwt    → `?token=<access>`; on 4401 force-refresh and reconnect once.
 * - apikey → browser WS cannot carry headers, and long-lived af_live_ keys
 *   must not leak into URLs (access logs record them). So we first exchange
 *   a 60s single-use ticket via POST /v1/ext/voice/ticket (apiRequest adds
 *   the header credentials) and connect with `?ticket=`.
 *
 * The URL (and, in apikey mode, the ticket) is built on EVERY connect —
 * never cached — so a host page re-injecting credentials takes effect on
 * the next reconnect via reconnectWithFreshCredentials().
 */
import {
  AUTH_MODE,
  apiRequest,
  ensureAccessToken,
  inIframe,
} from '../../api/client'

type JsonHandler = (msg: any) => void
type BinaryHandler = (data: ArrayBuffer) => void
type ErrorHandler = (code: string) => void

const MAX_RECONNECT_DELAY = 30_000
const BASE_RECONNECT_DELAY = 1_000
const WS_AUTH_FAILED_CODE = 4401

function wsBaseUrl(): string {
  const configured = import.meta.env.VITE_WS_BASE_URL
  if (configured) return configured
  if (import.meta.env.DEV) {
    // Direct to the backend — the Vite proxy doesn't forward WS and would
    // idle-timeout the connection (~60s) anyway.
    return 'ws://127.0.0.1:8000'
  }
  // Production: Caddy serves the client and reverse-proxies /api/* (WS
  // included) to the backend, so same-origin is the backend.
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}`
}

async function buildWsUrl(): Promise<string | null> {
  const base = `${wsBaseUrl()}/api/v1/voice/realtime`
  if (AUTH_MODE === 'apikey') {
    try {
      const { ticket } = await apiRequest<{ ticket: string }>(
        '/v1/ext/voice/ticket',
        { method: 'POST' },
      )
      return ticket ? `${base}?ticket=${encodeURIComponent(ticket)}` : null
    } catch {
      return null
    }
  }
  const token = await ensureAccessToken()
  return token ? `${base}?token=${encodeURIComponent(token)}` : null
}

export class VoiceWsClient {
  private ws: WebSocket | null = null
  private reconnectDelay = BASE_RECONNECT_DELAY
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private connecting = false
  private jsonHandlers = new Map<string, Set<JsonHandler>>()
  private binaryHandler: BinaryHandler | null = null
  private disposed = false
  private authFailed = false
  private errorHandlers = new Set<ErrorHandler>()

  connect(): void {
    if (this.disposed || this.authFailed || this.connecting) return
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return
    this.connecting = true
    void buildWsUrl()
      .then((url) => {
        this.connecting = false
        if (!url || this.disposed) {
          this.notifyAuthFailed('EXT_TICKET_FAILED')
          return
        }
        this.open(url)
      })
      .catch(() => {
        this.connecting = false
        this.notifyAuthFailed('EXT_TICKET_FAILED')
      })
  }

  /** Host page re-injected credentials → clear the auth-failed latch and retry. */
  reconnectWithFreshCredentials(): void {
    this.authFailed = false
    this.reconnectDelay = BASE_RECONNECT_DELAY
    this.connect()
  }

  /** JWT mode: force-refresh the access token, then reconnect. */
  reconnectWithFreshToken(): void {
    if (AUTH_MODE === 'apikey') {
      this.reconnectWithFreshCredentials()
      return
    }
    this.authFailed = false
    this.reconnectDelay = BASE_RECONNECT_DELAY
    void ensureAccessToken(true).then((token) => {
      if (token) this.connect()
    })
  }

  private open(url: string): void {
    this.ws = new WebSocket(url)
    this.ws.binaryType = 'arraybuffer'

    this.ws.onopen = () => {
      this.reconnectDelay = BASE_RECONNECT_DELAY
    }

    this.ws.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        this.binaryHandler?.(event.data)
        return
      }
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'ping') {
          this.ws?.send(JSON.stringify({ type: 'pong' }))
          return
        }
        this.emit(msg.type, msg)
      } catch {
        // ignore malformed text frames
      }
    }

    this.ws.onclose = (event: CloseEvent) => {
      this.ws = null
      if (event.code === WS_AUTH_FAILED_CODE) {
        this.notifyAuthFailed('WS_AUTH_FAILED')
        return
      }
      this.scheduleReconnect()
    }

    this.ws.onerror = () => {
      // onclose will fire after onerror
    }
  }

  private notifyAuthFailed(code: string): void {
    this.authFailed = true
    this.errorHandlers.forEach((h) => {
      try { h(code) } catch { /* don't let one handler break others */ }
    })
    if (AUTH_MODE === 'apikey' && inIframe()) {
      try {
        window.parent.postMessage({ type: 'agentflow:token_invalid' }, '*')
      } catch { /* ignore */ }
    }
  }

  /** Subscribe to auth-failure notifications. Returns an unsubscribe fn. */
  onAuthFailed(handler: ErrorHandler): () => void {
    this.errorHandlers.add(handler)
    return () => {
      this.errorHandlers.delete(handler)
    }
  }

  /** Subscribe to a JSON message type. Returns an unsubscribe fn. */
  on(type: string, handler: JsonHandler): () => void {
    if (!this.jsonHandlers.has(type)) this.jsonHandlers.set(type, new Set())
    this.jsonHandlers.get(type)!.add(handler)
    return () => {
      this.jsonHandlers.get(type)?.delete(handler)
    }
  }

  /** Single binary (audio) handler — last setter wins. */
  onBinary(handler: BinaryHandler): () => void {
    this.binaryHandler = handler
    return () => {
      if (this.binaryHandler === handler) this.binaryHandler = null
    }
  }

  sendJson(obj: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj))
  }

  sendBinary(data: ArrayBuffer): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(data)
  }

  disconnect(): void {
    this.disposed = true
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }

  /** Reset disposed flag (e.g. re-entering the voice input mode). */
  resume(): void {
    this.disposed = false
    this.authFailed = false
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  private emit(type: string, msg: unknown): void {
    this.jsonHandlers.get(type)?.forEach((h) => {
      try { h(msg) } catch { /* don't let one handler break others */ }
    })
  }

  private scheduleReconnect(): void {
    if (this.disposed || this.authFailed || this.reconnectTimer) return
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, this.reconnectDelay)
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, MAX_RECONNECT_DELAY)
  }
}

export const voiceWs = new VoiceWsClient()

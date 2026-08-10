/**
 * Dedicated WebSocket for the voice realtime channel.
 *
 * Separate from the notification `wsClient` — that one carries low-frequency
 * task/notification JSON; voice sends high-frequency binary audio frames that
 * would block it. Mirrors ws-client.ts patterns: token in query, 4401
 * auth-fail pause + fresh-token reconnect, exponential backoff, ping/pong.
 *
 * One binary handler (audio frames) + JSON dispatch by `type`. Binary frames
 * are received as ArrayBuffer (binaryType set on open).
 */
import { useAuthStore } from '../../stores/auth-store'
import { ENV } from '../../config/env'

type JsonHandler = (msg: any) => void
type BinaryHandler = (data: ArrayBuffer) => void

const MAX_RECONNECT_DELAY = 30_000
const BASE_RECONNECT_DELAY = 1_000
const WS_AUTH_FAILED_CODE = 4401

export class VoiceWsClient {
  private ws: WebSocket | null = null
  private reconnectDelay = BASE_RECONNECT_DELAY
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private jsonHandlers = new Map<string, Set<JsonHandler>>()
  private binaryHandler: BinaryHandler | null = null
  private disposed = false
  private authFailed = false

  connect(): void {
    if (this.disposed) return
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return
    const token = useAuthStore.getState().accessToken
    if (!token) return
    this.openWithToken(token)
  }

  reconnectWithFreshToken(token: string): void {
    if (this.disposed || !token) return
    this.authFailed = false
    this.reconnectDelay = BASE_RECONNECT_DELAY
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) {
      this.ws.onclose = null
      try { this.ws.close() } catch { /* ignore */ }
      this.ws = null
    }
    this.openWithToken(token)
  }

  private openWithToken(token: string): void {
    const url = `${ENV.WS_BASE_URL}/api/v1/voice/realtime?token=${token}`
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
        this.authFailed = true
        return
      }
      this.scheduleReconnect()
    }

    this.ws.onerror = () => {
      // onclose will fire after onerror
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

  /** Reset disposed flag (e.g. re-entering the voice tab). */
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

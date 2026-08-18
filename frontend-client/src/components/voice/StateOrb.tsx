/**
 * StateOrb — local looping energy video reflecting the voice session state.
 * （studio StateOrb 的翻译版：Tailwind 类换成 styles.css；视频资产来自共享的
 * public 目录，按 BASE_URL 前缀引用。）
 */
import { useCallback, useEffect, useRef, useState } from 'react'

const STATE_LABEL: Record<string, string> = {
  idle: '待命',
  listening: '聆听中',
  thinking: '思考中',
  speaking: '播报中',
  error: '出错',
}

export function StateOrb({ state, compact = false }: { state: string; compact?: boolean }) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [videoFailed, setVideoFailed] = useState(false)
  const active = state === 'listening' || state === 'thinking' || state === 'speaking'

  const syncPlayback = useCallback(() => {
    const video = videoRef.current
    if (!video) return
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (active && !reduceMotion) {
      video.play().catch(() => { /* poster remains visible if autoplay is blocked */ })
    } else {
      video.pause()
    }
  }, [active])

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    syncPlayback()
    media.addEventListener('change', syncPlayback)
    return () => media.removeEventListener('change', syncPlayback)
  }, [syncPlayback])

  const base = import.meta.env.BASE_URL
  return (
    <div className={`voice-orb-wrap ${compact ? 'voice-orb-wrap--compact' : ''}`} aria-live="polite">
      <div
        className={`voice-video-orb ${compact ? 'voice-video-orb--compact' : ''}`}
        data-state={state}
        role="img"
        aria-label={`语音状态：${STATE_LABEL[state] || state}`}
      >
        <span className="voice-video-orb__halo" />
        <div className="voice-video-orb__viewport">
          {!videoFailed ? (
            <video
              ref={videoRef}
              className="voice-video-orb__media"
              src={`${base}voice/voice-presence.mp4`}
              poster={`${base}voice/voice-presence-poster.jpg`}
              preload="auto"
              muted
              loop
              playsInline
              onCanPlay={syncPlayback}
              onError={() => setVideoFailed(true)}
              aria-hidden="true"
            />
          ) : (
            <span className="voice-video-orb__fallback" aria-hidden="true" />
          )}
          <span className="voice-video-orb__tint" aria-hidden="true" />
        </div>
      </div>
      <span className="voice-orb-label">
        {STATE_LABEL[state] || state}
      </span>
    </div>
  )
}

/** StateOrb — animated orb reflecting the voice session state. Pure CSS. */

const STATE_STYLE: Record<string, string> = {
  idle: 'bg-zinc-500',
  listening: 'bg-emerald-500 scale-105 animate-pulse',
  thinking: 'bg-amber-500 animate-pulse',
  speaking: 'bg-indigo-500 scale-110 animate-pulse',
  error: 'bg-rose-500',
}

const STATE_LABEL: Record<string, string> = {
  idle: '待命',
  listening: '聆听中',
  thinking: '思考中',
  speaking: '播报中',
  error: '出错',
}

export function StateOrb({ state }: { state: string }) {
  const style = STATE_STYLE[state] || 'bg-zinc-500'
  const label = STATE_LABEL[state] || state
  // Outer halo rings during active states for a "live" feel.
  const active = state === 'listening' || state === 'thinking' || state === 'speaking'
  return (
    <div className="relative flex flex-col items-center gap-3">
      <div className="relative w-32 h-32 flex items-center justify-center">
        {active && (
          <>
            <span className="absolute inline-flex h-full w-full rounded-full bg-current opacity-10 animate-ping" />
            <span className="absolute inline-flex h-[80%] w-[80%] rounded-full bg-current opacity-20 animate-ping" style={{ animationDelay: '0.4s' }} />
          </>
        )}
        <div className={`relative w-28 h-28 rounded-full ${style} transition-all duration-300 shadow-2xl ${active ? 'text-current' : ''}`} />
      </div>
      <span className="text-xs font-medium opacity-70 tracking-wide">{label}</span>
    </div>
  )
}

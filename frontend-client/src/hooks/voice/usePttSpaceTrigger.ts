import { useEffect, useRef } from 'react'

/**
 * usePttSpaceTrigger — Space-key push-to-talk for a mounted voice surface.
 *
 * keydown Space (first press only; auto-repeat ignored) → onPress.
 * keyup Space → onRelease. preventDefault stops page scroll and the default
 * click on a focused button. When `enabled` is false the listener is detached
 * so Space keeps its normal behavior (e.g. text input). Callers must keep
 * onPress/onRelease referentially stable (useCallback) to avoid re-binds.
 */
export function usePttSpaceTrigger(
  enabled: boolean,
  onPress: () => void,
  onRelease: () => void,
) {
  // Track in-flight press so a keyup without a matching keydown (e.g. enabled
  // toggled mid-press) doesn't fire a stray onRelease.
  const pressing = useRef(false)
  useEffect(() => {
    if (!enabled) {
      pressing.current = false
      return
    }
    const isSpace = (e: KeyboardEvent) => e.code === 'Space' || e.key === ' '
    const onDown = (e: KeyboardEvent) => {
      if (!isSpace(e) || e.repeat || pressing.current) return
      e.preventDefault()
      pressing.current = true
      onPress()
    }
    const onUp = (e: KeyboardEvent) => {
      if (!isSpace(e) || !pressing.current) return
      e.preventDefault()
      pressing.current = false
      onRelease()
    }
    window.addEventListener('keydown', onDown)
    window.addEventListener('keyup', onUp)
    return () => {
      window.removeEventListener('keydown', onDown)
      window.removeEventListener('keyup', onUp)
    }
  }, [enabled, onPress, onRelease])
}

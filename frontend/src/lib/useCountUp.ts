import { useEffect, useRef, useState } from 'react'

// Animates a number toward `target` with an ease-out cubic ramp (~500ms).
// First paint starts at the current target (no flash on cached data); the
// animation runs when the value *changes* — e.g. 0 → 306 as a query lands.
// Respects prefers-reduced-motion by jumping straight to the value.
export function useCountUp(target: number, duration = 500): number {
  const [value, setValue] = useState(target)
  const prevRef = useRef(target)

  useEffect(() => {
    const from = prevRef.current
    if (from === target) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      prevRef.current = target
      setValue(target)
      return
    }
    let raf: number
    const start = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      setValue(p < 1 ? Math.round(from + (target - from) * eased) : target)
      if (p < 1) raf = requestAnimationFrame(tick)
      else prevRef.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      prevRef.current = target
    }
  }, [target, duration])

  return value
}

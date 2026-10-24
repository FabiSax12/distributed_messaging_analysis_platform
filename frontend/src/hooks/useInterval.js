import { useEffect, useRef } from 'react'

// setInterval that always calls the latest version of the callback.
// `delay = null` pauses it. With `immediate` it runs once on mount.
export function useInterval(callback, delay, { immediate = false } = {}) {
  const saved = useRef(callback)
  saved.current = callback

  useEffect(() => {
    if (delay == null) return undefined
    if (immediate) saved.current()
    const id = setInterval(() => saved.current(), delay)
    return () => clearInterval(id)
  }, [delay, immediate])
}

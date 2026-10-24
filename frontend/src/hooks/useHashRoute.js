// Hash routing (#/cases/ID). CloudFront does not rewrite SPA routes (see
// infra/modules/frontend), so with hash routing any reload always requests
// index.html and never collides with /api/*.

import { useEffect, useState } from 'react'

function parse() {
  const path = window.location.hash.replace(/^#/, '') || '/'
  const parts = path.split('/').filter(Boolean)
  return { path, parts }
}

export function useHashRoute() {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const onChange = () => setRoute(parse())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function navigate(path) {
  window.location.hash = path
}

// Coordinator REST client. One method per endpoint in shared/api.py.
//
// Every request carries the Cognito ACCESS token as `Authorization: Bearer`
// (set by AuthProvider through `configureAuth`). On a 401 the client refreshes
// once and retries; a second 401 ends the session. Presigned S3 requests
// (putToS3, report download) never carry the API token.

import { API_BASE } from '../config.js'

let auth = { getToken: () => null, refresh: async () => null }

export function configureAuth(next) {
  auth = next
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(describeDetail(status, detail))
    this.status = status
    this.detail = detail
    // 'session' (401), 'forbidden' (403), 'unavailable' (0/5xx), 'request' (other 4xx)
    this.kind =
      status === 401 ? 'session' : status === 403 ? 'forbidden' : status === 0 || status >= 500 ? 'unavailable' : 'request'
  }
}

function describeDetail(status, detail) {
  if (status === 401) return 'Your session expired or is not valid. Sign in again.'
  if (status === 403) {
    // The coordinator answers 403 both for a role denial and for a request
    // that did not come through CloudFront (missing origin header).
    const text = typeof detail === 'string' ? detail : detail?.detail
    if (text && /origin/i.test(text)) return 'The coordinator rejected a request that did not come through CloudFront.'
    return 'Your role does not allow this action.'
  }
  if (status === 503 && typeof detail === 'string' && /cognito/i.test(detail)) {
    return `The coordinator's authentication is not configured (${detail}).`
  }
  if (!detail) {
    if (status >= 500) return `The coordinator responded ${status} with no detail (is it down or unreachable?)`
    return `HTTP error ${status}`
  }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg, type}, ...]
    return detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join(' · ')
  }
  if (detail.missing) return `Not found in the dataset: ${detail.missing.join(', ')}`
  if (detail.message) return detail.message
  return JSON.stringify(detail)
}

async function send(method, path, body, token) {
  const headers = {}
  if (body) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`
  try {
    return await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'No network response: could not reach the coordinator.')
  }
}

async function request(method, path, body) {
  let res = await send(method, path, body, auth.getToken())
  if (res.status === 401 && auth.getToken()) {
    const fresh = await auth.refresh()
    if (fresh) res = await send(method, path, body, fresh)
  }
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data)
  return data
}

export const api = {
  me: () => request('GET', '/api/me'),
  adminUsers: (cursor) => request('GET', `/api/admin/users${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  setRole: (username, role, enabled) => request('POST', '/api/admin/roles', { username, role, enabled }),
  state: () => request('GET', '/api/state'),
  metrics: (minutes = 30) => request('GET', `/api/metrics?minutes=${minutes}`),
  dataset: (prefix = '') => request('GET', `/api/dataset?prefix=${encodeURIComponent(prefix)}`),
  listCases: (status) => request('GET', `/api/cases${status ? `?status=${status}` : ''}`),
  getCase: (id) => request('GET', `/api/cases/${encodeURIComponent(id)}`),
  createCase: (payload) => request('POST', '/api/cases', payload),
  cancelCase: (id) => request('DELETE', `/api/cases/${encodeURIComponent(id)}`),
  reportUrl: (id) => request('GET', `/api/cases/${encodeURIComponent(id)}/report`),
  caseOutputs: (id) => request('GET', `/api/cases/${encodeURIComponent(id)}/outputs`),
  uploadUrls: (filenames) => request('POST', '/api/uploads', { filenames }),
}

// In development (localhost) the buckets do not accept CORS, so presigned S3
// URLs are rewritten to go through the Vite proxy:
//   https://<bucket>.s3.amazonaws.com/uploads/x?signature
//   -> /__s3/<bucket>/uploads/x?signature
// In production (served by CloudFront) they are used as-is.
const S3_HOST = /^https:\/\/([a-z0-9.-]+)\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com(\/.*)$/

export function toDevS3Url(url) {
  if (!import.meta.env.DEV) return url
  const m = url.match(S3_HOST)
  return m ? `/__s3/${m[1]}${m[2]}` : url
}

// Uploads a file straight to S3 with the presigned PUT URL signed by the
// coordinator (the bytes do not go through the coordinator). XHR instead of
// fetch so upload progress can be reported.
//
// The body is sent WITHOUT Content-Type: the coordinator signs with SigV2 and no
// ContentType, and in SigV2 the Content-Type is part of the signature. If the
// browser sent "video/mp4" (as it does for a File), S3 would respond
// SignatureDoesNotMatch. `file.slice(0, size, '')` gives the same content with
// an empty type, without copying it.
export function putToS3(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', toDevS3Url(url))
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve()
      const code = xhr.responseText.match(/<Code>([^<]+)<\/Code>/)?.[1]
      reject(new Error(`S3 rejected the upload of ${file.name} (${xhr.status}${code ? ` ${code}` : ''})`))
    }
    xhr.onerror = () =>
      reject(
        new Error(
          `Could not upload ${file.name} to S3. If the dashboard is not on the CloudFront domain or running under "npm run dev", the bucket blocks it via CORS.`,
        ),
      )
    xhr.send(file.slice(0, file.size, ''))
  })
}

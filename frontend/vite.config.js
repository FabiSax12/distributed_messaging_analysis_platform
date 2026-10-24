import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// In production CloudFront serves the dashboard and /api/* goes to the coordinator
// on the same domain (same origin, no CORS). In development, Vite proxies
// /api/* (REST and WebSocket) to VITE_PROXY_TARGET: the already-deployed
// CloudFront distribution (default) or a local coordinator
// (http://localhost:8000).
//
// It also proxies the S3 buckets (/__s3/<bucket>/...): the buckets only have
// CORS for the CloudFront domain, so from localhost the browser cannot PUT/GET
// directly with the presigned URLs. Going through the proxy makes the request
// same-origin and S3 sees the correct bucket Host, which is part of the
// signature. See `toDevS3Url` in src/api/client.js.
const DEFAULT_TARGET = 'http://localhost:8000'
const DEFAULT_BUCKETS = ''

function proxyError(res, message) {
  if (!res || typeof res.writeHead !== 'function' || res.headersSent) return
  res.writeHead(502, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify({ detail: message }))
}

function s3Proxies(buckets) {
  return Object.fromEntries(
    buckets.map((bucket) => [
      `/__s3/${bucket}`,
      {
        target: `https://${bucket}.s3.amazonaws.com`,
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.slice(`/__s3/${bucket}`.length),
        configure: (proxy) => {
          // No localhost Origin/Referer: S3 does not evaluate CORS for this request.
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.removeHeader('origin')
            proxyReq.removeHeader('referer')
          })
          proxy.on('error', (err, _req, res) =>
            proxyError(res, `The Vite proxy could not connect to S3 (${bucket}): ${err.code || err.message}`),
          )
        },
      },
    ]),
  )
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_PROXY_TARGET || DEFAULT_TARGET
  const buckets = (env.VITE_S3_BUCKETS || DEFAULT_BUCKETS).split(',').map((b) => b.trim()).filter(Boolean)

  return {
    plugins: [react()],
    base: './',
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
          secure: true,
          ws: true,
          // If the coordinator does not respond, Vite answers an empty 500 that
          // shows up in the dashboard as a backend error. We answer 502 with a
          // detail saying what happened and where the proxy was pointing.
          configure: (proxy) => {
            proxy.on('error', (err, _req, res) =>
              proxyError(
                res,
                `The Vite proxy could not connect to the coordinator at ${target} (${err.code || err.message}). Check VITE_PROXY_TARGET in frontend/.env.local.`,
              ),
            )
          },
        },
        ...s3Proxies(buckets),
      },
    },
  }
})

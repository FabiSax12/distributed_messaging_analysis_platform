# Integration contract for Camila

Backend work does not require waiting for the frontend, full dataset or M5.

## Authentication

Use Cognito Hosted UI authorization code + PKCE for the public SPA client.
Send the **access token**, not the ID token, on every REST call:
`Authorization: Bearer <token>`. Refresh/sign out on expiry; avoid persistent
localStorage token storage. Existing local dev works without Cognito only when
both the issuer and origin secret are empty. Cloud origin protection without
an issuer fails closed. CloudFront continues adding the origin header.

Browser WebSocket contract:

```js
new WebSocket(url, ['bearer', accessToken])
```

The server selects `bearer`, validates before accepting and closes on token
expiry. Tokens are not passed in URLs. On token refresh, reconnect the socket.

`GET /api/me` returns `{subject, roles}`. Analysts can read/upload/create;
supervisors can also cancel; administrators additionally manage roles through
`GET /api/admin/users?cursor=...` and `POST /api/admin/roles` with
`{username, role, enabled}`. List responses include `users` and a pagination
`cursor`. User creation/invitations remain Fabián's manual Cognito task.

## Findings and reports

The existing report URL now returns `findings`, `categories` and a `timeline`
plus the previous fields. Each report subtask includes `analysis` when present.
The case-detail subtask `output_meta.analysis` also remains available. Evidence
contains original source keys/hashes and message indexes, bounding boxes or
audio segment times. Render user text escaped by React; never use raw HTML.
Empty findings are valid. Rule scores are not calibrated probabilities.

`/api/healthz` is the unauthenticated liveness route through CloudFront (it
still requires the edge-added origin header). No client token is needed by
the deploy smoke check. A 401/403 must not be treated as dashboard JSON.

## Dataset and M5

Keep the schemas and generator ownership already agreed. The load client
accepts a local case manifest; source files stay outside git. Worker options
`MODEL_DEVICE`, `MODEL_THREADS`, `AUDIO_MODEL`, `AUDIO_COMPUTE_TYPE` are
environment settings, not values accepted from untrusted case params.
Default images contain CPU PyTorch, YOLOv8n and cached Whisper tiny/int8.
The audio image defaults to offline cached weights; a custom model must be baked, or the operator must explicitly set HF_HUB_OFFLINE=0. A CUDA image/driver and measurement-based routing remain M5 work; changing
device alone does not install GPU support. Benchmark thread counts and audio
quantization on CPU if GPU access is unavailable.

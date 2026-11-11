# Integration contract for Camila

Backend work does not require waiting for the frontend, full dataset or M5.

For setup, code locations and delivery checks, start with [Camila's handoff](handoff-camila.md).

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

`GET /api/cases/{id}/report` returns a presigned URL envelope, not the report
contents. Fetch that URL without an API bearer header. The report JSON includes
`findings`, `categories` and a `timeline` plus the previous fields.
Each report subtask includes `analysis` when present.
The case-detail subtask `output_meta.analysis` also remains available. Evidence
contains original source keys/hashes and message indexes, bounding boxes or
audio segment times. Render user text escaped by React; never use raw HTML.
Empty findings are valid. Rule scores are not calibrated probabilities.

## Input and output files

`GET /api/cases/{id}/outputs` presigns the files of every sub-task, also while
the case is still running:

```json
{
  "case_id": "...",
  "subtasks": [
    {
      "subtask_id": "...",
      "outputs": [{"key": "results/.../analysis.json", "url": "...", "expires_in": 900}],
      "input": {"key": "uploads/.../voice.opus", "url": "...", "expires_in": 900}
    }
  ]
}
```

Every sub-task is listed; `outputs` is empty until the worker writes results.
`input` presigns the original `input_key` (never moved or deleted; the
`content/<sha256>` copy is internal) and forces a `Content-Type` from the
extension (`.opus` -> `audio/ogg`, `.m4a` -> `audio/mp4`, `.mkv` ->
`video/x-matroska`, ...), because dashboard uploads are stored without one.
Fetch the URLs without the API bearer header; they expire after
`expires_in` seconds, so request the endpoint again to re-sign them. The case
detail view uses `input` for the inline image/audio/video preview of each
file and of each entry of the report's per-file analysis.

## Health

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

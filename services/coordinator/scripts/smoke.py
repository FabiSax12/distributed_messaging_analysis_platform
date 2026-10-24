#!/usr/bin/env python3
"""Smoke test against REAL AWS, meant for the USER to run by hand
(no agent/CI runs it). Requires:

  1. The coordinator running locally against `.env.local`
     (`uv run --package coordinator uvicorn coordinator.main:app --reload`).
  2. At least one `fake_worker.py` running for each pool involved (video,
     audio, metadata) so the case can close on its own.

Steps: uploads 4 tiny files via `POST /api/uploads` (presigned PUT), creates
a heterogeneous case with those 4 files, polls `GET /api/cases/{id}` until
it is terminal, and verifies that:
  - the final status is `partially_completed` (the .txt is an unsupported
    format, pre-failed on purpose),
  - the report exists in S3 (`GET /api/cases/{id}/report`),
  - the case counts match those in the report.

Clear output on stdout; exit code 1 if anything does not add up.
"""

from __future__ import annotations

import sys
import time

import httpx
from coordinator.config import get_settings

from shared.states import TERMINAL_CASE

_POLL_INTERVAL_S = 2.0
_POLL_TIMEOUT_S = 180.0

# Minimal bytes, they do NOT need to be decodable by a real decoder: the
# fake_worker does not run ffmpeg, it only routes by extension and simulates
# processing. It is enough for them to exist in the dataset bucket.
_FILES: dict[str, bytes] = {
    "smoke.mp4": b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
    "smoke.mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00fake-mp3-bytes",
    "smoke.png": b"\x89PNG\r\n\x1a\nfake-png-bytes",
    "smoke.txt": b"this is not media, it must be pre-failed",
}


def _fail(message: str) -> None:
    print(f"FAILED: {message}")
    sys.exit(1)


def main() -> None:
    settings = get_settings()
    base_url = f"http://localhost:{settings.API_PORT}"
    client = httpx.Client(base_url=base_url, timeout=30.0)

    print(
        f"== smoke test against {base_url} (dataset bucket: {settings.DATASET_BUCKET}) =="
    )

    print(f"-> uploading {len(_FILES)} files via /api/uploads")
    upload_resp = client.post("/api/uploads", json={"filenames": list(_FILES)})
    if upload_resp.status_code != 201:
        _fail(
            f"POST /api/uploads returned {upload_resp.status_code}: {upload_resp.text}"
        )
    upload_body = upload_resp.json()

    input_keys: list[str] = []
    for entry in upload_body["urls"]:
        content = _FILES[entry["filename"]]
        put_resp = httpx.put(entry["url"], content=content, timeout=30.0)
        if put_resp.status_code not in (200, 204):
            _fail(
                f"presigned PUT for {entry['filename']} returned {put_resp.status_code}"
            )
        input_keys.append(entry["input_key"])
    print(f"   uploaded: {input_keys}")

    print("-> creating heterogeneous case")
    create_resp = client.post(
        "/api/cases",
        json={
            "files": [{"input_key": key} for key in input_keys],
            "priority": "normal",
            "label": "smoke test",
        },
    )
    if create_resp.status_code != 201:
        _fail(f"POST /api/cases returned {create_resp.status_code}: {create_resp.text}")
    case = create_resp.json()
    case_id = case["case_id"]
    print(
        f"   case_id={case_id} subtask_count={case['subtask_count']} "
        f"prefailed_count={case['prefailed_count']}"
    )
    if case["prefailed_count"] != 1:
        _fail(
            f"expected 1 pre-failed sub-task (the .txt), got {case['prefailed_count']}"
        )

    print(f"-> polling GET /api/cases/{case_id} (timeout {_POLL_TIMEOUT_S:.0f}s)")
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    detail = None
    while time.monotonic() < deadline:
        detail_resp = client.get(f"/api/cases/{case_id}")
        if detail_resp.status_code != 200:
            _fail(f"GET /api/cases/{case_id} returned {detail_resp.status_code}")
        detail = detail_resp.json()
        status = detail["case"]["status"]
        print(f"   status={status} pending_count={detail['case']['pending_count']}")
        if status in {s.value for s in TERMINAL_CASE}:
            break
        time.sleep(_POLL_INTERVAL_S)
    else:
        _fail(
            "the case did not reach a terminal status within the timeout (is a fake_worker running?)"
        )

    assert detail is not None
    final_status = detail["case"]["status"]
    if final_status != "partially_completed":
        _fail(f"expected status='partially_completed', got '{final_status}'")

    print("-> verifying the report exists in S3")
    report_resp = client.get(f"/api/cases/{case_id}/report")
    if report_resp.status_code != 200:
        _fail(
            f"GET /api/cases/{case_id}/report returned {report_resp.status_code}: {report_resp.text}"
        )
    report_url = report_resp.json()["url"]
    report_body = httpx.get(report_url, timeout=30.0)
    if report_body.status_code != 200:
        _fail(f"the report's presigned URL returned {report_body.status_code}")
    report = report_body.json()

    if report["subtask_count"] != case["subtask_count"]:
        _fail(
            f"report subtask_count ({report['subtask_count']}) does not match "
            f"the case's ({case['subtask_count']})"
        )

    print(f"   report OK: {report['summary']}")
    print("== smoke test OK ==")


if __name__ == "__main__":
    main()

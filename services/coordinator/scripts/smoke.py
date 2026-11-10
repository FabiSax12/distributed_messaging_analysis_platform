#!/usr/bin/env python3
"""Smoke test of the coordinator over HTTP only (local stack or AWS).

Uploads tiny files through `POST /api/uploads` (presigned PUT), creates a
case with them, polls `GET /api/cases/{id}` printing every sub-task state
change (so `retrying` shows up), and checks the final status and report.

Needs the coordinator and at least one worker per pool involved
(`services/workers/scripts/fake_worker.py` is enough). Examples:

    uv run --package coordinator python services/coordinator/scripts/smoke.py
    # unsupported file -> pre-failed sub-task, case partially_completed
    uv run --package coordinator python services/coordinator/scripts/smoke.py \\
        --files chat.txt notes.xyz --expect partially_completed
    # create and exit, then resume polling later (coordinator restart)
    ... smoke.py --no-wait
    ... smoke.py --case-id 01J... --expect any

Exit code 1 if anything does not add up.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import PurePosixPath

import httpx

_TERMINAL = {"completed", "partially_completed", "failed", "cancelled"}

# Minimal bytes per extension: fake workers do not decode them.
_CONTENT: dict[str, bytes] = {
    ".txt": b"[1/2/24, 10:00] Ana: hola\n[1/2/24, 10:01] Luis: hola Ana\n",
    ".json": b'{"name": "chat", "messages": []}',
    ".jpg": b"\xff\xd8\xff\xe0fake-jpeg-bytes",
    ".png": b"\x89PNG\r\n\x1a\nfake-png-bytes",
    ".mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00fake-mp3-bytes",
    ".opus": b"OggSfake-opus-bytes",
    ".mp4": b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
}


def _fail(message: str) -> None:
    print(f"FAILED: {message}")
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument(
        "--files", nargs="+", default=["smoke.txt", "smoke.jpg", "smoke.mp3"]
    )
    parser.add_argument("--priority", default="medium")
    parser.add_argument(
        "--expect", default="completed", help="final case status, or 'any'"
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--case-id", help="poll an existing case instead of creating one")
    parser.add_argument("--no-wait", action="store_true", help="create and exit")
    return parser.parse_args()


def create_case(client: httpx.Client, files: list[str], priority: str) -> str:
    resp = client.post("/api/uploads", json={"filenames": files})
    if resp.status_code != 201:
        _fail(f"POST /api/uploads returned {resp.status_code}: {resp.text}")
    input_keys = []
    for entry in resp.json()["urls"]:
        suffix = PurePosixPath(entry["filename"]).suffix.lower()
        content = _CONTENT.get(suffix, b"unsupported content")
        put = httpx.put(entry["url"], content=content, timeout=30.0)
        if put.status_code not in (200, 204):
            _fail(f"presigned PUT for {entry['filename']} returned {put.status_code}")
        input_keys.append(entry["input_key"])

    resp = client.post(
        "/api/cases",
        json={
            "files": [{"input_key": key} for key in input_keys],
            "priority": priority,
            "label": "smoke test",
        },
    )
    if resp.status_code != 201:
        _fail(f"POST /api/cases returned {resp.status_code}: {resp.text}")
    case = resp.json()
    print(
        f"created case_id={case['case_id']} files={case['file_count']} "
        f"subtasks={case['subtask_count']} prefailed={case['prefailed_count']}"
    )
    return case["case_id"]


def poll(client: httpx.Client, case_id: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    seen: dict[str, tuple[str, int]] = {}
    case_status = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/cases/{case_id}")
        if resp.status_code != 200:
            _fail(f"GET /api/cases/{case_id} returned {resp.status_code}")
        detail = resp.json()
        if detail["case"]["status"] != case_status:
            case_status = detail["case"]["status"]
            print(f"  case    {case_status} (pending={detail['case']['pending_count']})")
        for sub in detail["subtasks"]:
            state = (sub["status"], sub["attempt"])
            if seen.get(sub["subtask_id"]) != state:
                seen[sub["subtask_id"]] = state
                error = f" error={sub['error']['code']}" if sub.get("error") else ""
                print(
                    f"  subtask {sub['subtask_id'][-4:]} {sub['input_key'].rsplit('/', 1)[-1]:<12}"
                    f" {sub['status']:<10} attempt={sub['attempt']}"
                    f" worker={sub['worker_id']}{error}"
                )
        if case_status in _TERMINAL:
            return detail
        time.sleep(0.5)
    _fail(f"case {case_id} not terminal after {timeout_s:.0f} s (workers running?)")
    raise AssertionError  # unreachable


def check_report(client: httpx.Client, detail: dict) -> None:
    case_id = detail["case"]["case_id"]
    resp = client.get(f"/api/cases/{case_id}/report")
    if resp.status_code != 200:
        _fail(f"GET /api/cases/{case_id}/report returned {resp.status_code}: {resp.text}")
    report = httpx.get(resp.json()["url"], timeout=30.0)
    if report.status_code != 200:
        _fail(f"the report's presigned URL returned {report.status_code}")
    body = report.json()
    if body["subtask_count"] != len(detail["subtasks"]):
        _fail(
            f"report has {body['subtask_count']} sub-tasks, "
            f"the case has {len(detail['subtasks'])}"
        )
    ids = [s["subtask_id"] for s in detail["subtasks"]]
    if len(ids) != len(set(ids)) or detail["case"]["total"] != len(ids):
        _fail(f"duplicated or missing sub-tasks: total={detail['case']['total']} ids={ids}")
    print(f"report: {body['summary']}")


def main() -> None:
    args = parse_args()
    client = httpx.Client(base_url=args.api, timeout=30.0)
    case_id = args.case_id or create_case(client, args.files, args.priority)
    if args.no_wait:
        return
    detail = poll(client, case_id, args.timeout)
    final = detail["case"]["status"]
    if args.expect != "any" and final != args.expect:
        _fail(f"expected status {args.expect!r}, got {final!r}")
    check_report(client, detail)
    print(f"OK case {case_id} -> {final}")


if __name__ == "__main__":
    main()

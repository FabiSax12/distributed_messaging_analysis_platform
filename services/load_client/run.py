"""Concurrent manifest upload/case driver; local or CloudFront with bearer auth."""

import argparse
import asyncio
import csv
import json
import os
import time
from pathlib import Path

import httpx

TERMINAL = {"completed", "partially_completed", "failed", "cancelled"}


async def case(
    client, base, manifest, index, timeout, cancel_after=None, priority=None
):
    started = time.monotonic()
    case_id, outcome, detail = "", "client_error", ""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        files = []
        for source in data["sources"]:
            path = (manifest.parent / source["path"]).resolve()
            if not path.is_relative_to(manifest.parent.resolve()):
                raise ValueError("Source escapes manifest directory")
            response = await client.post(
                base + "/api/uploads", json={"filenames": [path.name]}
            )
            response.raise_for_status()
            upload = response.json()["urls"][0]
            # A presigned S3 request must never carry the API bearer token.
            async with httpx.AsyncClient(timeout=timeout) as s3:
                with path.open("rb") as stream:

                    def blocks():
                        while chunk := stream.read(1024 * 1024):
                            yield chunk

                    # httpx async requests require an async byte iterator.
                    async def content():
                        for chunk in blocks():
                            yield chunk

                    put = await s3.put(
                        upload["url"],
                        content=content(),
                        headers={"Content-Length": str(path.stat().st_size)},
                    )
                    put.raise_for_status()
            files.append(
                {
                    "input_key": upload["input_key"],
                    "params": {
                        **{
                            key: source[key]
                            for key in ("chat_format", "timezone")
                            if key in source
                        },
                        "sha256": source["sha256"],
                    },
                }
            )
        response = await client.post(
            base + "/api/cases",
            json={
                "files": files,
                "label": f"load-{index}",
                "priority": priority or data.get("priority", "medium"),
            },
        )
        if response.status_code in (413, 429, 503):
            return {
                "index": index,
                "case_id": "",
                "outcome": "admission_rejected",
                "seconds": time.monotonic() - started,
                "detail": str(response.status_code),
            }
        response.raise_for_status()
        case_id = response.json()["case_id"]
        accepted = time.monotonic()
        cancelled = False
        while time.monotonic() - started < timeout:
            response = await client.get(base + "/api/cases/" + case_id)
            response.raise_for_status()
            state = response.json()
            outcome = state["case"]["status"]
            if outcome in TERMINAL:
                break
            if (
                cancel_after is not None
                and not cancelled
                and time.monotonic() - accepted >= cancel_after
            ):
                response = await client.delete(base + "/api/cases/" + case_id)
                response.raise_for_status()
                cancelled = True
            await asyncio.sleep(0.5)
        else:
            outcome = "client_timeout"
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        detail = (
            f"HTTP {exc.response.status_code}"
            if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__
        )
        outcome = "client_error"
    return {
        "index": index,
        "case_id": case_id,
        "outcome": outcome,
        "seconds": time.monotonic() - started,
        "detail": detail,
    }


async def run(args):
    started = time.monotonic()
    token = os.environ.get("ACCESS_TOKEN", "")
    headers = {"Authorization": "Bearer " + token} if token else {}
    if os.environ.get("ORIGIN_VERIFY_SECRET"):
        headers["X-Origin-Verify"] = os.environ["ORIGIN_VERIFY_SECRET"]
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        semaphore = asyncio.Semaphore(args.concurrency)

        pattern = getattr(args, "priority_pattern", [None])

        async def one(index):
            async with semaphore:
                before = time.monotonic()
                try:
                    async with asyncio.timeout(args.timeout):
                        return await case(
                            client,
                            args.url.rstrip("/"),
                            args.manifest,
                            index,
                            args.timeout,
                            args.cancel_after,
                            priority=pattern[index % len(pattern)],
                        )
                except TimeoutError:
                    return {
                        "index": index,
                        "case_id": "",
                        "outcome": "client_timeout",
                        "seconds": time.monotonic() - before,
                        "detail": "Total case deadline exceeded",
                    }

        rows = await asyncio.gather(*(one(index) for index in range(args.cases)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    seconds = time.monotonic() - started
    args.out.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "cases": len(rows),
                "wall_seconds": seconds,
                "completed_per_second": sum(
                    row["outcome"] == "completed" for row in rows
                )
                / seconds,
                "note": "Wall time includes uploads, semaphore waiting, processing and polling.",
            },
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "cases": len(rows),
                "outcomes": {
                    value: sum(row["outcome"] == value for row in rows)
                    for value in sorted({row["outcome"] for row in rows})
                },
            }
        )
    )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--cancel-after", type=float)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("experiments/fault_tests/burst/results/cases.csv"),
    )
    args = parser.parse_args()
    if min(args.cases, args.concurrency, args.timeout) <= 0:
        parser.error("cases, concurrency and timeout must be positive")
    rows = asyncio.run(run(args))
    if any(row["outcome"] in {"client_error", "client_timeout"} for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

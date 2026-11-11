"""Record origin bypass, role denials and optional container privilege evidence."""

import argparse
import csv
import os
import subprocess

import httpx


def main():
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="CloudFront base URL")
    parser.add_argument(
        "--origin",
        help="Direct coordinator URL, to test rejection or network isolation",
    )
    parser.add_argument(
        "--container",
        help="Local Docker container to inspect; run this tool over SSM for cloud nodes",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("experiments/m4_security/results/probes.csv")
    )
    args = parser.parse_args()
    rows = []

    def probe(label, method, url, token, expected, headers=None):
        request_headers = {"Authorization": "Bearer " + token} if token else {}
        request_headers.update(headers or {})
        try:
            response = httpx.request(method, url, headers=request_headers, timeout=10)
            status = response.status_code
            passed = status in expected
        except httpx.HTTPError:
            status = "network_blocked"
            passed = label == "origin_bypass"
        rows.append({"probe": label, "status": status, "passed": passed})

    probe("anonymous", "GET", args.url.rstrip("/") + "/api/me", "", {401, 403})
    for role in ("analyst", "supervisor", "administrator"):
        token = os.environ.get("ACCESS_TOKEN_" + role.upper())
        if not token:
            rows.append(
                {"probe": role, "status": "not_run_missing_token", "passed": False}
            )
            continue
        probe(role + "_read", "GET", args.url.rstrip("/") + "/api/me", token, {200})
        probe(
            role + "_admin",
            "GET",
            args.url.rstrip("/") + "/api/admin/users",
            token,
            {200} if role == "administrator" else {403},
        )
        probe(
            role + "_cancel",
            "DELETE",
            args.url.rstrip("/") + "/api/cases/security-probe-not-a-case",
            token,
            {403} if role == "analyst" else {404},
        )
    if args.origin:
        probe(
            "origin_bypass",
            "GET",
            args.origin.rstrip("/") + "/api/me",
            "",
            {403},
            {"X-Origin-Verify": "invalid-probe"},
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["probe", "status", "passed"])
        writer.writeheader()
        writer.writerows(rows)
    if args.container:
        result = subprocess.run(
            [
                "docker",
                "exec",
                args.container,
                "python",
                "-c",
                'import os; print("uid="+str(os.getuid())); print("".join(x for x in open("/proc/self/status") if x.startswith(("CapEff:","NoNewPrivs:"))))',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        args.out.with_suffix(".privileges.txt").write_text(result.stdout)
        assert "uid=0\n" not in result.stdout
        assert "0000000000000000" in result.stdout
        assert any(
            line.startswith("NoNewPrivs:") and line.split()[-1] == "1"
            for line in result.stdout.splitlines()
        )
    print(f"Recorded {len(rows)} security probes; no token values written")
    if not all(row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

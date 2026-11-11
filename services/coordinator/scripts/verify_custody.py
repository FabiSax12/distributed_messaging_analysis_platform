"""Verify the chain; a privileged DB administrator can still rewrite its history."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def verify(rows, anchor=None):
    previous = "0" * 64
    count = 0
    for row in rows:
        expected = hashlib.sha256((previous + row["payload"]).encode()).hexdigest()
        if row["previous_hash"] != previous or row["hash"] != expected:
            raise ValueError(f"Custody chain mismatch at sequence {row['sequence']}")
        previous = expected
        count += 1
        if anchor and count == anchor["records"] and previous != anchor["head"]:
            raise ValueError("Custody prefix differs from the external anchor")
    if anchor and count < anchor["records"]:
        raise ValueError("Custody chain is shorter than the external anchor")
    return count, previous


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--anchor", type=Path)
    args = parser.parse_args()
    if args.self_check:
        body = '{"event":"synthetic"}'
        row = {
            "sequence": 1,
            "payload": body,
            "previous_hash": "0" * 64,
            "hash": hashlib.sha256(("0" * 64 + body).encode()).hexdigest(),
        }
        assert verify([row], {"records": 1, "head": row["hash"]})[0] == 1
        row["payload"] += "tampered"
        try:
            verify([row])
        except ValueError:
            print("Custody tamper detection passed")
            return
        raise AssertionError("Tampering accepted")
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:  # noqa: SIM117
        with conn.cursor(name="custody_verify") as cursor:
            cursor.execute(
                "SELECT sequence, payload, previous_hash, hash FROM custody ORDER BY sequence"
            )
            count, head = verify(
                cursor, json.loads(args.anchor.read_text()) if args.anchor else None
            )
    print(f"Verified {count} records; head={head}")


if __name__ == "__main__":
    main()

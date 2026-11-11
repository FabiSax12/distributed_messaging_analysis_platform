"""Real PostgreSQL admission/custody checks in a transaction rolled back entirely."""

import os
import uuid
from importlib.resources import files

import psycopg
from coordinator.admission import reserve
from coordinator.config import Settings
from coordinator.repo.cases import insert
from coordinator.repo.subtasks import insert_many
from coordinator.routing.planner import plan_case
from fastapi import HTTPException
from psycopg import sql
from psycopg.rows import dict_row
from shared.analysis import CasePriority
from shared.api import CaseFileIn
from shared.models import CaseItem


def main():
    settings = Settings(
        AMQP_URL="amqp://localhost",
        DATABASE_URL=os.environ["DATABASE_URL"],
        S3_BUCKET="test",
        M1_MAX_ACTIVE_CASES=1,
        M1_TASK_BASE_BYTES=100,
        M1_MEMORY_BUDGET_BYTES=10000,
        _env_file=None,
    )
    with (
        psycopg.connect(
            settings.DATABASE_URL, row_factory=dict_row, connect_timeout=5
        ) as conn,
        conn.transaction(force_rollback=True),
    ):
        schema = "check_" + uuid.uuid4().hex
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(
            sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
        )
        conn.execute(files("coordinator.db").joinpath("schema.sql").read_text())
        tasks, _ = plan_case(
            [CaseFileIn(input_key="fixture.txt")], "synthetic", CasePriority.LOW
        )
        reserve(conn, settings, tasks, 10, {"fixture.txt": 10})
        with psycopg.connect(settings.DATABASE_URL, row_factory=dict_row) as other:
            assert not other.execute(
                "SELECT pg_try_advisory_xact_lock(66000002) AS locked"
            ).fetchone()["locked"]
        insert(
            conn,
            CaseItem(
                case_id="synthetic",
                priority=CasePriority.LOW,
                file_count=1,
                total=1,
                pending_count=1,
            ),
        )
        insert_many(conn, tasks)
        try:
            reserve(conn, settings, tasks, 10, {"fixture.txt": 10})
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("Active case limit was not enforced")
        rows = conn.execute("SELECT * FROM custody ORDER BY sequence").fetchall()
        assert len(rows) == 2 and rows[1]["previous_hash"] == rows[0]["hash"]
        try:
            with conn.transaction():
                conn.execute("UPDATE custody SET payload = 'tampered'")
        except psycopg.errors.RaiseException:
            pass
        else:
            raise AssertionError("Custody mutation accepted")
    print(
        "PostgreSQL atomic admission, cross-connection lock and custody checks passed; test schema rolled back"
    )


if __name__ == "__main__":
    main()

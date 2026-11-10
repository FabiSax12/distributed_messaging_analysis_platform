"""Small admission self-check, with fake SQL responses; no infrastructure needed."""

from types import SimpleNamespace

from coordinator.admission import RESERVATION_KEY, reserve
from coordinator.config import Settings
from fastapi import HTTPException


class Connection:
    def __init__(self, active=0, memory=0, unreserved=0, locked=True):
        self.rows = iter(
            [
                {"locked": locked},
                {"active": active, "memory": memory, "unreserved": unreserved},
            ]
        )

    def execute(self, sql, params):
        assert "pg_try_advisory_xact_lock" in sql or "FROM cases" in sql
        return SimpleNamespace(fetchone=lambda: next(self.rows))


def main():
    config = Settings(
        AMQP_URL="amqp://local",
        DATABASE_URL="postgresql://local",
        S3_BUCKET="local",
        M1_MAX_ACTIVE_CASES=2,
        M1_MEMORY_BUDGET_BYTES=1000,
        M1_TASK_BASE_BYTES=100,
        _env_file=None,
    )

    def task():
        return SimpleNamespace(
            subtask_id="t1",
            input_key="a.txt",
            operation="text_analyze",
            queue="work.text",
            params={RESERVATION_KEY: {"memory_bytes": -100000}},
        )

    tasks = [task()]
    reserve(Connection(), config, tasks, 10, {"a.txt": 10})
    assert tasks[0].params[RESERVATION_KEY]["memory_bytes"] == 140
    for conn, expected in [
        (Connection(active=2), 503),
        (Connection(memory=900), 503),
        (Connection(unreserved=1), 503),
        (Connection(locked=False), 503),
    ]:
        try:
            reserve(conn, config, [task()], 10, {"a.txt": 10})
            raise AssertionError("Overload accepted")
        except HTTPException as exc:
            assert exc.status_code == expected and exc.headers["Retry-After"] == "5"
    try:
        reserve(Connection(), config, [task()], 1000, {"a.txt": 1000})
        raise AssertionError("Oversized reservation accepted")
    except HTTPException as exc:
        assert exc.status_code == 413
    print(
        "Admission budget, overload, caller spoofing checks passed (SQL integration not tested)"
    )


if __name__ == "__main__":
    main()

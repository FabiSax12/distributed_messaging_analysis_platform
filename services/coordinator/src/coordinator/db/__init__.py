"""PostgreSQL access: one connection pool per process and the schema.

psycopg 3 with plain SQL, no ORM. `psycopg_pool.ConnectionPool` is
thread-safe: API threads, consumer threads and the sweeper each borrow a
connection for one unit of work (`with pool.connection() as conn:` commits
on success and rolls back on exception). Rows come back as dicts
(`dict_row`) whose keys match the pydantic models in `shared.models`.
"""

from __future__ import annotations

import logging
from importlib.resources import files

from psycopg import OperationalError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

logger = logging.getLogger(__name__)

# Serializes concurrent schema application (two coordinators starting at once).
_SCHEMA_LOCK_ID = 6600_0001

# Database unreachable: the message being handled is fine, retry it later.
DB_ERRORS: tuple[type[BaseException], ...] = (OperationalError, PoolTimeout)


def create_pool(url: str, *, max_size: int) -> ConnectionPool:
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        kwargs={"row_factory": dict_row},
        # Test a pooled connection before lending it, so a database restart
        # costs one reconnect instead of one failed query per idle connection.
        check=ConnectionPool.check_connection,
        # Fail fast while the database is down instead of blocking a thread
        # for psycopg_pool's default 30 s; callers retry (consumers requeue
        # with backoff, the sweeper and outbox loops retry next cycle).
        timeout=5,
        open=False,
        name="coordinator",
    )
    pool.open(wait=True, timeout=30)
    return pool


def apply_schema(pool: ConnectionPool) -> None:
    """Run `schema.sql` (idempotent DDL) in one transaction."""
    ddl = files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_ID,))
        conn.execute(ddl)
    logger.info("database schema applied")

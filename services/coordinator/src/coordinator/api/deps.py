"""FastAPI dependencies shared by the `api/` routers.

The database pool, repos, barrier and outbox relay are built once in
`main.py`'s `lifespan` and stored in `app.state`; these functions hand them
to the handlers as `Annotated[T, Depends(...)]` aliases (also keeps ruff's
B008 quiet: `Depends` lives in the annotation, not in a default value).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from psycopg_pool import ConnectionPool

from ..aws import s3_client
from ..barrier.close import Barrier
from ..config import Settings, get_settings
from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..routing.outbox import OutboxRelay


def get_s3() -> Any:
    return s3_client()


def get_db(request: Request) -> ConnectionPool:
    return request.app.state.db


def get_barrier(request: Request) -> Barrier:
    return request.app.state.barrier


def get_outbox(request: Request) -> OutboxRelay:
    return request.app.state.outbox


SettingsDep = Annotated[Settings, Depends(get_settings)]
S3Dep = Annotated[Any, Depends(get_s3)]
DbDep = Annotated[ConnectionPool, Depends(get_db)]
BarrierDep = Annotated[Barrier, Depends(get_barrier)]
OutboxDep = Annotated[OutboxRelay, Depends(get_outbox)]


def get_cases_repo(barrier: BarrierDep) -> CasesRepo:
    return barrier.cases


def get_subtasks_repo(barrier: BarrierDep) -> SubTasksRepo:
    return barrier.subtasks


CasesRepoDep = Annotated[CasesRepo, Depends(get_cases_repo)]
SubTasksRepoDep = Annotated[SubTasksRepo, Depends(get_subtasks_repo)]

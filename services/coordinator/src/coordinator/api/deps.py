"""FastAPI dependencies shared by the `api/` routers.

Previously, `cases.py`/`uploads.py`/`dataset.py` rebuilt their repos/clients
by hand inside each handler (private factories like `_cases_repo()`, or
calling `..aws.s3_client()` directly). This module centralizes that in
dependency functions so the routers receive them via
`fastapi.Depends()`, like the rest of an idiomatic FastAPI app. The
functions here still use the cached singletons from `..aws`/
`..config`; the only thing that changes is HOW they reach the handler.

They are exposed as `Annotated[T, Depends(...)]` aliases (the style
recommended by FastAPI) instead of `= Depends(...)` as a parameter default:
besides being shorter in each router, it keeps `ruff` (rule B008) from
flagging every use as "function call in an argument default"; here there is
no default, `Depends(...)` lives in the type annotation, not in the default
value.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends

from ..aws import dynamodb_client, s3_client, sqs_client
from ..config import Settings, get_settings
from ..repo.cases import CasesRepo
from ..repo.subtasks import SubTasksRepo
from ..repo.workers import WorkersRepo


def get_s3() -> Any:
    return s3_client()


def get_sqs() -> Any:
    return sqs_client()


def get_dynamodb() -> Any:
    return dynamodb_client()


SettingsDep = Annotated[Settings, Depends(get_settings)]
S3Dep = Annotated[Any, Depends(get_s3)]
SqsDep = Annotated[Any, Depends(get_sqs)]
DynamoDbDep = Annotated[Any, Depends(get_dynamodb)]


def get_cases_repo(settings: SettingsDep) -> CasesRepo:
    return CasesRepo(dynamodb_client(), settings.TABLE_CASES)


def get_subtasks_repo(settings: SettingsDep) -> SubTasksRepo:
    return SubTasksRepo(dynamodb_client(), settings.TABLE_SUBTASKS)


def get_workers_repo(settings: SettingsDep) -> WorkersRepo:
    return WorkersRepo(dynamodb_client(), settings.TABLE_WORKERS)


CasesRepoDep = Annotated[CasesRepo, Depends(get_cases_repo)]
SubTasksRepoDep = Annotated[SubTasksRepo, Depends(get_subtasks_repo)]
WorkersRepoDep = Annotated[WorkersRepo, Depends(get_workers_repo)]

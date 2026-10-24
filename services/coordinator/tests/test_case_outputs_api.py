"""Integration: `GET /api/cases/{id}/outputs` through the real router (TestClient).

Unlike `/report` (which requires the case to be terminal, because the
consolidated report is only uploaded when the barrier closes), `/outputs`
presigns whatever is already in each sub-task's `output_keys` regardless of
the case state: a sub-task can finish while the others keep running, and
there is no reason to wait for the case to close before letting that file be
seen.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from coordinator.barrier.close import close_subtask
from fastapi.testclient import TestClient
from shared.messages import ResultMessage, utcnow
from shared.models import CaseItem, SubTaskItem
from shared.routing import MediaType, Operation, Pool, Priority
from shared.states import SubTaskStatus


@contextlib.asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture
def client(aws: dict) -> Iterator[TestClient]:
    from coordinator.main import app

    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as test_client:
        yield test_client


def _make_case_with_subtasks(repos, case_id: str, n: int) -> list[SubTaskItem]:
    subtasks = [
        SubTaskItem.planned(
            case_id=case_id,
            subtask_id=f"{case_id}-{i:04d}",
            input_key=f"dataset/{case_id}-{i}.mp3",
            media_type=MediaType.AUDIO,
            operation=Operation.AUDIO_CONVERT,
            pool=Pool.AUDIO,
            priority=Priority.NORMAL,
        )
        for i in range(1, n + 1)
    ]
    repos["subtasks"].batch_put_planned(subtasks)
    repos["cases"].put_new(
        CaseItem(case_id=case_id, file_count=n, total=n, pending_count=n)
    )
    for subtask in subtasks:
        repos["subtasks"].mark_enqueued(case_id, subtask.subtask_id)
    return subtasks


def _close_with_outputs(
    *, repos, aws, settings, case_id: str, subtask_id: str, output_keys: list[str]
) -> None:
    result = ResultMessage(
        subtask_id=subtask_id,
        case_id=case_id,
        status=SubTaskStatus.COMPLETED,
        attempt=1,
        worker_id="w1",
        finished_at=utcnow(),
        output_keys=output_keys,
    )
    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket=settings.RESULTS_BUCKET,
    )


def test_case_outputs_404_when_case_missing(client: TestClient) -> None:
    resp = client.get("/api/cases/does-not-exist/outputs")
    assert resp.status_code == 404


def test_case_outputs_empty_when_query_by_case_returns_nothing(
    client: TestClient, repos
) -> None:
    # `CaseItem` requires total/file_count >= 1 (there is always at least one
    # file), so to actually see `query_by_case` return [] we simulate the
    # orphan case documented in `create_case`: the case's `PutItem` ran but
    # the sub-tasks' `BatchWriteItem` never got persisted.
    case_id = "outputs-no-subtasks"
    repos["cases"].put_new(CaseItem(case_id=case_id, file_count=1, total=1, pending_count=1))

    resp = client.get(f"/api/cases/{case_id}/outputs")
    assert resp.status_code == 200
    assert resp.json() == {"case_id": case_id, "subtasks": []}


def test_case_outputs_empty_when_no_subtask_has_output_keys(
    client: TestClient, repos
) -> None:
    case_id = "outputs-empty"
    _make_case_with_subtasks(repos, case_id, 2)

    resp = client.get(f"/api/cases/{case_id}/outputs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"] == case_id
    assert body["subtasks"] == []


def test_case_outputs_returns_presigned_url_per_output_key(
    client: TestClient, repos, aws: dict, settings
) -> None:
    case_id = "outputs-basic"
    subtasks = _make_case_with_subtasks(repos, case_id, 2)
    done, still_pending = subtasks
    keys = [
        f"results/{case_id}/{done.subtask_id}/out.mp3",
        f"results/{case_id}/{done.subtask_id}/thumb.jpg",
    ]
    _close_with_outputs(
        repos=repos,
        aws=aws,
        settings=settings,
        case_id=case_id,
        subtask_id=done.subtask_id,
        output_keys=keys,
    )

    resp = client.get(f"/api/cases/{case_id}/outputs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"] == case_id
    # Only the sub-task with output_keys appears; the still-pending one does not.
    assert len(body["subtasks"]) == 1
    entry = body["subtasks"][0]
    assert entry["subtask_id"] == done.subtask_id
    assert [o["key"] for o in entry["outputs"]] == keys
    for output in entry["outputs"]:
        assert output["expires_in"] == settings.PRESIGN_EXPIRES_S
        assert output["url"].startswith("https://")
    assert still_pending.subtask_id not in [
        s["subtask_id"] for s in body["subtasks"]
    ]


def test_case_outputs_available_before_case_is_terminal(
    client: TestClient, repos, aws: dict, settings
) -> None:
    case_id = "outputs-not-terminal"
    subtasks = _make_case_with_subtasks(repos, case_id, 2)
    _close_with_outputs(
        repos=repos,
        aws=aws,
        settings=settings,
        case_id=case_id,
        subtask_id=subtasks[0].subtask_id,
        output_keys=[f"results/{case_id}/{subtasks[0].subtask_id}/out.mp3"],
    )

    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 1  # the case is still "processing", not terminal

    resp = client.get(f"/api/cases/{case_id}/outputs")
    assert resp.status_code == 200
    assert len(resp.json()["subtasks"]) == 1

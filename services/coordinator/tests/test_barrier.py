"""`barrier/close.py` + `barrier/finalize.py`: duplicates, out-of-order messages, real concurrency,
a crash between pending_count=0 and finalize, and cancellation."""

from __future__ import annotations

import logging
import threading

from coordinator.barrier.close import close_subtask
from coordinator.barrier.finalize import finalize

from shared.messages import ErrorCode, ErrorInfo, ResultMessage, utcnow
from shared.models import CaseItem, SubTaskItem
from shared.routing import MediaType, Operation, Pool, Priority
from shared.states import CaseStatus, SubTaskStatus, state_order


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
        CaseItem(
            case_id=case_id,
            file_count=n,
            total=n,
            pending_count=n,
        )
    )
    for subtask in subtasks:
        repos["subtasks"].mark_enqueued(case_id, subtask.subtask_id)
    return subtasks


def _completed_result(case_id: str, subtask_id: str, attempt: int = 1) -> ResultMessage:
    return ResultMessage(
        subtask_id=subtask_id,
        case_id=case_id,
        status=SubTaskStatus.COMPLETED,
        attempt=attempt,
        worker_id="w1",
        finished_at=utcnow(),
        output_keys=[f"results/{case_id}/{subtask_id}/out.bin"],
    )


def test_duplicate_terminal_result_counts_once(repos, aws) -> None:
    case_id = "dupcase"
    subtasks = _make_case_with_subtasks(repos, case_id, 2)
    result = _completed_result(case_id, subtasks[0].subtask_id)

    kwargs = {
        "cases_repo": repos["cases"],
        "subtasks_repo": repos["subtasks"],
        "dynamodb": aws["dynamodb"],
        "s3": aws["s3"],
        "results_bucket": "test-results-bucket",
    }
    close_subtask(result, **kwargs)
    close_subtask(result, **kwargs)  # duplicate: same ResultMessage again

    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 1  # only decremented once


def test_stale_running_after_completed_is_ignored(repos) -> None:
    case_id = "stalecase"
    subtasks = _make_case_with_subtasks(repos, case_id, 1)
    subtask_id = subtasks[0].subtask_id

    # completed on attempt 1 (terminal state_order, the highest possible)
    updated = repos["subtasks"].update_progress(
        case_id,
        subtask_id,
        status=SubTaskStatus.COMPLETED.value,
        state_order=state_order(SubTaskStatus.COMPLETED, attempt=1),
        attempt=1,
        worker_id="w1",
        progress=None,
        started_at_if_missing=None,
    )
    assert updated is True

    # late running from the same attempt: lower state_order, ignored
    updated_again = repos["subtasks"].update_progress(
        case_id,
        subtask_id,
        status=SubTaskStatus.RUNNING.value,
        state_order=state_order(SubTaskStatus.RUNNING, attempt=1),
        attempt=1,
        worker_id="w1",
        progress=50,
        started_at_if_missing=None,
    )
    assert updated_again is False

    subtask = repos["subtasks"].query_by_case(case_id)[0]
    assert subtask.status == SubTaskStatus.COMPLETED


def test_concurrent_close_finalizes_case_exactly_once(repos, aws) -> None:
    case_id = "concase"
    subtasks = _make_case_with_subtasks(repos, case_id, 2)

    kwargs = {
        "cases_repo": repos["cases"],
        "subtasks_repo": repos["subtasks"],
        "dynamodb": aws["dynamodb"],
        "s3": aws["s3"],
        "results_bucket": "test-results-bucket",
    }

    results = [_completed_result(case_id, st.subtask_id) for st in subtasks]

    threads = [
        threading.Thread(target=close_subtask, args=(result,), kwargs=kwargs)
        for result in results
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    case = repos["cases"].get(case_id, consistent=True)
    assert case.status == CaseStatus.COMPLETED
    assert case.pending_count == 0

    # A single report in S3 (deterministic put_object to the same key, not
    # duplicated or corrupted).
    obj = aws["s3"].get_object(Bucket="test-results-bucket", Key=case.report_key)
    assert obj["Body"].read()  # the report has content


def test_crash_before_finalize_is_repaired_by_duplicate_close(
    repos, aws, monkeypatch
) -> None:
    """Simulates the process "dying" right after pending_count=0 but before
    calling `finalize` (we patch `_maybe_finalize` to do nothing the first
    time). A second `close_subtask` with a duplicate result (or the sweeper)
    must repair the barrier and finalize the case."""
    import coordinator.barrier.close as close_module

    case_id = "crashcase"
    subtasks = _make_case_with_subtasks(repos, case_id, 1)
    result = _completed_result(case_id, subtasks[0].subtask_id)

    monkeypatch.setattr(close_module, "_maybe_finalize", lambda *a, **k: None)

    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )

    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 0
    assert case.status != CaseStatus.COMPLETED  # not finalized yet

    # Now without the monkeypatch: a duplicate of the same result triggers the
    # "late result" path, which does check and repair the barrier.
    monkeypatch.undo()
    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )

    repaired = repos["cases"].get(case_id, consistent=True)
    assert repaired.status == CaseStatus.COMPLETED


def test_cancellation_closes_case_as_cancelled(repos, aws) -> None:
    case_id = "cancelcase"
    subtasks = _make_case_with_subtasks(repos, case_id, 1)
    repos["cases"].update_cancel_requested(case_id)

    result = ResultMessage(
        subtask_id=subtasks[0].subtask_id,
        case_id=case_id,
        status=SubTaskStatus.CANCELLED,
        attempt=1,
        worker_id="w1",
        finished_at=utcnow(),
        error=ErrorInfo(code=ErrorCode.CANCELLED, message="cancelado"),
    )
    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )

    case = repos["cases"].get(case_id, consistent=True)
    assert case.status == CaseStatus.CANCELLED


def test_concurrent_close_same_subtask_id_counts_once(repos, aws, monkeypatch) -> None:
    """Two real threads closing the SAME `subtask_id` (two nearly identical
    `ResultMessage`s, as a repeated SQS Standard delivery would produce) at
    the same time: the barrier must decrement `pending_count` only once and
    the case must end up closed with a single report, no matter which of the
    two threads won the race.

    `moto` (unlike real DynamoDB) does not correctly serialize two concurrent
    `TransactWriteItems` competing for THE SAME item: under real threads it
    sometimes lets both "win" (see https://github.com/getmoto/moto - a known
    limitation of the emulator, not of the coordinator code, which does no
    locking of its own because it relies on DynamoDB's real atomicity). To
    exercise the `close_subtask` logic with genuinely concurrent threads
    without inheriting that emulator bug, here we serialize only the raw
    `transact_write_items` call against the moto backend with a lock;
    everything else (barrier-synchronized start, building the updates,
    exception handling, `finalize`) still runs for real on two threads.
    """
    case_id = "samesubtaskcase"
    subtasks = _make_case_with_subtasks(repos, case_id, 1)
    result_a = _completed_result(case_id, subtasks[0].subtask_id)
    result_b = _completed_result(case_id, subtasks[0].subtask_id)

    write_lock = threading.Lock()
    original_transact_write_items = aws["dynamodb"].transact_write_items

    def _serialized_transact_write_items(*args, **kwargs):
        with write_lock:
            return original_transact_write_items(*args, **kwargs)

    monkeypatch.setattr(
        aws["dynamodb"], "transact_write_items", _serialized_transact_write_items
    )

    kwargs = {
        "cases_repo": repos["cases"],
        "subtasks_repo": repos["subtasks"],
        "dynamodb": aws["dynamodb"],
        "s3": aws["s3"],
        "results_bucket": "test-results-bucket",
    }

    barrier = threading.Barrier(2)

    def _run(result):
        barrier.wait(timeout=5)
        close_subtask(result, **kwargs)

    threads = [
        threading.Thread(target=_run, args=(result_a,)),
        threading.Thread(target=_run, args=(result_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 0  # decremented exactly once, did not end up at -1
    assert case.status == CaseStatus.COMPLETED

    # A single report at the case's deterministic key.
    obj = aws["s3"].get_object(Bucket="test-results-bucket", Key=case.report_key)
    assert obj["Body"].read()


def test_close_subtask_with_nonexistent_subtask_logs_warning_not_debug(
    repos, aws, caplog
) -> None:
    """A `ResultMessage` that references a `subtask_id` that never existed for
    that case is an invalid reference (poison message / worker bug), not a
    routine duplicate: it must be discarded without breaking anything but
    logging at WARNING level, not debug."""
    case_id = "ghostcase"
    repos["cases"].put_new(
        CaseItem(case_id=case_id, file_count=1, total=1, pending_count=1)
    )
    result = _completed_result(case_id, f"{case_id}-9999")

    with caplog.at_level(logging.DEBUG, logger="coordinator.barrier.close"):
        close_subtask(
            result,
            cases_repo=repos["cases"],
            subtasks_repo=repos["subtasks"],
            dynamodb=aws["dynamodb"],
            s3=aws["s3"],
            results_bucket="test-results-bucket",
        )

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("inexistente" in r.message for r in warning_records)
    assert not any(
        r.levelno >= logging.WARNING and "inexistente" not in r.message
        for r in caplog.records
    )

    # The write failed: the case's pending_count was not touched.
    case = repos["cases"].get(case_id, consistent=True)
    assert case.pending_count == 1


def test_finalize_is_idempotent_when_already_terminal(repos, aws) -> None:
    case_id = "idemcase"
    _make_case_with_subtasks(repos, case_id, 1)
    subtasks = repos["subtasks"].query_by_case(case_id)
    result = _completed_result(case_id, subtasks[0].subtask_id)

    close_subtask(
        result,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        dynamodb=aws["dynamodb"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )
    # Calling finalize() again on an already terminal case must not blow up or
    # change anything.
    status = finalize(
        case_id,
        cases_repo=repos["cases"],
        subtasks_repo=repos["subtasks"],
        s3=aws["s3"],
        results_bucket="test-results-bucket",
    )
    assert status is None

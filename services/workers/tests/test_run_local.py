"""`workers.ops.run_local`: run one analysis op on a local file, no infra.

The ops are fakes registered in `ANALYSIS_OPERATIONS` with `monkeypatch`.
"""

from __future__ import annotations

import json

import pytest

from shared.analysis import (
    AnalysisOperation,
    AnalysisResult,
    Evidence,
    EvidenceKind,
    Finding,
    FindingCategory,
    Severity,
)
from shared.messages import ErrorCode
from workers.ops import analysis, run_local
from workers.ops.base import OpError, OpOutput

TEXT = AnalysisOperation.TEXT_ANALYZE


def fake_text_analyze(task, src, out_dir, *, timeout_s, on_progress):
    on_progress(50)
    (out_dir / "keywords.txt").write_text("threat\n")
    result = AnalysisResult(
        operation=TEXT,
        keywords=["threat"],
        findings=[
            Finding(
                category=FindingCategory.THREAT,
                severity=Severity.HIGH,
                confidence=0.75,
                label=f"seen {src.read_text().strip()} in {timeout_s:.0f}s",
                evidence=[
                    Evidence(
                        source_key=task.input_key,
                        kind=EvidenceKind.MESSAGE,
                        message_index=0,
                    )
                ],
            )
        ],
    )
    on_progress(100)
    return OpOutput(files=[out_dir / "keywords.txt"], meta=result.to_meta())


def fake_bad_meta(task, src, out_dir, *, timeout_s, on_progress):
    return OpOutput(
        files=[], meta={"analysis": {"operation": "text_analyze", "findings": [{}]}}
    )


def fake_wrong_operation(task, src, out_dir, *, timeout_s, on_progress):
    return OpOutput(files=[], meta=AnalysisResult(operation="image_analyze").to_meta())


def fake_op_error(task, src, out_dir, *, timeout_s, on_progress):
    raise OpError(ErrorCode.CORRUPT_INPUT, "not a chat export")


@pytest.fixture
def chat(tmp_path):
    path = tmp_path / "chat.txt"
    path.write_text("hello\n")
    return path


@pytest.fixture
def register(monkeypatch):
    def _register(op, fn):
        monkeypatch.setitem(analysis.ANALYSIS_OPERATIONS, op, fn)

    return _register


def test_registry_starts_empty_and_is_separate_from_legacy_operations():
    from workers.ops import OPERATIONS

    assert analysis.ANALYSIS_OPERATIONS == {}
    assert analysis.ANALYSIS_OPERATIONS is not OPERATIONS


def test_build_task_is_a_minimal_subtask(chat):
    task = run_local.build_task(TEXT, chat, {"lang": "es"})
    assert task.operation == "text_analyze"
    assert task.media_type == "text"
    assert task.input_key == str(chat)
    assert task.params == {"lang": "es"}
    assert task.output_prefix.endswith(f"{task.subtask_id}/")


def test_run_returns_validated_result_and_measurements(chat, tmp_path, register):
    register(TEXT, fake_text_analyze)
    out = tmp_path / "out"

    report = run_local.run("text_analyze", chat, out_dir=out, timeout_s=7)

    assert isinstance(report.result, AnalysisResult)
    assert report.result.findings[0].label == "seen hello in 7s"
    assert report.result.findings[0].evidence[0].source_key == str(chat)
    assert report.files == [out / "keywords.txt"]
    assert report.wall_time_s >= 0
    # Only type and sign: the absolute value depends on the machine and imports.
    assert isinstance(report.peak_rss_bytes, int) and report.peak_rss_bytes > 0
    assert isinstance(report.peak_rss_children_bytes, int)
    assert report.peak_rss_children_bytes >= 0
    assert report.progress == [50, 100]


def test_main_prints_json_with_result_and_measurements(
    chat, tmp_path, register, capsys
):
    register(TEXT, fake_text_analyze)

    code = run_local.main(["text_analyze", str(chat), "--out", str(tmp_path / "o")])

    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["operation"] == "text_analyze"
    assert printed["result"]["keywords"] == ["threat"]
    assert printed["files"] == [str(tmp_path / "o" / "keywords.txt")]
    assert printed["wall_time_s"] >= 0
    assert isinstance(printed["peak_rss_bytes"], int) and printed["peak_rss_bytes"] > 0
    assert AnalysisResult.model_validate(printed["result"]).operation is TEXT


def test_main_passes_params(chat, tmp_path, register, capsys):
    seen = {}

    def capture(task, src, out_dir, *, timeout_s, on_progress):
        seen.update(task.params)
        return OpOutput(files=[], meta=AnalysisResult(operation=TEXT).to_meta())

    register(TEXT, capture)
    run_local.main(
        ["text_analyze", str(chat), "--param", "lang=es", "--param", "k=a=b"]
    )
    assert seen == {"lang": "es", "k": "a=b"}


def test_unknown_operation_lists_registered_ones(chat, register, capsys):
    register(TEXT, fake_text_analyze)

    with pytest.raises(SystemExit) as exc:
        run_local.main(["nope_analyze", str(chat)])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "nope_analyze" in err and "text_analyze" in err


def test_known_but_unregistered_operation_is_unknown(chat, register):
    register(TEXT, fake_text_analyze)
    with pytest.raises(run_local.UnknownOperation, match="text_analyze"):
        run_local.run("audio_analyze", chat)


def test_empty_registry_says_so(chat):
    with pytest.raises(
        run_local.UnknownOperation, match="no analysis operations registered"
    ):
        run_local.run("text_analyze", chat)


def test_missing_file_fails_before_running(tmp_path, register):
    register(TEXT, fake_text_analyze)
    with pytest.raises(FileNotFoundError):
        run_local.run("text_analyze", tmp_path / "missing.txt")


def test_invalid_meta_is_reported(chat, register, capsys):
    register(TEXT, fake_bad_meta)
    assert run_local.main(["text_analyze", str(chat)]) == 1
    assert "AnalysisResult" in capsys.readouterr().err


def test_result_for_another_operation_is_rejected(chat, register):
    register(TEXT, fake_wrong_operation)
    with pytest.raises(run_local.ContractViolation, match="image_analyze"):
        run_local.run("text_analyze", chat)


def test_op_error_is_reported_with_its_code(chat, register, capsys):
    register(TEXT, fake_op_error)
    assert run_local.main(["text_analyze", str(chat)]) == 1
    err = capsys.readouterr().err
    assert "corrupt_input" in err and "not a chat export" in err


def test_main_reports_operation_mismatch_as_contract_violation(chat, register, capsys):
    register(TEXT, fake_wrong_operation)
    assert run_local.main(["text_analyze", str(chat)]) == 1
    err = capsys.readouterr().err
    assert "contract violation" in err and "image_analyze" in err


def test_meta_without_analysis_key_is_a_contract_violation(chat, register):
    register(TEXT, lambda task, src, out_dir, **_: OpOutput(files=[], meta={"x": 1}))
    with pytest.raises(run_local.ContractViolation, match="analysis"):
        run_local.run("text_analyze", chat)

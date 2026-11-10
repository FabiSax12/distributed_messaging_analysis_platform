"""`dataset/generators/validate_manifests.py` against small datasets in tmp_path.

Run with: uv run --group dataset pytest dataset/tests -q
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "generators" / "validate_manifests.py"
_spec = importlib.util.spec_from_file_location("validate_manifests", SCRIPT)
vm = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vm  # dataclasses look the module up while it executes
_spec.loader.exec_module(vm)

CHAT = b"01/03/24, 21:15 - Test Sender A: hello\n"
SHA_CHAT = hashlib.sha256(CHAT).hexdigest()
SHA_X = "b" * 64

MANIFEST = {
    "case_id": "case-001",
    "title": "Synthetic heterogeneous case",
    "priority": "high",
    "heterogeneous": True,
    "sources": [
        {
            "path": "chat/whatsapp.txt",
            "content_type": "text",
            "sha256": SHA_CHAT,
            "size_bytes": len(CHAT),
            "chat_format": "whatsapp",
        },
        {
            "path": "media/IMG-0001.jpg",
            "content_type": "image",
            "sha256": SHA_X,
            "size_bytes": 10,
        },
        {
            "path": "media/PTT-0001.ogg",
            "content_type": "audio",
            "sha256": SHA_X,
            "size_bytes": 10,
        },
        {
            "path": "media/VID-0001.mp4",
            "content_type": "video",
            "sha256": SHA_X,
            "size_bytes": 250 * 1024 * 1024,
        },
    ],
}

GROUND_TRUTH = {
    "case_id": "case-001",
    "labeled_by": "Test Labeler",
    "sources": [
        {
            "path": "chat/whatsapp.txt",
            "findings": [
                {
                    "category": "threat",
                    "severity": "high",
                    "kind": "message",
                    "message_index": 0,
                }
            ],
        },
        {
            "path": "media/IMG-0001.jpg",
            "findings": [
                {
                    "category": "sensitive_image",
                    "severity": "medium",
                    "kind": "image",
                    "bbox": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5},
                }
            ],
        },
        {
            "path": "media/PTT-0001.ogg",
            "findings": [
                {
                    "category": "offensive_language",
                    "severity": "low",
                    "kind": "audio_segment",
                    "audio_start_s": 1.0,
                    "audio_end_s": 2.5,
                }
            ],
        },
        {"path": "media/VID-0001.mp4", "findings": []},
    ],
}


def _write(
    root: Path, manifest=None, ground_truth=None, *, chat: bytes | None = CHAT
) -> Path:
    manifest = MANIFEST if manifest is None else manifest
    case_dir = root / "cases" / manifest["case_id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "manifest.json").write_text(json.dumps(manifest))
    if chat is not None:
        (case_dir / "chat").mkdir(exist_ok=True)
        (case_dir / "chat" / "whatsapp.txt").write_bytes(chat)
    if ground_truth is not False:
        gt = GROUND_TRUTH if ground_truth is None else ground_truth
        (root / "ground_truth").mkdir(exist_ok=True)
        (root / "ground_truth" / f"{gt['case_id']}.json").write_text(json.dumps(gt))
    return root


def _errors(root: Path, **kwargs) -> list[str]:
    return vm.validate_dataset(root, schema_dir=vm.DEFAULT_SCHEMA_DIR, **kwargs).errors


def test_valid_dataset_has_no_errors_and_a_summary(tmp_path):
    report = vm.validate_dataset(_write(tmp_path), schema_dir=vm.DEFAULT_SCHEMA_DIR)
    assert report.errors == []
    assert report.summary["cases"] == 1
    assert report.summary["heterogeneous_cases"] == 1
    assert report.summary["heavy_cases"] == 1
    assert report.summary["sources_by_content_type"] == {
        "audio": 1,
        "image": 1,
        "text": 1,
        "video": 1,
    }
    assert report.summary["labeled_sources"] == 4


def test_empty_dataset_is_valid(tmp_path):
    assert _errors(tmp_path) == []


def test_schema_violation_is_reported_with_its_file(tmp_path):
    manifest = copy.deepcopy(MANIFEST)
    manifest["priority"] = "urgent"
    errors = _errors(_write(tmp_path, manifest, ground_truth=False))
    assert len(errors) == 1
    assert "case-001/manifest.json" in errors[0] and "urgent" in errors[0]


def test_case_id_must_match_directory(tmp_path):
    _write(tmp_path, ground_truth=False)
    (tmp_path / "cases" / "case-001").rename(tmp_path / "cases" / "case-999")
    assert any("directory" in e for e in _errors(tmp_path))


def test_heterogeneous_flag_must_match_sources(tmp_path):
    manifest = copy.deepcopy(MANIFEST)
    manifest["sources"] = manifest["sources"][:2]  # text + image only
    assert any(
        "heterogeneous" in e
        for e in _errors(_write(tmp_path, manifest, ground_truth=False))
    )


def test_duplicate_source_paths_are_rejected(tmp_path):
    manifest = copy.deepcopy(MANIFEST)
    manifest["sources"].append(copy.deepcopy(manifest["sources"][1]))
    assert any(
        "duplicate" in e
        for e in _errors(_write(tmp_path, manifest, ground_truth=False))
    )


@pytest.mark.parametrize("path", ["/etc/passwd", "../other/x.jpg", "a/../../x.jpg"])
def test_source_path_cannot_escape_the_case(tmp_path, path):
    manifest = copy.deepcopy(MANIFEST)
    manifest["sources"][1]["path"] = path
    assert _errors(_write(tmp_path, manifest, ground_truth=False))


def test_chat_format_only_on_text_sources(tmp_path):
    manifest = copy.deepcopy(MANIFEST)
    manifest["sources"][1]["chat_format"] = "telegram"
    assert _errors(_write(tmp_path, manifest, ground_truth=False))


def test_verify_files_checks_hash_and_size_of_local_files(tmp_path):
    root = _write(tmp_path, chat=b"tampered\n", ground_truth=False)
    assert _errors(root) == []  # without --verify-files, local content is not read
    errors = _errors(root, verify_files=True)
    assert any("sha256" in e for e in errors) and any("size" in e for e in errors)


def test_verify_files_ignores_media_that_lives_only_in_s3(tmp_path):
    assert _errors(_write(tmp_path), verify_files=True) == []


def test_ground_truth_for_unknown_case(tmp_path):
    gt = copy.deepcopy(GROUND_TRUTH)
    gt["case_id"] = "case-404"
    assert any("no manifest" in e for e in _errors(_write(tmp_path, ground_truth=gt)))


def test_ground_truth_path_must_be_in_the_manifest(tmp_path):
    gt = copy.deepcopy(GROUND_TRUTH)
    gt["sources"][0]["path"] = "chat/missing.txt"
    assert any(
        "chat/missing.txt" in e for e in _errors(_write(tmp_path, ground_truth=gt))
    )


def test_ground_truth_kind_must_fit_the_content_type(tmp_path):
    gt = copy.deepcopy(GROUND_TRUTH)
    gt["sources"][1]["findings"][0] = {
        "category": "threat",
        "severity": "high",
        "kind": "message",
        "message_index": 2,
    }
    assert any("kind" in e for e in _errors(_write(tmp_path, ground_truth=gt)))


def test_ground_truth_message_finding_needs_message_index(tmp_path):
    gt = copy.deepcopy(GROUND_TRUTH)
    del gt["sources"][0]["findings"][0]["message_index"]
    assert _errors(_write(tmp_path, ground_truth=gt))


def test_ground_truth_audio_segment_must_be_ordered(tmp_path):
    gt = copy.deepcopy(GROUND_TRUTH)
    gt["sources"][2]["findings"][0]["audio_end_s"] = 0.5
    assert any("audio_end_s" in e for e in _errors(_write(tmp_path, ground_truth=gt)))


def test_ground_truth_bbox_must_stay_inside_the_image(tmp_path):
    gt = copy.deepcopy(GROUND_TRUTH)
    gt["sources"][1]["findings"][0]["bbox"] = {"x": 0.8, "y": 0.0, "w": 0.5, "h": 0.5}
    assert any("bbox" in e for e in _errors(_write(tmp_path, ground_truth=gt)))


def test_main_exit_codes(tmp_path, capsys):
    assert vm.main(["--root", str(_write(tmp_path))]) == 0
    assert "1 case" in capsys.readouterr().out

    bad = copy.deepcopy(MANIFEST)
    bad["title"] = ""
    _write(tmp_path, bad, ground_truth=False)
    assert vm.main(["--root", str(tmp_path)]) == 1
    assert "title" in capsys.readouterr().err


def test_invalid_json_is_an_error_not_a_crash(tmp_path):
    case_dir = tmp_path / "cases" / "case-001"
    case_dir.mkdir(parents=True)
    (case_dir / "manifest.json").write_text("{not json")
    assert any("invalid JSON" in e for e in _errors(tmp_path))


@pytest.mark.parametrize(
    ("index", "extra"),
    [
        (0, {"audio_start_s": 1.0, "audio_end_s": 2.0}),  # message + audio times
        (0, {"bbox": {"x": 0, "y": 0, "w": 1, "h": 1}}),  # message + bbox
        (1, {"message_index": 4}),  # image + message_index
        (2, {"bbox": {"x": 0, "y": 0, "w": 1, "h": 1}}),  # audio segment + bbox
    ],
)
def test_ground_truth_rejects_locators_of_another_kind(tmp_path, index, extra):
    """Same rules as `Evidence` in shared/analysis.py."""
    gt = copy.deepcopy(GROUND_TRUTH)
    gt["sources"][index]["findings"][0].update(extra)
    assert _errors(_write(tmp_path, ground_truth=gt))


def test_ground_truth_document_finding_has_no_locators(tmp_path):
    manifest = copy.deepcopy(MANIFEST)
    manifest["sources"].append(
        {
            "path": "docs/a.pdf",
            "content_type": "document",
            "sha256": SHA_X,
            "size_bytes": 1,
        }
    )
    gt = copy.deepcopy(GROUND_TRUTH)
    finding = {"category": "other", "severity": "low", "kind": "document"}
    gt["sources"].append({"path": "docs/a.pdf", "findings": [finding]})
    assert _errors(_write(tmp_path, manifest, gt)) == []

    finding["message_index"] = 0
    assert _errors(_write(tmp_path, manifest, gt))


def test_ground_truth_bbox_uses_the_shared_tolerance(tmp_path):
    gt = copy.deepcopy(GROUND_TRUTH)
    bbox = gt["sources"][1]["findings"][0]["bbox"]
    bbox.update(x=0.5, w=0.5 + vm.BBOX_TOLERANCE / 2)
    assert _errors(_write(tmp_path, ground_truth=gt)) == []
    bbox.update(w=0.5 + vm.BBOX_TOLERANCE * 10)
    assert any("bbox" in e for e in _errors(_write(tmp_path, ground_truth=gt)))


def test_bbox_tolerance_matches_the_analysis_contract():
    text = (
        Path(__file__).resolve().parents[2] / "services/shared/shared/analysis.py"
    ).read_text()
    match = re.search(r"^BBOX_TOLERANCE = ([0-9.e+-]+)$", text, re.MULTILINE)
    assert match and float(match.group(1)) == vm.BBOX_TOLERANCE


def test_ground_truth_path_uses_the_manifest_safe_path_pattern():
    manifest_schema = json.loads(
        (vm.DEFAULT_SCHEMA_DIR / "case_manifest.schema.json").read_text()
    )
    gt_schema = json.loads(
        (vm.DEFAULT_SCHEMA_DIR / "ground_truth.schema.json").read_text()
    )
    assert (
        gt_schema["$defs"]["labeled_source"]["properties"]["path"]["pattern"]
        == manifest_schema["$defs"]["source"]["properties"]["path"]["pattern"]
    )


@pytest.mark.parametrize("path", ["/etc/passwd", "../x.jpg", "a/../../x.jpg"])
def test_ground_truth_path_cannot_escape_the_case(tmp_path, path):
    gt = copy.deepcopy(GROUND_TRUTH)
    gt["sources"][0]["path"] = path
    assert any(
        "pattern" in e or "does not match" in e
        for e in _errors(_write(tmp_path, ground_truth=gt))
    )

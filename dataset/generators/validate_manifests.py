"""Validate the dataset contract: case manifests and ground-truth labels.

    uv run --group dataset python dataset/generators/validate_manifests.py [--root dataset]
        [--verify-files]

Checks every ``cases/<case_id>/manifest.json`` and ``ground_truth/<case_id>.json``
against the JSON Schemas in ``dataset/schema/`` plus the rules a schema cannot
express (case_id = directory name, unique paths, the heterogeneous flag,
ground-truth paths and locators consistent with the manifest). With
``--verify-files``, sources present locally are hashed and their size checked;
media that only lives in S3 is skipped.

Prints a summary to compare against the minimums in ``docs/dataset.md``.
Exit code 0 if valid, 1 if any error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

DATASET_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_DIR = DATASET_ROOT / "schema"
HEAVY_CASE_BYTES = 200 * 1024 * 1024
# Slack on x + w <= 1 and y + h <= 1 for float rounding. Must equal
# BBOX_TOLERANCE in services/shared/shared/analysis.py (a test checks it).
BBOX_TOLERANCE = 1e-6
HETEROGENEOUS_REQUIRED = {"text", "image", "audio"}
HETEROGENEOUS_ANY_OF = {"video", "document"}

# Evidence kind -> content types it can point into.
KIND_CONTENT_TYPES: dict[str, set[str]] = {
    "message": {"text"},
    "image": {"image", "video"},  # a video frame counts as an image region
    "audio_segment": {"audio", "video"},
    "document": {"document"},
}

_HASH_BLOCK = 1024 * 1024


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _load_json(path: Path, errors: list[str]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return None


def _schema_errors(validator: Draft202012Validator, data: Any, path: Path) -> list[str]:
    found = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    return [
        f"{path}: {'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in found
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(_HASH_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def _is_heterogeneous(content_types: set[str]) -> bool:
    return HETEROGENEOUS_REQUIRED <= content_types and bool(
        HETEROGENEOUS_ANY_OF & content_types
    )


def _check_manifest(
    manifest: dict[str, Any], path: Path, verify_files: bool, errors: list[str]
) -> None:
    case_dir = path.parent
    if manifest["case_id"] != case_dir.name:
        errors.append(
            f"{path}: case_id {manifest['case_id']!r} != directory name {case_dir.name!r}"
        )

    paths = [s["path"] for s in manifest["sources"]]
    for dup in sorted(p for p, n in Counter(paths).items() if n > 1):
        errors.append(f"{path}: duplicate source path {dup!r}")

    types = {s["content_type"] for s in manifest["sources"]}
    expected = _is_heterogeneous(types)
    if manifest["heterogeneous"] != expected:
        errors.append(
            f"{path}: heterogeneous is {manifest['heterogeneous']} but sources "
            f"{sorted(types)} make it {expected} (needs text, image, audio and video or document)"
        )

    if not verify_files:
        return
    for source in manifest["sources"]:
        local = case_dir / source["path"]
        if not local.is_file():
            continue  # media lives in S3, not in git
        size = local.stat().st_size
        if size != source["size_bytes"]:
            errors.append(
                f"{path}: {source['path']}: size {size} != size_bytes {source['size_bytes']}"
            )
        if _sha256(local) != source["sha256"]:
            errors.append(f"{path}: {source['path']}: sha256 does not match the file")


def _check_ground_truth(
    gt: dict[str, Any],
    path: Path,
    manifests: dict[str, dict[str, Any]],
    errors: list[str],
) -> int:
    """Returns how many sources it labels."""
    if gt["case_id"] != path.stem:
        errors.append(f"{path}: case_id {gt['case_id']!r} != file name {path.stem!r}")
    manifest = manifests.get(gt["case_id"])
    if manifest is None:
        errors.append(f"{path}: no manifest for case {gt['case_id']!r}")
        return len(gt["sources"])

    content_types = {s["path"]: s["content_type"] for s in manifest["sources"]}
    for source in gt["sources"]:
        where = f"{path}: {source['path']}"
        content_type = content_types.get(source["path"])
        if content_type is None:
            errors.append(f"{where}: not a source of the case manifest")
            continue
        for i, finding in enumerate(source["findings"]):
            kind = finding["kind"]
            if content_type not in KIND_CONTENT_TYPES[kind]:
                errors.append(
                    f"{where}: findings/{i}: kind {kind!r} cannot point into {content_type}"
                )
            start, end = finding.get("audio_start_s"), finding.get("audio_end_s")
            if start is not None and end is not None and end < start:
                errors.append(f"{where}: findings/{i}: audio_end_s < audio_start_s")
            bbox = finding.get("bbox")
            if bbox and (
                bbox["x"] + bbox["w"] > 1 + BBOX_TOLERANCE
                or bbox["y"] + bbox["h"] > 1 + BBOX_TOLERANCE
            ):
                errors.append(f"{where}: findings/{i}: bbox goes outside the image")
    return len(gt["sources"])


def validate_dataset(
    root: Path, *, schema_dir: Path = DEFAULT_SCHEMA_DIR, verify_files: bool = False
) -> Report:
    report = Report()
    errors = report.errors
    manifest_schema = Draft202012Validator(
        json.loads(
            (schema_dir / "case_manifest.schema.json").read_text(encoding="utf-8")
        )
    )
    gt_schema = Draft202012Validator(
        json.loads(
            (schema_dir / "ground_truth.schema.json").read_text(encoding="utf-8")
        )
    )

    manifests: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "cases").glob("*/manifest.json")):
        data = _load_json(path, errors)
        if data is None:
            continue
        schema_errors = _schema_errors(manifest_schema, data, path)
        if schema_errors:
            errors.extend(schema_errors)
            continue
        _check_manifest(data, path, verify_files, errors)
        manifests[data["case_id"]] = data

    labeled = 0
    for path in sorted((root / "ground_truth").glob("*.json")):
        data = _load_json(path, errors)
        if data is None:
            continue
        schema_errors = _schema_errors(gt_schema, data, path)
        if schema_errors:
            errors.extend(schema_errors)
            continue
        labeled += _check_ground_truth(data, path, manifests, errors)

    sources = [s for m in manifests.values() for s in m["sources"]]
    report.summary = {
        "cases": len(manifests),
        "heterogeneous_cases": sum(m["heterogeneous"] for m in manifests.values()),
        "heavy_cases": sum(
            any(s["size_bytes"] >= HEAVY_CASE_BYTES for s in m["sources"])
            for m in manifests.values()
        ),
        "sources_by_content_type": dict(
            sorted(Counter(s["content_type"] for s in sources).items())
        ),
        "total_bytes": sum(s["size_bytes"] for s in sources),
        "labeled_sources": labeled,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=DATASET_ROOT, help="dataset directory"
    )
    parser.add_argument("--schema-dir", type=Path, default=DEFAULT_SCHEMA_DIR)
    parser.add_argument(
        "--verify-files",
        action="store_true",
        help="hash local sources and check their size",
    )
    args = parser.parse_args(argv)

    report = validate_dataset(
        args.root, schema_dir=args.schema_dir, verify_files=args.verify_files
    )
    for error in report.errors:
        print(error, file=sys.stderr)
    print(
        f"Validated {report.summary['cases']} case(s) with {len(report.errors)} error(s)."
    )
    print(json.dumps(report.summary, indent=2))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())

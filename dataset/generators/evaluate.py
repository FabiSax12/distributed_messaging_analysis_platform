"""Precision/recall of the analysis operations against the ground truth.

    uv run --all-packages --all-extras --group dataset python dataset/generators/evaluate.py \
        [--cases case-001 ...] [--ops text image audio] [--out dataset/build/eval]

Runs the production operations in-process through ``workers.ops.run_local.run``
(the same functions the worker pools dispatch; no broker or S3) on every
labeled source present locally, caches each prediction by source SHA-256 and
compares it with ``dataset/ground_truth``. Only sources listed in a ground
truth file are scored (an empty findings list is a known negative).

Matching rule (one-to-one, per category, within one source):

- ``message``: same category and same ``message_index``.
- ``audio_segment``: same category and the predicted segment overlaps the
  labeled one, with ``AUDIO_TOLERANCE_S`` slack on each side (Whisper merges
  or splits sentences, so exact boundaries are not expected).
- ``image``: same category and box IoU >= ``IOU_THRESHOLD``; a labeled image
  finding without bbox matches any predicted finding on that image. An
  image-level score (any finding vs any label) is reported too.

Several predicted findings of one category on the same message collapse to
one. A source whose operation fails counts its labels as false negatives and
is listed in ``errors``. Writes ``metrics.json``, ``metrics.md`` and
``predictions.jsonl`` to ``--out``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

DATASET_ROOT = Path(__file__).resolve().parents[1]
AUDIO_TOLERANCE_S = 0.5
IOU_THRESHOLD = 0.5
OPERATION = {"text": "text_analyze", "image": "image_analyze", "audio": "audio_analyze", "video": "audio_analyze"}
POOL = {"text": "text", "image": "image", "audio": "audio", "video": "audio"}


def iou(a: dict, b: dict) -> float:
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def predicted_items(result: dict) -> list[dict]:
    """One item per (finding, evidence): category + locator + confidence."""
    items = []
    for finding in result.get("findings", []):
        for ev in finding["evidence"]:
            items.append({"category": finding["category"], "kind": ev["kind"], "confidence": finding["confidence"],
                          **{k: ev[k] for k in ("message_index", "audio_start_s", "audio_end_s", "bbox") if k in ev}})
    return items


def match(expected: list[dict], predicted: list[dict]) -> tuple[int, int, int, list[float]]:
    """(tp, fp, fn, ious) for one category of one source."""
    if not expected and not predicted:
        return 0, 0, 0, []
    kind = (expected or predicted)[0]["kind"]
    if kind == "message":
        exp = {e["message_index"] for e in expected}
        pred = {p["message_index"] for p in predicted}
        return len(exp & pred), len(pred - exp), len(exp - pred), []
    used: set[int] = set()
    tp, ious = 0, []
    order = sorted(range(len(predicted)), key=lambda i: -predicted[i]["confidence"])
    for e in expected:
        best, best_score = None, 0.0
        for i in order:
            if i in used:
                continue
            p = predicted[i]
            if kind == "audio_segment":
                ok = p["audio_start_s"] < e["audio_end_s"] + AUDIO_TOLERANCE_S and p["audio_end_s"] > e["audio_start_s"] - AUDIO_TOLERANCE_S
                score = 1.0 if ok else 0.0
            elif "bbox" not in e or "bbox" not in p:
                score = 1.0
            else:
                score = iou(e["bbox"], p["bbox"])
                ok = score >= IOU_THRESHOLD
                score = score if ok else 0.0
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            used.add(best)
            tp += 1
            if kind == "image" and "bbox" in e and "bbox" in predicted[best]:
                ious.append(best_score)
    return tp, len(predicted) - len(used), len(expected) - tp, ious


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


def run_op(content_type: str, path: Path, source: dict) -> tuple[dict | None, str | None, float]:
    from workers.ops.base import OpError
    from workers.ops.run_local import run

    params = {k: source[k] for k in ("chat_format", "timezone") if k in source}
    started = time.perf_counter()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            report = run(OPERATION[content_type], path, params=params, timeout_s=1800)
        return report.to_json()["result"], None, time.perf_counter() - started
    except OpError as exc:
        return None, f"{exc.code}: {exc.message}", time.perf_counter() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DATASET_ROOT)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--ops", nargs="*", default=["text", "image", "audio"], help="pools to evaluate")
    parser.add_argument("--out", type=Path, default=DATASET_ROOT / "build" / "eval")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    cache_path = args.out / "predictions.jsonl"
    cache: dict[str, dict] = {}
    if cache_path.is_file():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cache[row["key"]] = row

    totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])  # (modality, category)
    image_level = [0, 0, 0, 0]  # tp, fp, fn, tn
    ious: list[float] = []
    errors, scored = [], defaultdict(int)
    gt_files = sorted((args.root / "ground_truth").glob("*.json"))
    for gt_path in gt_files:
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        if args.cases and gt["case_id"] not in args.cases:
            continue
        case_dir = args.root / "cases" / gt["case_id"]
        manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
        by_path = {s["path"]: s for s in manifest["sources"]}
        for labeled in gt["sources"]:
            source = by_path[labeled["path"]]
            content_type = source["content_type"]
            if content_type not in OPERATION or POOL[content_type] not in args.ops:
                continue
            path = case_dir / source["path"]
            if not path.is_file():
                continue
            key = f"{source['sha256']}:{OPERATION[content_type]}"
            if key not in cache:
                result, error, seconds = run_op(content_type, path, source)
                cache[key] = {"key": key, "case_id": gt["case_id"], "path": source["path"], "content_type": content_type,
                              "seconds": round(seconds, 3), "error": error, "result": result}
                with cache_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(cache[key], ensure_ascii=False) + "\n")
                print(f"{gt['case_id']} {source['path']}: {seconds:.1f}s {error or ''}", file=sys.stderr)
            row = cache[key]
            modality = "video" if content_type == "video" else content_type
            scored[modality] += 1
            predicted = predicted_items(row["result"]) if row["result"] else []
            if row["error"]:
                errors.append({"case_id": gt["case_id"], "path": source["path"], "error": row["error"]})
            categories = {f["category"] for f in labeled["findings"]} | {p["category"] for p in predicted}
            for category in categories:
                exp = [f for f in labeled["findings"] if f["category"] == category]
                pred = [p for p in predicted if p["category"] == category]
                tp, fp, fn, found_ious = match(exp, pred)
                t = totals[(modality, category)]
                t[0] += tp
                t[1] += fp
                t[2] += fn
                ious.extend(found_ious)
            if content_type == "image":
                truth, guess = bool(labeled["findings"]), bool(predicted)
                image_level[0 if truth and guess else 1 if guess else 2 if truth else 3] += 1

    per_category: dict[str, dict] = {}
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for (modality, category), (tp, fp, fn) in sorted(totals.items()):
        per_category[f"{modality}/{category}"] = _prf(tp, fp, fn)
        for i, v in enumerate((tp, fp, fn)):
            by_cat[category][i] += v
    metrics = {
        "matching": {"audio_tolerance_s": AUDIO_TOLERANCE_S, "iou_threshold": IOU_THRESHOLD},
        "scored_sources": dict(scored),
        "per_modality_category": per_category,
        "per_category": {c: _prf(*v) for c, v in sorted(by_cat.items())},
        "image_level": {**_prf(*image_level[:3]), "tn": image_level[3]},
        "mean_iou_of_matched_boxes": sum(ious) / len(ious) if ious else None,
        "errors": errors,
    }
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    def fmt(v):
        return "n/a" if v is None else f"{v:.3f}"

    lines = ["| Modality / category | TP | FP | FN | Precision | Recall | F1 |", "|---|---|---|---|---|---|---|"]
    for name, m in per_category.items():
        lines.append(f"| {name} | {m['tp']} | {m['fp']} | {m['fn']} | {fmt(m['precision'])} | {fmt(m['recall'])} | {fmt(m['f1'])} |")
    for name, m in metrics["per_category"].items():
        lines.append(f"| **all/{name}** | {m['tp']} | {m['fp']} | {m['fn']} | {fmt(m['precision'])} | {fmt(m['recall'])} | {fmt(m['f1'])} |")
    il = metrics["image_level"]
    lines.append(f"| image-level sensitive_image | {il['tp']} | {il['fp']} | {il['fn']} | {fmt(il['precision'])} | {fmt(il['recall'])} | {fmt(il['f1'])} |")
    (args.out / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"scored sources: {dict(scored)}; errors: {len(errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

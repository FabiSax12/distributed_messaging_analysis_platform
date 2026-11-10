"""YOLO COCO object detection; knife/scissors detections require human review."""

import threading
import time
from functools import lru_cache
from pathlib import Path

from shared.analysis import (
    AnalysisOperation,
    AnalysisResult,
    BoundingBox,
    Evidence,
    EvidenceKind,
    Finding,
    FindingCategory,
    ImageLabel,
    ModelInfo,
    Severity,
)
from shared.messages import ErrorCode

from ..base import OpError
from .common import deadline, digest, output, remaining

LOCK = threading.Lock()


@lru_cache(maxsize=1)
def load_model():
    from ultralytics import YOLO

    cache = Path.home() / ".cache" / "madp"
    cache.mkdir(parents=True, exist_ok=True)
    return YOLO(str(cache / "yolov8n.pt"))


def image_analyze(task, src, out_dir, *, timeout_s, on_progress):
    end = deadline(timeout_s)
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise OpError(ErrorCode.PROCESSING_ERROR, "Install workers[image]") from exc
    try:
        with Image.open(src) as image:
            image.verify()
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise OpError(ErrorCode.CORRUPT_INPUT, str(exc)) from exc
    if not LOCK.acquire(timeout=remaining(end)):
        raise OpError(ErrorCode.TIMEOUT, "Waiting for image model")
    try:
        started = time.perf_counter()
        model = load_model()
        load_s = time.perf_counter() - started
        remaining(end)
        started = time.perf_counter()
        result = model.predict(str(src), device="cpu", conf=0.25, verbose=False)[0]
        infer_s = time.perf_counter() - started
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise OpError(ErrorCode.PROCESSING_ERROR, f"YOLO failed: {exc}") from exc
    finally:
        LOCK.release()
    remaining(end)
    labels, findings = {}, []
    source_hash = digest(src)
    for box in result.boxes:
        label = result.names[int(box.cls.item())]
        confidence = float(box.conf.item())
        labels[label] = max(labels.get(label, 0), confidence)
        if label in {"knife", "scissors"}:
            x1, y1, x2, y2 = [min(1, max(0, float(v))) for v in box.xyxyn[0].tolist()]
            findings.append(
                Finding(
                    category=FindingCategory.SENSITIVE_IMAGE,
                    severity=Severity.MEDIUM,
                    confidence=confidence,
                    label=f"{label}: review context",
                    evidence=[
                        Evidence(
                            source_key=task.input_key,
                            kind=EvidenceKind.IMAGE,
                            sha256=source_hash,
                            bbox=BoundingBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1),
                        )
                    ],
                )
            )
    return output(
        AnalysisResult(
            operation=AnalysisOperation.IMAGE_ANALYZE,
            findings=findings,
            image_labels=[ImageLabel(label=k, confidence=v) for k, v in labels.items()],
            model=ModelInfo(name="yolov8n-coco"),
            timings_s={"load_model": load_s, "infer": infer_s},
        ),
        out_dir,
        on_progress,
    )

"""`shared.analysis`: the result contract of the text/image/audio analysis ops."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.analysis import (
    ANALYSIS_META_KEY,
    BBOX_TOLERANCE,
    EXCERPT_MAX_CHARS,
    OPERATION_CONTENT_TYPE,
    AnalysisOperation,
    AnalysisResult,
    BoundingBox,
    CasePriority,
    ContentType,
    Evidence,
    EvidenceKind,
    Finding,
    FindingCategory,
    ImageLabel,
    ModelInfo,
    Sentiment,
    SentimentLabel,
    Severity,
    Transcript,
    TranscriptSegment,
)

SHA = "a" * 64


def _evidence(**overrides) -> Evidence:
    """Valid by default: a message locator unless another kind is requested."""
    fields = {"source_key": "cases/c1/chat.txt", "kind": EvidenceKind.MESSAGE}
    fields.update(overrides)
    if fields["kind"] is EvidenceKind.MESSAGE:
        fields.setdefault("message_index", 0)
    return Evidence(**fields)


def _finding(**overrides) -> Finding:
    fields = {
        "category": FindingCategory.THREAT,
        "severity": Severity.HIGH,
        "confidence": 0.9,
        "label": "explicit threat",
        "evidence": [_evidence(message_index=3)],
    }
    fields.update(overrides)
    return Finding(**fields)


def _full_audio_result() -> AnalysisResult:
    return AnalysisResult(
        operation=AnalysisOperation.AUDIO_ANALYZE,
        findings=[
            _finding(
                evidence=[
                    _evidence(
                        source_key="cases/c1/voice-01.ogg",
                        kind=EvidenceKind.AUDIO_SEGMENT,
                        sender="Test Sender A",
                        timestamp=datetime(2024, 3, 1, 21, 15, tzinfo=UTC),
                        excerpt="you will regret it",
                        audio_start_s=1.5,
                        audio_end_s=3.25,
                        sha256=SHA,
                    )
                ]
            )
        ],
        keywords=["regret", "tomorrow"],
        topics=["personal conflict"],
        sentiment=Sentiment(score=-0.8, label=SentimentLabel.NEGATIVE),
        transcript=Transcript(
            text="you will regret it tomorrow",
            language="en",
            segments=[
                TranscriptSegment(start_s=1.5, end_s=3.25, text="you will regret it")
            ],
        ),
        model=ModelInfo(name="whisper-small", version="20231117"),
        timings_s={"transcribe": 2.5, "text_analyze": 0.125},
        peak_rss_bytes=512 * 1024 * 1024,
    )


# ---- Enums ------------------------------------------------------------------ #


def test_enum_values_are_the_wire_values():
    assert [c.value for c in ContentType] == [
        "text",
        "image",
        "audio",
        "video",
        "document",
    ]
    assert [o.value for o in AnalysisOperation] == [
        "text_analyze",
        "image_analyze",
        "audio_analyze",
    ]
    assert [p.value for p in CasePriority] == ["low", "medium", "high", "critical"]
    assert [s.value for s in Severity] == ["low", "medium", "high", "critical"]
    assert {c.value for c in FindingCategory} >= {
        "threat",
        "violence",
        "offensive_language",
        "illicit_activity",
        "sensitive_image",
        "other",
    }
    assert [k.value for k in EvidenceKind] == [
        "message",
        "image",
        "audio_segment",
        "document",
    ]


def test_every_operation_maps_to_its_content_type():
    assert OPERATION_CONTENT_TYPE == {
        AnalysisOperation.TEXT_ANALYZE: ContentType.TEXT,
        AnalysisOperation.IMAGE_ANALYZE: ContentType.IMAGE,
        AnalysisOperation.AUDIO_ANALYZE: ContentType.AUDIO,
    }


# ---- Evidence --------------------------------------------------------------- #


def test_evidence_document_only_needs_source_and_kind():
    ev = Evidence(source_key="cases/c1/report.pdf", kind=EvidenceKind.DOCUMENT)
    assert ev.message_index is None and ev.bbox is None and ev.sha256 is None


def test_evidence_image_without_bbox_means_whole_image():
    assert Evidence(source_key="cases/c1/a.jpg", kind=EvidenceKind.IMAGE).bbox is None


def test_message_evidence_requires_message_index():
    with pytest.raises(ValidationError, match="message_index"):
        Evidence(source_key="cases/c1/chat.txt", kind=EvidenceKind.MESSAGE)


def test_audio_segment_evidence_requires_both_times():
    with pytest.raises(ValidationError, match="audio_start_s"):
        Evidence(source_key="cases/c1/v.ogg", kind=EvidenceKind.AUDIO_SEGMENT)


@pytest.mark.parametrize(
    ("kind", "locator"),
    [
        (EvidenceKind.MESSAGE, {"audio_start_s": 1.0, "audio_end_s": 2.0}),
        (EvidenceKind.MESSAGE, {"bbox": {"x": 0, "y": 0, "w": 1, "h": 1}}),
        (EvidenceKind.AUDIO_SEGMENT, {"message_index": 1}),
        (EvidenceKind.AUDIO_SEGMENT, {"bbox": {"x": 0, "y": 0, "w": 1, "h": 1}}),
        (EvidenceKind.IMAGE, {"message_index": 1}),
        (EvidenceKind.IMAGE, {"audio_start_s": 1.0, "audio_end_s": 2.0}),
        (EvidenceKind.DOCUMENT, {"message_index": 1}),
        (EvidenceKind.DOCUMENT, {"audio_start_s": 1.0, "audio_end_s": 2.0}),
        (EvidenceKind.DOCUMENT, {"bbox": {"x": 0, "y": 0, "w": 1, "h": 1}}),
    ],
)
def test_evidence_rejects_locators_of_another_kind(kind, locator):
    """Same rules as dataset/schema/ground_truth.schema.json."""
    required = {
        EvidenceKind.MESSAGE: {"message_index": 0},
        EvidenceKind.AUDIO_SEGMENT: {"audio_start_s": 0.0, "audio_end_s": 1.0},
    }.get(kind, {})
    with pytest.raises(ValidationError, match=kind.value):
        Evidence(source_key="k", kind=kind, **{**required, **locator})


def test_bbox_tolerates_float_rounding_at_the_edge():
    assert BoundingBox(x=0.5, y=0.0, w=0.5 + BBOX_TOLERANCE / 2, h=1.0).w > 0.5
    with pytest.raises(ValidationError):
        BoundingBox(x=0.5, y=0.0, w=0.5 + BBOX_TOLERANCE * 10, h=1.0)


def test_evidence_rejects_empty_source_key():
    with pytest.raises(ValidationError):
        _evidence(source_key="")


def test_evidence_rejects_negative_message_index():
    with pytest.raises(ValidationError):
        _evidence(message_index=-1)


def test_evidence_audio_end_before_start_is_rejected():
    with pytest.raises(ValidationError, match="audio_end_s"):
        _evidence(kind=EvidenceKind.AUDIO_SEGMENT, audio_start_s=4.0, audio_end_s=3.0)


def test_evidence_audio_segment_needs_both_ends():
    with pytest.raises(ValidationError, match="audio_start_s"):
        _evidence(kind=EvidenceKind.AUDIO_SEGMENT, audio_start_s=1.0)


def test_evidence_zero_length_audio_segment_is_valid():
    ev = _evidence(kind=EvidenceKind.AUDIO_SEGMENT, audio_start_s=2.0, audio_end_s=2.0)
    assert ev.audio_end_s == ev.audio_start_s


def test_evidence_rejects_negative_audio_time():
    with pytest.raises(ValidationError):
        _evidence(kind=EvidenceKind.AUDIO_SEGMENT, audio_start_s=-1.0, audio_end_s=1.0)


@pytest.mark.parametrize(
    "bbox",
    [
        {"x": -0.1, "y": 0.0, "w": 0.5, "h": 0.5},
        {"x": 0.0, "y": 1.2, "w": 0.1, "h": 0.1},
        {"x": 0.0, "y": 0.0, "w": 1.5, "h": 0.5},
        {"x": 0.8, "y": 0.0, "w": 0.5, "h": 0.5},  # x + w > 1
        {"x": 0.0, "y": 0.6, "w": 0.2, "h": 0.6},  # y + h > 1
    ],
)
def test_evidence_rejects_bbox_outside_unit_square(bbox):
    with pytest.raises(ValidationError):
        _evidence(kind=EvidenceKind.IMAGE, bbox=bbox)


def test_evidence_accepts_full_frame_bbox():
    ev = _evidence(kind=EvidenceKind.IMAGE, bbox={"x": 0, "y": 0, "w": 1, "h": 1})
    assert ev.bbox == BoundingBox(x=0.0, y=0.0, w=1.0, h=1.0)


@pytest.mark.parametrize("sha", ["abc", "A" * 64, "g" * 64])
def test_evidence_rejects_malformed_sha256(sha):
    with pytest.raises(ValidationError):
        _evidence(sha256=sha)


def test_evidence_long_excerpt_is_truncated_to_the_bound():
    ev = _evidence(excerpt="x" * (EXCERPT_MAX_CHARS * 3))
    assert ev.excerpt is not None
    assert len(ev.excerpt) == EXCERPT_MAX_CHARS
    assert ev.excerpt.endswith("…")


def test_evidence_short_excerpt_is_kept_verbatim():
    assert _evidence(excerpt="hello").excerpt == "hello"


# ---- Finding ---------------------------------------------------------------- #


def test_finding_needs_at_least_one_evidence():
    with pytest.raises(ValidationError):
        _finding(evidence=[])


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_finding_confidence_must_be_a_probability(confidence):
    with pytest.raises(ValidationError):
        _finding(confidence=confidence)


def test_finding_rejects_empty_label():
    with pytest.raises(ValidationError):
        _finding(label="")


def test_finding_rejects_unknown_category():
    with pytest.raises(ValidationError):
        _finding(category="gossip")


# ---- Optional result parts -------------------------------------------------- #


@pytest.mark.parametrize("score", [-1.5, 1.5])
def test_sentiment_score_is_bounded(score):
    with pytest.raises(ValidationError):
        Sentiment(score=score, label=SentimentLabel.NEUTRAL)


def test_transcript_segment_end_before_start_is_rejected():
    with pytest.raises(ValidationError):
        TranscriptSegment(start_s=5.0, end_s=4.0, text="hi")


def test_image_label_confidence_is_bounded():
    with pytest.raises(ValidationError):
        ImageLabel(label="weapon", confidence=2.0)


def test_result_rejects_negative_timings_and_rss():
    with pytest.raises(ValidationError):
        AnalysisResult(
            operation=AnalysisOperation.TEXT_ANALYZE, timings_s={"total": -1.0}
        )
    with pytest.raises(ValidationError):
        AnalysisResult(operation=AnalysisOperation.TEXT_ANALYZE, peak_rss_bytes=-1)


def test_result_defaults_to_no_findings():
    result = AnalysisResult(operation=AnalysisOperation.IMAGE_ANALYZE)
    assert (
        result.findings == []
        and result.image_labels == []
        and result.transcript is None
    )


# ---- Meta round-trip -------------------------------------------------------- #


def test_to_meta_nests_under_the_analysis_key():
    meta = AnalysisResult(operation=AnalysisOperation.TEXT_ANALYZE).to_meta()
    assert list(meta) == [ANALYSIS_META_KEY]
    assert meta[ANALYSIS_META_KEY]["operation"] == "text_analyze"


def test_to_meta_is_json_safe_and_round_trips():
    result = _full_audio_result()
    meta = result.to_meta()

    # Plain JSON types only: no datetime, enum or model objects left.
    encoded = json.dumps(meta)
    assert "2024-03-01T21:15:00Z" in encoded

    assert AnalysisResult.from_meta(json.loads(encoded)) == result


def test_to_meta_omits_unset_optional_fields():
    payload = AnalysisResult(operation=AnalysisOperation.TEXT_ANALYZE).to_meta()[
        ANALYSIS_META_KEY
    ]
    assert "sentiment" not in payload and "peak_rss_bytes" not in payload


def test_from_meta_tolerates_other_meta_keys():
    meta = {**_full_audio_result().to_meta(), "duration_ms": 1200}
    assert AnalysisResult.from_meta(meta) == _full_audio_result()


def test_from_meta_survives_floats_stringified_by_the_runner():
    """The legacy runner turns every float in `output_meta` into a str before
    storing it (DynamoDB rejects floats); the contract must still parse."""

    def stringify(value):
        if isinstance(value, float):
            return str(value)
        if isinstance(value, dict):
            return {k: stringify(v) for k, v in value.items()}
        if isinstance(value, list):
            return [stringify(v) for v in value]
        return value

    meta = stringify(_full_audio_result().to_meta())
    assert AnalysisResult.from_meta(meta) == _full_audio_result()


def test_from_meta_without_analysis_key_fails_clearly():
    with pytest.raises(ValueError, match=ANALYSIS_META_KEY):
        AnalysisResult.from_meta({"duration_ms": 10})


def test_from_meta_with_invalid_payload_raises_validation_error():
    meta = {
        ANALYSIS_META_KEY: {"operation": "text_analyze", "findings": [{"label": "x"}]}
    }
    with pytest.raises(ValidationError):
        AnalysisResult.from_meta(meta)


def test_naive_timestamp_is_rejected():
    """Timestamps must be timezone-aware so ordering across exports is unambiguous."""
    with pytest.raises(ValidationError):
        _evidence(timestamp=datetime(2024, 3, 1, 21, 15))


# ---- NaN / infinity --------------------------------------------------------- #

_NON_FINITE = [float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("value", _NON_FINITE)
@pytest.mark.parametrize(
    "build",
    [
        lambda v: _finding(confidence=v),
        lambda v: Sentiment(score=v, label=SentimentLabel.NEUTRAL),
        lambda v: BoundingBox(x=v, y=0, w=0.1, h=0.1),
        lambda v: _evidence(
            kind=EvidenceKind.AUDIO_SEGMENT, audio_start_s=0, audio_end_s=v
        ),
        lambda v: TranscriptSegment(start_s=0, end_s=v, text="x"),
        lambda v: ImageLabel(label="weapon", confidence=v),
        lambda v: AnalysisResult(
            operation=AnalysisOperation.TEXT_ANALYZE, timings_s={"t": v}
        ),
    ],
    ids=[
        "confidence",
        "sentiment",
        "bbox",
        "audio",
        "segment",
        "image_label",
        "timings",
    ],
)
def test_non_finite_floats_are_rejected(build, value):
    with pytest.raises(ValidationError):
        build(value)


def test_to_meta_is_strict_json():
    json.dumps(_full_audio_result().to_meta(), allow_nan=False)


# ---- Schema version --------------------------------------------------------- #


def test_future_schema_version_is_rejected_clearly():
    meta = AnalysisResult(operation=AnalysisOperation.TEXT_ANALYZE).to_meta()
    meta[ANALYSIS_META_KEY]["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version 2"):
        AnalysisResult.from_meta(meta)


def test_schema_version_cannot_be_set_to_another_value():
    with pytest.raises(ValidationError):
        AnalysisResult(operation=AnalysisOperation.TEXT_ANALYZE, schema_version=2)

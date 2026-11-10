"""Analysis contract: what the text, image and audio analysis ops return.

One sub-task analyzes one file:

- ``text_analyze``: a chat export (WhatsApp/Telegram) or plain text ->
  keywords, topics, sentiment and findings (threats, offensive language...).
- ``image_analyze``: classification or detection of sensitive content.
- ``audio_analyze``: speech-to-text, then the same text analysis on the
  transcript; findings point at audio segments.

An op builds an ``AnalysisResult`` and returns ``OpOutput(files, meta=result.to_meta())``;
the coordinator and the report read it back with ``AnalysisResult.from_meta``.
The payload is nested under ``ANALYSIS_META_KEY`` so it can share ``meta`` with
other keys the runner may add.

``shared.routing`` maps file extensions to these content types and operations,
and operations to worker pools.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION: Literal[1] = 1
ANALYSIS_META_KEY = "analysis"
EXCERPT_MAX_CHARS = 500
_ELLIPSIS = "…"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
# Slack allowed on x + w <= 1 and y + h <= 1 for float rounding (a detector
# reporting a box that touches the edge). dataset/generators/validate_manifests.py
# uses the same value for the ground truth; keep them in sync.
BBOX_TOLERANCE = 1e-6


class ContentType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


class AnalysisOperation(StrEnum):
    TEXT_ANALYZE = "text_analyze"
    IMAGE_ANALYZE = "image_analyze"
    AUDIO_ANALYZE = "audio_analyze"  # transcribe, then text analysis on the transcript


# Content type each operation consumes. Video is routed to audio_analyze (the
# audio worker extracts the track); documents have no operation yet.
OPERATION_CONTENT_TYPE: dict[AnalysisOperation, ContentType] = {
    AnalysisOperation.TEXT_ANALYZE: ContentType.TEXT,
    AnalysisOperation.IMAGE_ANALYZE: ContentType.IMAGE,
    AnalysisOperation.AUDIO_ANALYZE: ContentType.AUDIO,
}


class CasePriority(StrEnum):
    """Case priority. ``shared.routing.MESSAGE_PRIORITY`` maps it to the AMQP
    message priority of the work queues (M2 scheduling builds on it)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(StrEnum):
    """Extensible: add new members at the end; never rename a value (it is
    stored in results and in ``dataset/ground_truth``)."""

    THREAT = "threat"
    VIOLENCE = "violence"
    OFFENSIVE_LANGUAGE = "offensive_language"
    ILLICIT_ACTIVITY = "illicit_activity"
    SENSITIVE_IMAGE = "sensitive_image"
    OTHER = "other"


class EvidenceKind(StrEnum):
    MESSAGE = "message"
    IMAGE = "image"
    AUDIO_SEGMENT = "audio_segment"
    DOCUMENT = "document"


class SentimentLabel(StrEnum):
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"


# Every float in the contract is finite: NaN/inf are not valid JSON and break
# comparisons (a NaN confidence passes no threshold and fails none).
UnitFloat = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class _Model(BaseModel):
    # extra="ignore", as in shared.messages: an old reader tolerates new fields.
    model_config = ConfigDict(frozen=True, extra="ignore")


# ---- Evidence --------------------------------------------------------------- #


class BoundingBox(_Model):
    """Normalized to the image size: (0, 0) is the top-left corner, 1 is the
    full width/height."""

    x: UnitFloat
    y: UnitFloat
    w: UnitFloat
    h: UnitFloat

    @model_validator(mode="after")
    def _inside_image(self) -> Self:
        if self.x + self.w > 1 + BBOX_TOLERANCE or self.y + self.h > 1 + BBOX_TOLERANCE:
            raise ValueError("bbox must lie inside the image (x + w <= 1, y + h <= 1)")
        return self


_AUDIO_TIMES = frozenset({"audio_start_s", "audio_end_s"})

# kind -> (required locators, allowed locators). sender, timestamp, excerpt and
# sha256 are allowed on every kind.
_KIND_LOCATORS: dict[EvidenceKind, tuple[frozenset[str], frozenset[str]]] = {
    EvidenceKind.MESSAGE: (frozenset({"message_index"}), frozenset({"message_index"})),
    EvidenceKind.AUDIO_SEGMENT: (_AUDIO_TIMES, _AUDIO_TIMES),
    EvidenceKind.IMAGE: (frozenset(), frozenset({"bbox"})),  # no bbox = whole image
    EvidenceKind.DOCUMENT: (frozenset(), frozenset()),
}


class Evidence(_Model):
    """Where a finding comes from. ``source_key`` is the object key of the
    original file; the optional locators narrow it down to a message, an
    audio segment or an image region."""

    source_key: str = Field(min_length=1)
    kind: EvidenceKind
    message_index: int | None = Field(default=None, ge=0)  # 0-based within the chat
    sender: str | None = Field(default=None, max_length=200)
    timestamp: AwareDatetime | None = None  # parsers must attach a time zone
    excerpt: str | None = Field(default=None, max_length=EXCERPT_MAX_CHARS)
    audio_start_s: NonNegativeFloat | None = None
    audio_end_s: NonNegativeFloat | None = None
    bbox: BoundingBox | None = None
    sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )  # of the source file

    @field_validator("excerpt", mode="before")
    @classmethod
    def _clip_excerpt(cls, value: Any) -> Any:
        if isinstance(value, str) and len(value) > EXCERPT_MAX_CHARS:
            return value[: EXCERPT_MAX_CHARS - len(_ELLIPSIS)] + _ELLIPSIS
        return value

    @model_validator(mode="after")
    def _locators_match_kind(self) -> Self:
        # Same rules as dataset/schema/ground_truth.schema.json: each kind has
        # its required locator and cannot carry the locators of another kind.
        start, end = self.audio_start_s, self.audio_end_s
        present = {
            name
            for name, value in (
                ("message_index", self.message_index),
                ("audio_start_s", start),
                ("audio_end_s", end),
                ("bbox", self.bbox),
            )
            if value is not None
        }
        required, allowed = _KIND_LOCATORS[self.kind]
        missing = required - present
        if missing:
            raise ValueError(f"{self.kind} evidence needs {sorted(missing)}")
        foreign = present - allowed
        if foreign:
            raise ValueError(f"{self.kind} evidence cannot have {sorted(foreign)}")
        if start is not None and end is not None and end < start:
            raise ValueError("audio_end_s must be >= audio_start_s")
        return self


class Finding(_Model):
    category: FindingCategory
    severity: Severity
    confidence: UnitFloat
    label: str = Field(min_length=1, max_length=120)  # short, human readable
    evidence: list[Evidence] = Field(min_length=1)


# ---- Optional result parts -------------------------------------------------- #


class Sentiment(_Model):
    score: float = Field(
        ge=-1, le=1, allow_inf_nan=False
    )  # -1 very negative, 1 very positive
    label: SentimentLabel


class TranscriptSegment(_Model):
    start_s: NonNegativeFloat
    end_s: NonNegativeFloat
    text: str

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_s < self.start_s:
            raise ValueError("end_s must be >= start_s")
        return self


class Transcript(_Model):
    text: str
    language: str | None = None  # e.g. "es", "en"
    segments: list[TranscriptSegment] = Field(default_factory=list)


class ImageLabel(_Model):
    label: str = Field(min_length=1)
    confidence: UnitFloat


class ModelInfo(_Model):
    name: str = Field(min_length=1)  # e.g. "whisper-small", "yolov8n"
    version: str | None = None


# ---- Result ----------------------------------------------------------------- #


class AnalysisResult(_Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    operation: AnalysisOperation
    findings: list[Finding] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    sentiment: Sentiment | None = None
    transcript: Transcript | None = None  # audio_analyze only
    image_labels: list[ImageLabel] = Field(default_factory=list)  # image_analyze only
    model: ModelInfo | None = None
    # M1 measurement: seconds per stage (e.g. {"load_model": 3.1, "infer": 0.4})
    # and the process peak RSS while running the op.
    timings_s: dict[str, NonNegativeFloat] = Field(default_factory=dict)
    peak_rss_bytes: int | None = Field(default=None, ge=0)

    def to_meta(self) -> dict[str, Any]:
        """JSON-safe dict for ``OpOutput.meta`` (datetimes as ISO 8601, enums as
        their values, unset optional fields omitted)."""
        return {ANALYSIS_META_KEY: self.model_dump(mode="json", exclude_none=True)}

    @classmethod
    def from_meta(cls, meta: dict[str, Any]) -> Self:
        """Inverse of ``to_meta``. Ignores other keys in ``meta``. Raises
        ``ValueError`` if the analysis key is missing and ``ValidationError``
        (a ``ValueError`` too) if the payload breaks the contract."""
        if ANALYSIS_META_KEY not in meta:
            raise ValueError(f"meta has no {ANALYSIS_META_KEY!r} key: {sorted(meta)}")
        payload = meta[ANALYSIS_META_KEY]
        version = (
            payload.get("schema_version", SCHEMA_VERSION)
            if isinstance(payload, dict)
            else SCHEMA_VERSION
        )
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported analysis schema_version {version}; "
                f"this reader understands {SCHEMA_VERSION}"
            )
        return cls.model_validate(payload)

"""Deterministic English/Spanish triage; matches are review cues, not verdicts."""

import json
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shared.analysis import (
    AnalysisOperation,
    AnalysisResult,
    Evidence,
    EvidenceKind,
    Finding,
    FindingCategory,
    ModelInfo,
    Sentiment,
    SentimentLabel,
    Severity,
)
from shared.messages import ErrorCode

from ..base import OpError
from .common import deadline, digest, output, remaining


def normalize(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if not unicodedata.combining(c)
    )


RULES = (
    (
        FindingCategory.THREAT,
        Severity.HIGH,
        r"\b(kill you|hurt you|you will regret|te voy a matar|te matare|vas a pagar|te vas a arrepentir)\b",
    ),
    (
        FindingCategory.VIOLENCE,
        Severity.HIGH,
        r"\b(shoot|stab|beating|disparar|apunalar|golpear|golpiza)\b",
    ),
    (
        FindingCategory.OFFENSIVE_LANGUAGE,
        Severity.MEDIUM,
        r"\b(idiot|stupid|asshole|idiota|imbecil|mierda)\b",
    ),
    (
        FindingCategory.ILLICIT_ACTIVITY,
        Severity.HIGH,
        r"\b(sell drugs|drug deal|vender drogas|venta de drogas|extorsion|extortion|lavado de dinero)\b",
    ),
)
STOP = {
    "the",
    "and",
    "for",
    "you",
    "are",
    "this",
    "that",
    "with",
    "from",
    "para",
    "como",
    "una",
    "los",
    "las",
    "del",
    "por",
    "que",
    "con",
    "esta",
    "este",
    "pero",
}


def findings_for(text, evidence):
    value = normalize(text)
    return [
        Finding(
            category=category,
            severity=severity,
            confidence=0.65,
            label=f"Rule match: {category.value}",
            evidence=[evidence],
        )
        for category, severity, pattern in RULES
        if re.search(pattern, value)
    ]


def summaries(text):
    words = re.findall(r"\b[^\W\d_]{3,}\b", normalize(text))
    keywords = [
        word for word, _ in Counter(w for w in words if w not in STOP).most_common(12)
    ]
    positive = sum(
        words.count(w)
        for w in ("gracias", "feliz", "excelente", "happy", "thanks", "great")
    )
    negative = sum(
        words.count(w) for w in ("odio", "matar", "idiota", "hate", "kill", "stupid")
    )
    score = (positive - negative) / max(1, positive + negative)
    label = (
        SentimentLabel.POSITIVE
        if score > 0
        else SentimentLabel.NEGATIVE
        if score < 0
        else SentimentLabel.NEUTRAL
    )
    topics = [
        category.value
        for category, _, pattern in RULES
        if re.search(pattern, normalize(text))
    ]
    return keywords, topics, Sentiment(score=score, label=label)


PREFIX = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4}), (\d{1,2}:\d{2}) - (.*)$")


def messages(src, params):
    try:
        zone = ZoneInfo(str(params.get("timezone", "America/Costa_Rica")))
    except ZoneInfoNotFoundError as exc:
        raise OpError(ErrorCode.UNSUPPORTED_FORMAT, "Unknown timezone") from exc
    fmt = params.get(
        "chat_format", "telegram" if src.suffix.lower() == ".json" else "auto"
    )
    if fmt not in {"auto", "plain", "whatsapp", "telegram"}:
        raise OpError(ErrorCode.UNSUPPORTED_FORMAT, "Unknown chat_format")
    try:
        text = src.read_text(encoding="utf-8-sig")
        if "\x00" in text:
            raise ValueError("Binary data in text export")
        if fmt == "telegram":
            entries = json.loads(text)["messages"]
            if not isinstance(entries, list):
                raise ValueError("messages must be a list")
            result = []
            for item in entries:
                body = item["text"]
                if isinstance(body, list):
                    body = "".join(
                        part if isinstance(part, str) else part["text"] for part in body
                    )
                if not isinstance(body, str):
                    raise TypeError("text must be string or entities")
                stamp = datetime.fromisoformat(item["date"])
                result.append(
                    (
                        body,
                        item.get("from"),
                        stamp if stamp.tzinfo else stamp.replace(tzinfo=zone),
                    )
                )
            return result
        result = []
        for line in text.splitlines():
            match = PREFIX.match(line.lstrip("\u200e")) if fmt != "plain" else None
            if match:
                date, clock, body = match.groups()
                stamp = datetime.strptime(
                    f"{date} {clock}",
                    "%d/%m/%Y %H:%M"
                    if len(date.split("/")[-1]) == 4
                    else "%d/%m/%y %H:%M",
                ).replace(tzinfo=zone)
                sender, sep, body_text = body.partition(": ")
                result.append(
                    (body_text if sep else body, sender if sep else None, stamp)
                )
            elif result:
                body, sender, stamp = result[-1]
                result[-1] = (body + "\n" + line, sender, stamp)
            elif line.strip():
                if fmt == "whatsapp":
                    raise ValueError("Missing WhatsApp date prefix")
                result.append((line, None, None))
        if not result:
            raise ValueError("Empty text export")
        return result
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise OpError(ErrorCode.CORRUPT_INPUT, f"Invalid text export: {exc}") from exc


def text_analyze(task, src, out_dir, *, timeout_s, on_progress):
    started = time.perf_counter()
    end = deadline(timeout_s)
    entries = messages(src, task.params)
    source_hash = digest(src)
    findings = []
    texts = []
    for index, (text, sender, timestamp) in enumerate(entries):
        remaining(end)
        texts.append(text)
        evidence = Evidence(
            source_key=task.input_key,
            kind=EvidenceKind.MESSAGE,
            message_index=index,
            sender=sender,
            timestamp=timestamp,
            excerpt=text,
            sha256=source_hash,
        )
        findings.extend(findings_for(text, evidence))
    keywords, topics, sentiment = summaries("\n".join(texts))
    remaining(end)
    return output(
        AnalysisResult(
            operation=AnalysisOperation.TEXT_ANALYZE,
            findings=findings,
            keywords=keywords,
            topics=topics,
            sentiment=sentiment,
            model=ModelInfo(name="bilingual-rules", version="1"),
            timings_s={"infer": time.perf_counter() - started},
        ),
        out_dir,
        on_progress,
    )

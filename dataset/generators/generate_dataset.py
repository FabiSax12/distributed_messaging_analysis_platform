"""Generate the 40 synthetic cases: chats, voice notes, videos, images, labels.

    uv run --group dataset python dataset/generators/fetch_coco.py      # once
    uv run --group dataset python dataset/generators/generate_dataset.py [--seed 42] [--force]

Requires ffmpeg on PATH and a TTS backend (see ``tts.py``). Writes, per case:

- ``dataset/cases/<case_id>/manifest.json`` (committed)
- ``chat/whatsapp.txt`` or ``chat/result.json`` (committed, small)
- media under ``media/`` (WhatsApp) or ``chat/photos|voice_messages|video_files``
  (Telegram), ignored by git
- ``dataset/ground_truth/<case_id>.json`` (committed)

Everything is planned from one ``random.Random(seed)`` before any file is
rendered, so the plan (texts, labels, message indexes, segment times) is
reproducible. Media bytes depend on the TTS voices and the ffmpeg/x264 build;
the manifests record the hashes of the published media. Existing media files
are reused unless ``--force`` is given.

Labels come from the generator: it knows which message or spoken sentence it
made harmful and why (see ``corpus.py``). Image labels are COCO's human
knife/scissors boxes (see ``fetch_coco.py``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import corpus as C
import tts

GENERATORS = Path(__file__).resolve().parent
DATASET_ROOT = GENERATORS.parent
CASES_DIR = DATASET_ROOT / "cases"
GT_DIR = DATASET_ROOT / "ground_truth"
BUILD = DATASET_ROOT / "build"
COCO_IMAGES = BUILD / "coco" / "val2017"
COCO_SELECTION = GENERATORS / "data" / "coco_selection.json"
HEAVY_BYTES = 200 * 1024 * 1024

N_CASES = 40
TARGET_USER_MESSAGES = 21_000
TOTAL_IMAGES = 300
TOTAL_AUDIO = 150
LABELED_BY = "Camila Hidalgo (generator labels; images: COCO 2017 human annotations)"

# name: (title, harmful categories, message harmful rate, voice harmful rate, priorities)
SCENARIOS = {
    "extortion": ("Extortion of a small business owner", [C.THREAT, C.ILLICIT], 0.05, 0.45, ["critical", "high"]),
    "drug_sales": ("Drug sales coordinated through a group chat", [C.ILLICIT, C.THREAT], 0.05, 0.4, ["high", "critical"]),
    "bullying": ("School bullying in a class group", [C.OFFENSIVE, C.THREAT], 0.06, 0.35, ["high", "medium"]),
    "gang_violence": ("Planned assault discussed by a group", [C.VIOLENCE, C.THREAT], 0.04, 0.4, ["critical", "high"]),
    "neighbors": ("Neighborhood dispute that escalates", [C.OFFENSIVE, C.THREAT, C.VIOLENCE], 0.03, 0.25, ["medium", "high"]),
    "harassment": ("Harassment of an ex-partner", [C.THREAT, C.OFFENSIVE], 0.04, 0.35, ["high"]),
    "fraud": ("Money laundering through a family business", [C.ILLICIT], 0.03, 0.3, ["medium", "high"]),
    "family": ("Family group chat (benign control)", [], 0.0, 0.0, ["low"]),
    "work": ("Work team coordination (benign control)", [], 0.0, 0.0, ["low", "medium"]),
    "sports": ("Sports club chat (benign control)", [], 0.0, 0.0, ["low"]),
}
HARMFUL_SCENARIOS = [s for s, v in SCENARIOS.items() if v[1]]
BENIGN_SCENARIOS = [s for s, v in SCENARIOS.items() if not v[1]]
TIMEZONES = ["America/Costa_Rica"] * 6 + ["America/Mexico_City", "America/Bogota", "Europe/Madrid"]


# ---- Plan ------------------------------------------------------------------- #


@dataclass
class Entry:
    """One chat entry, in export order (its position is the message_index)."""

    sender: str | None  # None = system/service entry
    text: str
    label: str | None = None  # finding category, or None
    media: tuple[str, int] | None = None  # ("image"|"audio"|"video", n)
    ts: datetime | None = None
    service_action: str | None = None


@dataclass
class VoicePlan:
    sentences: list[tuple[str, str | None]]  # (text, category or None)
    voice: str
    rate: int
    pad_to_s: float | None = None  # heavy video track length


@dataclass
class CasePlan:
    case_id: str
    kind: str  # hetero | heavy | standard | text_only | text_images
    fmt: str  # whatsapp | telegram
    scenario: str
    lang: str  # es | en | mixed
    timezone: str
    priority: str
    n_user_messages: int
    images: list[dict] = field(default_factory=list)  # coco selection entries
    voices: list[VoicePlan] = field(default_factory=list)
    video: VoicePlan | None = None  # audio track of the case video
    entries: list[Entry] = field(default_factory=list)


def _fill(rng: random.Random, template: str) -> str:
    out = template
    for key, values in C.SLOTS.items():
        while "{" + key + "}" in out:
            out = out.replace("{" + key + "}", rng.choice(values), 1)
    return out


def _lang_of(rng: random.Random, lang: str) -> str:
    return lang if lang != "mixed" else rng.choice(["es", "en"])


def _harmful_text(rng: random.Random, category: str, lang: str) -> str:
    # About 70% of harmful messages use a phrasing the rules know.
    bank = C.DETECTABLE if rng.random() < 0.7 else C.PARAPHRASE
    return _fill(rng, rng.choice(bank[category][lang]))


def _voice_plan(rng: random.Random, scenario: str, length: str) -> VoicePlan:
    n = {"short": rng.randint(1, 2), "medium": rng.randint(4, 10), "long": rng.randint(20, 40)}[length]
    picked = rng.sample(C.VOICE_BENIGN, min(n, len(C.VOICE_BENIGN)))
    picked += [rng.choice(C.VOICE_BENIGN) for _ in range(n - len(picked))]
    sentences: list[tuple[str, str | None]] = [(text, None) for text in picked]
    _, categories, _, voice_rate, _ = SCENARIOS[scenario]
    if categories and rng.random() < voice_rate:
        category = rng.choice(categories)
        bank = C.VOICE_PARAPHRASE if category in C.VOICE_PARAPHRASE and rng.random() < 0.25 else C.VOICE_DETECTABLE
        if category not in bank:
            bank = C.VOICE_DETECTABLE
        sentences[rng.randrange(n)] = (rng.choice(bank[category]), category)
    elif not categories and rng.random() < 0.15:
        sentences[rng.randrange(n)] = (rng.choice(C.VOICE_HARD_NEGATIVE), None)
    return VoicePlan(sentences, rng.choice(tts.VOICES), rng.choice([-1, 0, 1]))


def _spread(rng: random.Random, total: int, slots: list[int]) -> dict[int, int]:
    """Distribute ``total`` over the case indexes in ``slots`` as evenly as possible."""
    base, extra = divmod(total, len(slots))
    lucky = set(rng.sample(slots, extra))
    return {i: base + (i in lucky) for i in slots}


def plan_dataset(seed: int) -> list[CasePlan]:
    rng = random.Random(seed)
    selection = json.loads(COCO_SELECTION.read_text(encoding="utf-8"))["images"]
    positives = [img for img in selection if img["sensitive_boxes"]]
    negatives = [img for img in selection if not img["sensitive_boxes"]]
    images = positives + rng.sample(negatives, TOTAL_IMAGES - len(positives))
    rng.shuffle(images)

    kinds = (["hetero"] * 10 + ["heavy"] * 5 + ["standard"] * 20 + ["text_only"] * 3 + ["text_images"] * 2)
    telegram = {7, 8, 9, 13, 14, 30, 31, 32, 33, 34, 37}  # 0-based case indexes
    scenarios = [rng.choice(HARMFUL_SCENARIOS) for _ in range(28)] + [rng.choice(BENIGN_SCENARIOS) for _ in range(12)]
    rng.shuffle(scenarios)

    # Fixed per-kind media counts; the rest spread over standard cases.
    fixed_images = {"hetero": 8, "heavy": 6, "text_images": 12, "text_only": 0}
    fixed_audio = {"hetero": 5, "heavy": 3, "text_images": 0, "text_only": 0}
    standard = [i for i, k in enumerate(kinds) if k == "standard"]
    image_counts = _spread(rng, TOTAL_IMAGES - sum(fixed_images[k] for k in kinds if k != "standard"), standard)
    audio_counts = _spread(rng, TOTAL_AUDIO - sum(fixed_audio[k] for k in kinds if k != "standard"), standard)

    weights = [rng.uniform(0.6, 1.4) * (3.0 if k == "text_only" else 1.0) for k in kinds]
    scale = TARGET_USER_MESSAGES / sum(weights)
    msg_counts = [round(w * scale) for w in weights]
    msg_counts[-1] += TARGET_USER_MESSAGES - sum(msg_counts)

    plans = []
    cursor = 0
    for i, kind in enumerate(kinds):
        scenario = scenarios[i]
        _, _, _, _, priorities = SCENARIOS[scenario]
        plan = CasePlan(
            case_id=f"case-{i + 1:03d}",
            kind=kind,
            fmt="telegram" if i in telegram else "whatsapp",
            scenario=scenario,
            lang=rng.choices(["es", "en", "mixed"], [5, 3, 2])[0],
            timezone=rng.choice(TIMEZONES),
            priority=rng.choice(priorities),
            n_user_messages=msg_counts[i],
        )
        n_img = image_counts.get(i, fixed_images.get(kind, 0))
        plan.images = images[cursor : cursor + n_img]
        cursor += n_img
        n_audio = audio_counts.get(i, fixed_audio.get(kind, 0))
        for _ in range(n_audio):
            length = rng.choices(["short", "medium", "long"], [55, 33, 12])[0]
            plan.voices.append(_voice_plan(rng, scenario, length))
        if kind == "hetero":
            plan.video = _voice_plan(rng, scenario, "medium")
        elif kind == "heavy":
            plan.video = _voice_plan(rng, scenario, "long")
            plan.video.pad_to_s = 100.0
        plan.entries = _plan_chat(rng, plan)
        plans.append(plan)
    assert cursor == TOTAL_IMAGES
    return plans


def _plan_chat(rng: random.Random, plan: CasePlan) -> list[Entry]:
    people = [f"{f} {l}" for f, l in zip(rng.sample(C.FIRST_NAMES, 6), rng.sample(C.LAST_NAMES, 6))]
    members = people[: rng.randint(3, 6)]
    _, categories, harmful_rate, _, _ = SCENARIOS[plan.scenario]
    group = f"{plan.scenario.replace('_', ' ').title()} {rng.randint(1, 99)}"

    body: list[Entry] = []
    n = plan.n_user_messages
    n_media = len(plan.images) + len(plan.voices) + (1 if plan.video else 0)
    n_harmful = round((n - n_media) * harmful_rate) if categories else 0
    n_hard = max(1, round((n - n_media) * 0.006))
    n_plain = n - n_media - n_harmful - n_hard

    for _ in range(n_plain):
        lang = _lang_of(rng, plan.lang)
        if rng.random() < 0.03:
            bank = C.MULTILINE_ES if lang == "es" else C.MULTILINE_EN
            text = _fill(rng, rng.choice(bank)) if plan.fmt == "whatsapp" else _fill(rng, rng.choice(bank)).replace("\n", " ")
        elif plan.fmt == "whatsapp" and rng.random() < 0.02:
            text = "<Media omitted>"
        else:
            text = _fill(rng, rng.choice(C.BENIGN_ES if lang == "es" else C.BENIGN_EN))
        body.append(Entry(rng.choice(members), text))
    for _ in range(n_harmful):
        category = rng.choice(categories)
        body.append(Entry(rng.choice(members), _harmful_text(rng, category, _lang_of(rng, plan.lang)), label=category))
    for _ in range(n_hard):
        body.append(Entry(rng.choice(members), _fill(rng, rng.choice(C.HARD_NEGATIVE[_lang_of(rng, plan.lang)]))))
    for k in range(len(plan.images)):
        body.append(Entry(rng.choice(members), "", media=("image", k)))
    for k in range(len(plan.voices)):
        body.append(Entry(rng.choice(members), "", media=("audio", k)))
    if plan.video:
        body.append(Entry(rng.choice(members), "", media=("video", 0)))
    rng.shuffle(body)

    # System/service entries: group creation at the top, joins/leaves inside.
    a, b = members[0], members[1]
    if plan.fmt == "whatsapp":
        head = [Entry(None, C.SYSTEM_WHATSAPP[0]), Entry(None, f'{a} created group "{group}"')]
        head += [Entry(None, f"{a} added {m}") for m in members[1:]]
        for _ in range(rng.randint(2, 6)):
            body.insert(rng.randrange(len(body)), Entry(None, _fill(rng, rng.choice(C.SYSTEM_WHATSAPP[2:])).format(a=a, b=b, group=group)))
    else:
        head = [Entry(None, "", service_action="create_group"), Entry(None, "", service_action="invite_members")]
        for _ in range(rng.randint(2, 6)):
            body.insert(rng.randrange(len(body)), Entry(None, "", service_action=rng.choice(C.SYSTEM_TELEGRAM[1:])))
    entries = head + body  # service entries get their actor when rendered
    ts =datetime(2024, 1, 1) + timedelta(days=rng.randint(0, 640), hours=rng.randint(7, 20), minutes=rng.randint(0, 59))
    for e in entries:
        ts += timedelta(minutes=min(240, rng.expovariate(1 / 18)), seconds=rng.randint(0, 59))
        if ts.hour < 6:  # people sleep
            ts = ts.replace(hour=rng.randint(6, 8))
        e.ts = ts
    plan._group = group  # type: ignore[attr-defined]
    plan._members = members  # type: ignore[attr-defined]
    return entries


# ---- Rendering --------------------------------------------------------------- #


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def _compose(plan: VoicePlan, clips: dict[str, Path], rng: random.Random) -> tuple[bytes, list[dict]]:
    """PCM of the note and the time span of each sentence."""
    pcm = tts.silence(rng.uniform(0.3, 0.8))
    spans = []
    for text, category in plan.sentences:
        start = tts.seconds(pcm)
        pcm += tts.read_pcm(clips[tts.Clip(text, plan.voice, plan.rate).key])
        spans.append({"text": text, "category": category, "start": round(start, 2), "end": round(tts.seconds(pcm), 2)})
        pcm += tts.silence(rng.uniform(0.35, 1.0))
    pcm += tts.silence(0.5)
    if plan.pad_to_s and tts.seconds(pcm) < plan.pad_to_s:
        pcm += tts.silence(plan.pad_to_s - tts.seconds(pcm))
    return pcm, spans


def _media_names(plan: CasePlan) -> dict[tuple[str, int], str]:
    names = {}
    counter = 1
    for e in plan.entries:
        if not e.media:
            continue
        kind, _ = e.media
        if plan.fmt == "whatsapp":
            prefix, ext = {"image": ("IMG", "jpg"), "audio": ("PTT", "opus"), "video": ("VID", "mp4")}[kind]
            names[e.media] = f"media/{prefix}-{e.ts:%Y%m%d}-WA{counter:04d}.{ext}"
        else:
            stamp = f"{e.ts:%d-%m-%Y_%H-%M-%S}"
            folder, base, ext = {"image": ("photos", "photo", "jpg"), "audio": ("voice_messages", "audio", "ogg"),
                                 "video": ("video_files", "video", "mp4")}[kind]
            names[e.media] = f"chat/{folder}/{base}_{counter}@{stamp}.{ext}"
        counter += 1
    return names


def _render_chat(plan: CasePlan, names: dict, durations: dict) -> tuple[str, bytes]:
    members = plan._members  # type: ignore[attr-defined]
    if plan.fmt == "whatsapp":
        lines = []
        for e in plan.entries:
            prefix = f"{e.ts:%d/%m/%y, %H:%M} - "
            text = f"{Path(names[e.media]).name} (file attached)" if e.media else e.text
            lines.append(prefix + (f"{e.sender}: {text}" if e.sender else text))
        return "chat/whatsapp.txt", ("\n".join(lines) + "\n").encode("utf-8")

    ids = {m: f"user{1000 + i}" for i, m in enumerate(members)}
    messages = []
    rng = random.Random(plan.case_id)
    for i, e in enumerate(plan.entries, 1):
        base = {"id": i, "date": e.ts.isoformat(timespec="seconds")}
        if e.service_action:
            actor = members[0]
            msg = {**base, "type": "service", "actor": actor, "actor_id": ids[actor], "action": e.service_action, "text": ""}
            if e.service_action == "invite_members":
                msg["members"] = members[1:]
            if e.service_action == "edit_group_title":
                msg["title"] = plan._group  # type: ignore[attr-defined]
            messages.append(msg)
            continue
        msg = {**base, "type": "message", "from": e.sender, "from_id": ids[e.sender]}
        if e.media:
            kind, _ = e.media
            path = names[e.media].removeprefix("chat/")
            if kind == "image":
                msg.update(photo=path, width=640, height=480, text="")
            elif kind == "audio":
                msg.update(file=path, media_type="voice_message", mime_type="audio/ogg",
                           duration_seconds=round(durations[e.media]), text="")
            else:
                msg.update(file=path, media_type="video_file", mime_type="video/mp4",
                           duration_seconds=round(durations[e.media]), text="")
        else:
            words = e.text.split(" ")
            if len(words) > 3 and rng.random() < 0.08:
                cut = rng.randint(1, len(words) - 1)
                msg["text"] = [" ".join(words[:cut]) + " ", {"type": "bold", "text": " ".join(words[cut:])}]
            else:
                msg["text"] = e.text
        messages.append(msg)
    export = {"name": plan._group, "type": "private_group", "id": 1_000_000 + int(plan.case_id[-3:]), "messages": messages}  # type: ignore[attr-defined]
    return "chat/result.json", (json.dumps(export, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _encode_audio(wav: Path, out: Path) -> None:
    _ffmpeg(["-i", str(wav), "-c:a", "libopus", "-b:a", "24k", "-vbr", "on", "-application", "voip", "-f", "ogg", str(out)])


def _encode_video(wav: Path, out: Path, heavy: bool, seed: int) -> None:
    if heavy:
        video = ["-f", "lavfi", "-i", f"color=c=0x30404f:s=1280x720:r=25,noise=c0s=48:c0f=t+u:all_seed={seed}"]
        codec = ["-c:v", "libx264", "-preset", "ultrafast", "-b:v", "20M", "-minrate", "20M",
                 "-maxrate", "20M", "-bufsize", "20M", "-x264-params", "nal-hrd=cbr"]
    else:
        video = ["-f", "lavfi", "-i", f"testsrc2=s=320x240:r=15,hue=h={seed % 360}"]
        codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30"]
    _ffmpeg([*video, "-i", str(wav), "-map", "0:v", "-map", "1:a", "-shortest", *codec,
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)])


def render_case(plan: CasePlan, clips: dict[str, Path], force: bool) -> dict:
    case_dir = CASES_DIR / plan.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    names = _media_names(plan)
    rng = random.Random(f"audio-{plan.case_id}")
    scratch = BUILD / "wav" / plan.case_id
    sources: list[dict] = []
    gt_sources: list[dict] = []
    durations: dict = {}

    def add_source(rel: str, content_type: str, **extra) -> Path:
        path = case_dir / rel
        sources.append({"path": rel, "content_type": content_type, **extra})
        return path

    media_sources = []
    for k, image in enumerate(plan.images):
        rel = names[("image", k)]
        dest = case_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if force or not dest.is_file():
            shutil.copyfile(COCO_IMAGES / image["file_name"], dest)
        media_sources.append((rel, "image", f"COCO val2017 {image['file_name']} ({image['license']}, {image['flickr_url']})"))
        gt_sources.append({"path": rel, "findings": [
            {"category": "sensitive_image", "severity": "medium", "kind": "image", "bbox": b["bbox"],
             "notes": f"COCO {b['label']} box, {b['area_px']} px²"} for b in image["sensitive_boxes"]]})

    voices = [(("audio", k), v) for k, v in enumerate(plan.voices)]
    if plan.video:
        voices.append((("video", 0), plan.video))
    for key, voice in voices:
        rel = names[key]
        pcm, spans = _compose(voice, clips, rng)
        durations[key] = tts.seconds(pcm)
        dest = case_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if force or not dest.is_file():
            wav = scratch / (Path(rel).stem + ".wav")
            tts.write_wav(wav, pcm)
            if key[0] == "audio":
                _encode_audio(wav, dest)
            else:
                _encode_video(wav, dest, heavy=plan.kind == "heavy", seed=int(plan.case_id[-3:]))
            wav.unlink()
        if key[0] == "video" and plan.kind == "heavy" and dest.stat().st_size < HEAVY_BYTES:
            raise SystemExit(f"{dest} is {dest.stat().st_size} bytes, below the heavy threshold")
        kind_label = "synthetic speech (Windows SAPI TTS)" if tts.backend() == "sapi" else "synthetic speech (espeak-ng)"
        media_sources.append((rel, key[0], f"{kind_label}, {durations[key]:.1f} s"))
        gt_sources.append({"path": rel, "findings": [
            {"category": s["category"], "severity": C.SEVERITY[s["category"]], "kind": "audio_segment",
             "audio_start_s": s["start"], "audio_end_s": s["end"], "notes": s["text"][:200]}
            for s in spans if s["category"]]})

    chat_rel, chat_bytes = _render_chat(plan, names, durations)
    chat_path = case_dir / chat_rel
    chat_path.parent.mkdir(parents=True, exist_ok=True)
    chat_path.write_bytes(chat_bytes)
    add_source(chat_rel, "text", chat_format=plan.fmt, timezone=plan.timezone,
               sha256=hashlib.sha256(chat_bytes).hexdigest(), size_bytes=len(chat_bytes))
    gt_sources.insert(0, {"path": chat_rel, "findings": [
        {"category": e.label, "severity": C.SEVERITY[e.label], "kind": "message", "message_index": i}
        for i, e in enumerate(plan.entries) if e.label]})

    for rel, content_type, note in media_sources:
        path = case_dir / rel
        add_source(rel, content_type, sha256=sha256(path), size_bytes=path.stat().st_size, notes=note)

    types = {s["content_type"] for s in sources}
    title, *_ = SCENARIOS[plan.scenario]
    manifest = {
        "case_id": plan.case_id,
        "title": f"Synthetic: {title}" + (" (heavy video)" if plan.kind == "heavy" else ""),
        "priority": plan.priority,
        "heterogeneous": {"text", "image", "audio"} <= types and bool({"video", "document"} & types),
        "sources": sources,
        "notes": (f"Generated by dataset/generators/generate_dataset.py, seed {SEED}; scenario {plan.scenario}; "
                  f"kind {plan.kind}; chat language {plan.lang}; synthetic people and events."),
    }
    (case_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gt = {
        "case_id": plan.case_id,
        "labeled_by": LABELED_BY,
        "notes": ("Every source is labeled. Text/audio labels are the generator's intended category for the harmful "
                  "messages/sentences it inserted (including paraphrases the rules miss); hard negatives are "
                  "unlabeled-as-harmful by design. Image labels: COCO knife/scissors boxes."),
        "sources": gt_sources,
    }
    GT_DIR.mkdir(parents=True, exist_ok=True)
    (GT_DIR / f"{plan.case_id}.json").write_text(json.dumps(gt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"case_id": plan.case_id, "messages": len(plan.entries),
            "user_messages": sum(1 for e in plan.entries if e.sender), "sources": len(sources)}


SEED = 42


def main(argv: list[str] | None = None) -> int:
    global SEED
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true", help="re-render existing media")
    parser.add_argument("--only", nargs="*", help="case ids to render (default all)")
    args = parser.parse_args(argv)
    SEED = args.seed
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH")
    if not COCO_SELECTION.is_file():
        raise SystemExit("run fetch_coco.py first")

    plans = plan_dataset(args.seed)
    if args.only:
        plans = [p for p in plans if p.case_id in set(args.only)]
    needed = {tts.Clip(t, v.voice, v.rate) for p in plans for v in p.voices + ([p.video] if p.video else []) for t, _ in v.sentences}
    clips = tts.synthesize(sorted(needed, key=lambda c: c.key), BUILD / "tts")
    totals = {"messages": 0, "user_messages": 0}
    for plan in plans:
        info = render_case(plan, clips, args.force)
        totals["messages"] += info["messages"]
        totals["user_messages"] += info["user_messages"]
        print(f"{plan.case_id}: {plan.kind:11s} {plan.fmt:8s} {plan.scenario:13s} "
              f"{info['messages']:5d} entries, {info['sources']:3d} sources", file=sys.stderr)
    print(json.dumps(totals), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Count the dataset independently of the generator and of the manifests.

    uv run --all-packages --all-extras --group dataset python dataset/generators/dataset_stats.py [--json OUT]

- Messages: parses every chat export with the text worker's own parser
  (``workers.ops.analysis.text.messages``), the same code that assigns
  ``message_index`` in production. System/service entries are counted
  separately.
- Audio/video: ``ffprobe`` must find an audio stream; reports the duration
  distribution (voice notes from a few seconds to several minutes).
- Images: Pillow must open and verify each file.
- Heavy cases: a single source of at least 200 MiB on disk.

Exit code 1 if a minimum from docs/dataset.md is not met or a file is invalid.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

from PIL import Image
from workers.ops.analysis.text import messages

DATASET_ROOT = Path(__file__).resolve().parents[1]
HEAVY_BYTES = 200 * 1024 * 1024
MINIMUMS = {"cases": 40, "heterogeneous_cases": 10, "messages": 20_000, "images": 300,
            "voice_notes": 150, "heavy_cases": 5}


def probe(path: Path) -> tuple[bool, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(out.stdout)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return has_audio, float(info["format"]["duration"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DATASET_ROOT)
    parser.add_argument("--json", type=Path, help="also write the stats here")
    args = parser.parse_args(argv)

    stats: Counter = Counter()
    problems: list[str] = []
    durations: list[float] = []
    video_durations: list[float] = []
    formats: Counter = Counter()
    for manifest_path in sorted((args.root / "cases").glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        case_dir = manifest_path.parent
        stats["cases"] += 1
        stats["heterogeneous_cases"] += manifest["heterogeneous"]
        heavy = False
        for source in manifest["sources"]:
            path = case_dir / source["path"]
            if not path.is_file():
                problems.append(f"{case_dir.name}/{source['path']}: missing locally")
                continue
            heavy |= path.stat().st_size >= HEAVY_BYTES
            kind = source["content_type"]
            if kind == "text":
                params = {k: source[k] for k in ("chat_format", "timezone") if k in source}
                entries = messages(path, params)
                users = sum(1 for _, sender, _ in entries if sender)
                stats["messages"] += users
                stats["system_entries"] += len(entries) - users
                formats[source.get("chat_format", "plain")] += 1
            elif kind == "image":
                with Image.open(path) as image:
                    image.verify()
                stats["images"] += 1
            elif kind in ("audio", "video"):
                has_audio, seconds = probe(path)
                if not has_audio:
                    problems.append(f"{case_dir.name}/{source['path']}: no audio stream")
                if kind == "audio":
                    stats["voice_notes"] += 1
                    durations.append(seconds)
                else:
                    stats["videos"] += 1
                    video_durations.append(seconds)
        stats["heavy_cases"] += heavy

    result = {
        **{k: stats[k] for k in ("cases", "heterogeneous_cases", "heavy_cases", "messages", "system_entries",
                                 "images", "voice_notes", "videos")},
        "chat_formats": dict(formats),
        "voice_note_seconds": {
            "min": round(min(durations), 1), "median": round(statistics.median(durations), 1),
            "max": round(max(durations), 1), "total": round(sum(durations), 1),
            "under_10s": sum(d < 10 for d in durations), "10_to_60s": sum(10 <= d < 60 for d in durations),
            "over_60s": sum(d >= 60 for d in durations),
        } if durations else None,
        "video_seconds_total": round(sum(video_durations), 1),
        "minimums": {k: {"required": v, "actual": stats[k], "ok": stats[k] >= v} for k, v in MINIMUMS.items()},
        "problems": problems,
    }
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8", newline="\n")
    return 0 if not problems and all(m["ok"] for m in result["minimums"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())

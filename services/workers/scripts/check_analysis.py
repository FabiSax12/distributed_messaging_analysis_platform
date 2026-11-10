"""Local contract checks using synthetic fixtures; --models also runs real ML."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from shared.analysis import EvidenceKind, FindingCategory
from shared.messages import ErrorCode
from workers.ops.base import OpError
from workers.ops.run_local import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        chat = root / "chat.txt"
        chat.write_text(
            "01/03/24, 21:14 - Alex: gracias\n01/03/24, 21:15 - Sam: te voy a matar\nidiota\n",
            encoding="utf-8",
        )
        report = run("text_analyze", chat, out_dir=root / "text")
        assert {f.category for f in report.result.findings} == {
            FindingCategory.THREAT,
            FindingCategory.OFFENSIVE_LANGUAGE,
        }
        evidence = report.result.findings[0].evidence[0]
        assert evidence.message_index == 1 and evidence.sender == "Sam"
        assert evidence.timestamp.utcoffset().total_seconds() == -21600
        telegram = root / "result.json"
        telegram.write_text(
            json.dumps(
                {
                    "messages": [
                        {"type": "service", "date": "2024-03-01T10:00:00", "text": ""},
                        {
                            "type": "message",
                            "from": "Alex",
                            "date": "2024-03-01T10:01:00",
                            "text": ["I will ", {"type": "bold", "text": "kill you"}],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        report = run("text_analyze", telegram, out_dir=root / "telegram")
        assert report.result.findings[0].evidence[0].message_index == 1
        telegram.write_text("{broken", encoding="utf-8")
        try:
            run("text_analyze", telegram, out_dir=root / "bad")
            raise AssertionError("Corrupt export accepted")
        except OpError as exc:
            assert exc.code == ErrorCode.CORRUPT_INPUT
        try:
            run("text_analyze", chat, out_dir=root / "timeout", timeout_s=0)
            raise AssertionError("Expired deadline accepted")
        except OpError as exc:
            assert exc.code == ErrorCode.TIMEOUT
        if args.models:
            from PIL import Image

            image = root / "blank.png"
            Image.new("RGB", (320, 320), "gray").save(image)
            report = run("image_analyze", image, out_dir=root / "image", timeout_s=300)
            assert not report.result.findings
            video = root / "sample.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=160x120:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=16000:cl=mono",
                    "-t",
                    "2",
                    "-c:v",
                    "mpeg4",
                    "-c:a",
                    "aac",
                    str(video),
                ],
                check=True,
            )
            report = run("audio_analyze", video, out_dir=root / "audio", timeout_s=300)
            assert report.result.transcript is not None
            assert all(
                e.kind == EvidenceKind.AUDIO_SEGMENT
                for f in report.result.findings
                for e in f.evidence
            )
            assert not (root / "audio" / "extracted.wav").exists()
    print(
        "Analysis contract checks passed"
        + (" (real YOLO and Whisper)" if args.models else "")
    )


if __name__ == "__main__":
    main()

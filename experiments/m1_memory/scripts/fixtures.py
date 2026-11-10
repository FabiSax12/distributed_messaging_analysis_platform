"""Generate synthetic model inputs outside the dataset; no assessment labels."""

import argparse
import subprocess
from pathlib import Path

from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
args = parser.parse_args()
args.directory.mkdir(parents=True, exist_ok=True)
(args.directory / "chat.txt").write_text(
    "01/03/24, 21:15 - Synthetic: te voy a matar\n", encoding="utf-8"
)
Image.new("RGB", (320, 320), "gray").save(args.directory / "blank.png")
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
        "anullsrc=r=16000:cl=mono",
        "-t",
        "2",
        str(args.directory / "silence.wav"),
    ],
    check=True,
)

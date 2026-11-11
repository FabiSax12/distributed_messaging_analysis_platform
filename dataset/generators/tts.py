"""Text-to-speech for the synthetic voice notes (16 kHz mono 16-bit WAV).

Backends, picked automatically:

- ``sapi``: Windows System.Speech (voices "Microsoft David Desktop" and
  "Microsoft Zira Desktop", both en-US, installed with Windows). All clips are
  synthesized by one PowerShell process.
- ``espeak``: ``espeak-ng`` on Linux/macOS (``apt install espeak-ng``). The
  voices differ, so the audio (and its hashes) differ from the SAPI build.

Every clip is synthetic speech and is marked as such in the provenance table.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16_000
VOICES = ("david", "zira")
_SAPI_VOICE = {"david": "Microsoft David Desktop", "zira": "Microsoft Zira Desktop"}
_ESPEAK_VOICE = {"david": "en-us+m3", "zira": "en-us+f3"}

_SAPI_SCRIPT = r"""
param([string]$Jobs)
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($job in (Get-Content -Raw -Encoding UTF8 $Jobs | ConvertFrom-Json)) {
  $synth.SelectVoice($job.voice)
  $synth.Rate = [int]$job.rate
  $synth.SetOutputToWaveFile($job.out, $fmt)
  $synth.Speak($job.text)
  $synth.SetOutputToNull()
}
$synth.Dispose()
"""


@dataclass(frozen=True)
class Clip:
    text: str
    voice: str  # one of VOICES
    rate: int  # SAPI rate -10..10; espeak maps it to words/minute

    @property
    def key(self) -> str:
        raw = f"{self.voice}|{self.rate}|{self.text}".encode()
        return hashlib.sha256(raw).hexdigest()[:20]


def backend() -> str:
    if sys.platform == "win32":
        return "sapi"
    if shutil.which("espeak-ng"):
        return "espeak"
    raise SystemExit("No TTS backend: run on Windows (SAPI) or install espeak-ng")


def synthesize(clips: list[Clip], cache_dir: Path) -> dict[str, Path]:
    """Synthesize the clips that are not cached yet; returns key -> wav path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {c.key: cache_dir / f"{c.key}.wav" for c in clips}
    todo = {c.key: c for c in clips if not paths[c.key].is_file()}
    if not todo:
        return paths
    print(f"synthesizing {len(todo)} speech clips ({backend()})...", file=sys.stderr)
    if backend() == "sapi":
        _sapi(list(todo.values()), paths)
    else:
        for clip in todo.values():
            _espeak(clip, paths[clip.key])
    return paths


def _sapi(clips: list[Clip], paths: dict[str, Path]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "tts.ps1"
        script.write_text(_SAPI_SCRIPT, encoding="utf-8")
        # Chunks keep one failure from losing all the work.
        for start in range(0, len(clips), 200):
            chunk = clips[start : start + 200]
            jobs = Path(tmp) / "jobs.json"
            jobs.write_text(
                json.dumps(
                    [
                        {
                            "voice": _SAPI_VOICE[c.voice],
                            "rate": c.rate,
                            "text": c.text,
                            "out": str(paths[c.key]),
                        }
                        for c in chunk
                    ]
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Jobs",
                    str(jobs),
                ],
                check=True,
            )


def _espeak(clip: Clip, out: Path) -> None:
    raw = out.with_suffix(".raw.wav")
    subprocess.run(
        [
            "espeak-ng",
            "-v",
            _ESPEAK_VOICE[clip.voice],
            "-s",
            str(165 + 12 * clip.rate),
            "-w",
            str(raw),
            clip.text,
        ],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ac", "1",
         "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(out)],
        check=True,
    )
    raw.unlink()


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (
            SAMPLE_RATE,
            1,
            2,
        ):
            raise ValueError(f"{path}: expected 16 kHz mono 16-bit")
        return wav.readframes(wav.getnframes())


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(seconds * SAMPLE_RATE)


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


def seconds(pcm: bytes) -> float:
    return len(pcm) / 2 / SAMPLE_RATE

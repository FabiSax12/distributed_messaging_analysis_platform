"""Chunked transfer with SHA-256 verification (interface only).

A sender splits a file into numbered chunks, each with its own SHA-256, and
sends them in any order (possibly in parallel or with retries). The receiver
feeds them to a ``Reassembler``, which writes them in order and, on
``finish``, checks that the SHA-256 of the whole reassembled stream equals the
hash announced by the sender. A corrupt chunk is detected by its own hash
before it is written; a missing or reordered chunk by the final hash.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Protocol

DEFAULT_CHUNK_SIZE = (
    4 * 1024 * 1024
)  # 4 MiB; M3 measures throughput against other sizes


@dataclass(frozen=True)
class Chunk:
    index: int  # 0-based position in the original stream
    data: bytes
    sha256: str  # lowercase hex digest of ``data``


class ChunkIntegrityError(ValueError):
    """A chunk does not match its hash, or the reassembled stream does not
    match the expected full hash, or chunks are missing/duplicated."""


class Chunker(Protocol):
    def split(
        self, stream: BinaryIO, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> Iterator[Chunk]:
        """Read ``stream`` lazily and yield ``Chunk`` objects of ``chunk_size``
        bytes (the last one may be shorter). Must not load the whole stream in
        memory: at most one chunk is held at a time.

        Raises ``ValueError`` if ``chunk_size <= 0``.
        """
        ...

    def digest(self, stream: BinaryIO) -> str:
        """SHA-256 (lowercase hex) of the whole stream, read in bounded blocks.
        This is the hash a ``Reassembler`` verifies against."""
        ...


class Reassembler(Protocol):
    expected_sha256: str  # full-stream hash announced by the sender
    total_chunks: int

    def add(self, chunk: Chunk) -> None:
        """Accept one chunk in any order. Verifies ``chunk.sha256`` against its
        data and raises ``ChunkIntegrityError`` on mismatch or on an index out
        of ``range(total_chunks)``. A duplicate index with identical content is
        idempotent (a retried send)."""
        ...

    def missing(self) -> list[int]:
        """Indexes not received yet, ascending (lets the sender resend only those)."""
        ...

    def finish(self, out: BinaryIO) -> str:
        """Write all chunks to ``out`` in index order and return the SHA-256 of
        what was written. Raises ``ChunkIntegrityError`` if chunks are missing
        or the full hash differs from ``expected_sha256``."""
        ...

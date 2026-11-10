"""Content-addressed store with deduplication (interface only).

Objects are stored under the SHA-256 of their content, so storing the same
bytes twice keeps one physical copy (the second ``put`` is a dedup hit). The
dedup ratio over the dataset is one of the M3 measurements.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoreStats:
    objects: int  # distinct objects physically stored
    puts: int  # total ``put`` calls
    dedup_hits: int  # ``put`` calls whose content already existed
    logical_bytes: int  # sum of the sizes of every ``put`` (what the clients sent)
    physical_bytes: int  # bytes actually stored (one copy per distinct object)

    @property
    def dedup_ratio(self) -> float:
        """logical / physical bytes; 1.0 means no duplication was found."""
        return self.logical_bytes / self.physical_bytes if self.physical_bytes else 1.0


class ObjectNotFound(KeyError):
    """No object is stored under the requested SHA-256."""


class ContentStore(Protocol):
    def put(self, stream: BinaryIO) -> str:
        """Store the stream's content and return its SHA-256 (lowercase hex).
        Hashes while streaming (bounded memory) and writes atomically (temporary
        file + rename or equivalent), so a crash never leaves a partial object
        under a valid hash. If the object already exists, does not write it
        again and counts a dedup hit."""
        ...

    def get(self, sha256: str) -> Iterator[bytes]:
        """Stream the object's content in blocks. Raises ``ObjectNotFound``."""
        ...

    def exists(self, sha256: str) -> bool:
        """Whether an object with this hash is stored (lets a client skip an upload)."""
        ...

    def stats(self) -> StoreStats:
        """Counters since the store was created, for the dedup-ratio evidence."""
        ...

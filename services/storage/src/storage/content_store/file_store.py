"""Atomic local CAS. A hard link publishes without replacing another writer."""

import hashlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from threading import Lock
from typing import BinaryIO

from . import ObjectNotFound, StoreStats

BLOCK_SIZE = 1024 * 1024


class FileContentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._puts = self._hits = self._logical = 0
        self._seen: dict[str, int] = {}

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("object key must be lowercase SHA-256 hex")
        return self.root / digest

    def put(self, stream: BinaryIO) -> str:
        digest = hashlib.sha256()
        size = 0
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as out:
                while block := stream.read(BLOCK_SIZE):
                    digest.update(block)
                    size += len(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            key = digest.hexdigest()
            try:
                os.link(temporary, self._path(key))
                hit = False
            except FileExistsError:
                # Do not count or silently reuse a damaged preexisting object.
                with self._path(key).open("rb") as existing:
                    if hashlib.file_digest(existing, "sha256").hexdigest() != key:
                        raise ValueError("existing object is corrupt") from None
                hit = True
            with self._lock:
                self._puts += 1
                self._hits += hit
                self._logical += size
                self._seen[key] = size
            return key
        finally:
            os.unlink(temporary)

    def get(self, sha256: str) -> Iterator[bytes]:
        path = self._path(sha256)
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            raise ObjectNotFound(sha256) from None
        with stream:
            while block := stream.read(BLOCK_SIZE):
                yield block

    def exists(self, sha256: str) -> bool:
        return self._path(sha256).is_file()

    def stats(self) -> StoreStats:
        with self._lock:
            return StoreStats(
                len(self._seen),
                self._puts,
                self._hits,
                self._logical,
                sum(self._seen.values()),
            )

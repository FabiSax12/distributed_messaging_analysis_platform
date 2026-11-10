"""Streaming sender and temporary-disk receiver; no payload cache in RAM."""

import hashlib
import tempfile
from collections.abc import Iterator
from threading import RLock
from typing import BinaryIO

from . import DEFAULT_CHUNK_SIZE, Chunk, ChunkIntegrityError


class FileChunker:
    def split(
        self, stream: BinaryIO, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> Iterator[Chunk]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        index = 0
        while True:
            data = bytearray()
            while len(data) < chunk_size:
                block = stream.read(chunk_size - len(data))
                if not block:
                    break
                data.extend(block)
            if not data:
                return
            payload = bytes(data)
            yield Chunk(index, payload, hashlib.sha256(payload).hexdigest())
            index += 1

    def digest(self, stream: BinaryIO) -> str:
        digest = hashlib.sha256()
        while block := stream.read(DEFAULT_CHUNK_SIZE):
            digest.update(block)
        return digest.hexdigest()


class DiskReassembler:
    def __init__(self, expected_sha256: str, total_chunks: int):
        if total_chunks < 0:
            raise ValueError("total_chunks must be nonnegative")
        if len(expected_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in expected_sha256
        ):
            raise ValueError("expected_sha256 must be lowercase SHA-256 hex")
        self.expected_sha256 = expected_sha256
        self.total_chunks = total_chunks
        self._spool = tempfile.TemporaryFile()  # noqa: SIM115 -- owned until close/context exit
        self._chunks: dict[int, tuple[int, int, str]] = {}
        self._lock = RLock()

    def add(self, chunk: Chunk) -> None:
        with self._lock:
            if not 0 <= chunk.index < self.total_chunks:
                raise ChunkIntegrityError("chunk index out of range")
            if hashlib.sha256(chunk.data).hexdigest() != chunk.sha256:
                raise ChunkIntegrityError("chunk digest mismatch")
            if chunk.index in self._chunks:
                offset, size, digest = self._chunks[chunk.index]
                self._spool.seek(offset)
                if (size, digest) != (
                    len(chunk.data),
                    chunk.sha256,
                ) or self._spool.read(size) != chunk.data:
                    raise ChunkIntegrityError("conflicting duplicate chunk")
                return
            self._spool.seek(0, 2)
            offset = self._spool.tell()
            self._spool.write(chunk.data)
            self._chunks[chunk.index] = (offset, len(chunk.data), chunk.sha256)

    def missing(self) -> list[int]:
        with self._lock:
            return [i for i in range(self.total_chunks) if i not in self._chunks]

    def finish(self, out: BinaryIO) -> str:
        with self._lock, tempfile.TemporaryFile() as assembled:
            if self.missing():
                raise ChunkIntegrityError("missing chunks")
            digest = hashlib.sha256()
            for index in range(self.total_chunks):
                offset, size, expected = self._chunks[index]
                self._spool.seek(offset)
                chunk_digest = hashlib.sha256()
                remaining = size
                while remaining:
                    block = self._spool.read(min(remaining, DEFAULT_CHUNK_SIZE))
                    if not block:
                        raise ChunkIntegrityError("truncated spool")
                    remaining -= len(block)
                    digest.update(block)
                    chunk_digest.update(block)
                    assembled.write(block)
                if chunk_digest.hexdigest() != expected:
                    raise ChunkIntegrityError("spooled chunk digest mismatch")
            result = digest.hexdigest()
            if result != self.expected_sha256:
                raise ChunkIntegrityError("full digest mismatch")
            assembled.seek(0)
            while block := assembled.read(DEFAULT_CHUNK_SIZE):
                view = memoryview(block)
                while view:
                    written = out.write(view)
                    if written is None or written <= 0:
                        raise OSError("output made no write progress")
                    view = view[written:]
            return result

    def close(self) -> None:
        with self._lock:
            self._spool.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

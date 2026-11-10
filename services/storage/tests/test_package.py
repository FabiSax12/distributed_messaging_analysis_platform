"""Smoke test: the M3 interfaces import and expose their public names."""

from __future__ import annotations


def test_interfaces_import():
    from storage.backpressure import (
        BoundedQueue,
        OverflowPolicy,
        QueueClosed,
        QueueFull,
    )
    from storage.chunking import (
        DEFAULT_CHUNK_SIZE,
        Chunk,
        Chunker,
        ChunkIntegrityError,
        Reassembler,
    )
    from storage.content_store import ContentStore, ObjectNotFound, StoreStats

    assert DEFAULT_CHUNK_SIZE > 0
    assert {p.value for p in OverflowPolicy} == {"block", "reject"}
    assert StoreStats(1, 3, 2, 300, 100).dedup_ratio == 3.0
    assert StoreStats(0, 0, 0, 0, 0).dedup_ratio == 1.0
    for name in (
        BoundedQueue,
        QueueClosed,
        QueueFull,
        Chunk,
        Chunker,
        ChunkIntegrityError,
        Reassembler,
        ContentStore,
        ObjectNotFound,
    ):
        assert name is not None

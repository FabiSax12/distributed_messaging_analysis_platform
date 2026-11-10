"""Executable contract checks: uv run --all-packages python services/storage/check_storage.py."""

import hashlib
import io
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from storage.backpressure import OverflowPolicy, QueueClosed, QueueFull
from storage.backpressure.thread_queue import ThreadBoundedQueue
from storage.chunking import Chunk, ChunkIntegrityError
from storage.chunking.file_chunker import DiskReassembler, FileChunker
from storage.content_store.file_store import FileContentStore


def raises(kind, call):
    try:
        call()
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


def main():
    data = b"hello world" * 1000
    chunks = list(FileChunker().split(io.BytesIO(data), 512))
    digest = hashlib.sha256(data).hexdigest()
    with DiskReassembler(digest, len(chunks)) as receiver:
        raises(
            ChunkIntegrityError,
            lambda: receiver.add(Chunk(0, b"bad", chunks[0].sha256)),
        )
        raises(ChunkIntegrityError, lambda: receiver.finish(io.BytesIO()))
        for chunk in reversed(chunks):
            receiver.add(chunk)
            receiver.add(chunk)
        out = io.BytesIO()
        assert receiver.finish(out) == digest and out.getvalue() == data
    with DiskReassembler("0" * 64, len(chunks)) as receiver:
        for chunk in chunks:
            receiver.add(chunk)
        out = io.BytesIO()
        raises(ChunkIntegrityError, lambda: receiver.finish(out))
        assert not out.getvalue()
    with DiskReassembler(hashlib.sha256(b"").hexdigest(), 0) as receiver:
        assert receiver.finish(io.BytesIO()) == hashlib.sha256(b"").hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        store = FileContentStore(tmp)
        with ThreadPoolExecutor(max_workers=4) as pool:
            keys = list(pool.map(lambda _: store.put(io.BytesIO(data)), range(8)))
        assert set(keys) == {digest}
        assert b"".join(store.get(digest)) == data
        stats = store.stats()
        assert stats.objects == 1 and stats.dedup_hits == 7 and stats.dedup_ratio == 8
        raises(ValueError, lambda: store.exists("../bad"))
    queue = ThreadBoundedQueue(1, OverflowPolicy.REJECT)
    queue.put(1)
    raises(QueueFull, lambda: queue.put(2))
    assert queue.rejected() == 1
    queue.close()
    assert queue.get() == 1
    raises(QueueClosed, queue.get)
    raises(QueueClosed, lambda: queue.put(3))
    queue = ThreadBoundedQueue(1)
    queue.put(1)
    raises(QueueFull, lambda: queue.put(2, timeout=0.001))
    waiting = threading.Event()
    closed = threading.Event()

    def producer():
        waiting.set()
        try:
            queue.put(3)
        except QueueClosed:
            closed.set()

    thread = threading.Thread(target=producer)
    thread.start()
    assert waiting.wait(1)
    queue.close()
    thread.join(1)
    assert closed.is_set() and not thread.is_alive()
    print("Storage integrity, concurrent dedup and backpressure checks passed")


if __name__ == "__main__":
    main()

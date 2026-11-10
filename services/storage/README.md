# Storage (M3: I/O and storage)

Standalone building blocks for module M3. They do not depend on the
coordinator, the workers or any broker, so they can be implemented, tested and
benchmarked on a laptop and plugged into the coordinator after the RabbitMQ +
PostgreSQL migration.

Concrete stdlib implementations (David Molina):

- `storage.chunking.file_chunker.FileChunker` and `DiskReassembler`:
  lazy SHA-256 chunks; out-of-order disk spool, idempotent retries and full
  integrity verification before writing any output. Use the receiver as a
  context manager to close its temporary file.
- `storage.content_store.file_store.FileContentStore(root)`: streamed writes,
  fsync + atomic hard-link publication, concurrent identical writers dedup.
  Requires a filesystem supporting hard links (NTFS/ext4, not exFAT).
  Successful put counters and distinct encountered bytes are per store instance;
  they are not a persistent inventory of preexisting objects. Existing corrupt
  objects are rejected on put; get streams bytes without rehashing them.
- `storage.backpressure.thread_queue.ThreadBoundedQueue(maxsize, policy)`:
  BLOCK timeout or immediate REJECT, FIFO, bounded depth, close wakes producers
  and lets consumers drain. Capacity counts items, so callers must also bound
  item size. This standalone queue does not replace RabbitMQ.

Run contract checks: `uv run --all-packages python services/storage/check_storage.py`.
The components are standalone; S3 upload/download APIs are unchanged.

| Subpackage | Interface | Purpose |
|---|---|---|
| `storage.chunking` | `Chunker.split(stream, chunk_size) -> Iterator[Chunk(index, data, sha256)]`, `Chunker.digest`, `Reassembler.add/missing/finish` | Chunked transfer; each chunk carries its SHA-256 and the reassembled file is verified against the full hash |
| `storage.content_store` | `ContentStore.put(stream) -> sha256`, `get`, `exists`, `stats() -> StoreStats(dedup_ratio)` | Content-addressed store keyed by SHA-256; identical content is stored once |
| `storage.backpressure` | `BoundedQueue.put/get/depth/rejected/close` with `OverflowPolicy.BLOCK` or `REJECT` | Bounded buffer between a fast producer and slow consumers |

Implementations go next to the interfaces (e.g. `storage/chunking/file_chunker.py`)
and keep the stdlib-only dependency list unless there is a clear reason
(`asyncio`/`aiofiles` for the async I/O variant, for instance).

## Required M3 evidence

Scripts and raw CSVs go in `experiments/m3_io_storage/`; the write-up goes in
`docs/module-m3-io-storage.md`.

1. **Throughput vs chunk size.** MB/s of chunked transfer + reassembly + SHA-256
   verification for at least three chunk sizes (e.g. 256 KiB, 4 MiB, 16 MiB) on
   the heavy cases (>= 200 MB). Report mean and spread over several runs and
   whether the bottleneck is disk, network or hashing (CPU).
2. **Deduplication ratio on the dataset.** `StoreStats.dedup_ratio` (logical /
   physical bytes) after storing the whole dataset, plus the number of dedup
   hits. Explain where the duplicates come from (forwarded images, repeated
   voice notes, the same attachment in several cases).
3. **Backpressure during a burst.** Queue depth over time and response time
   (p50/p95) of the producer during a burst, with and without the bound
   (unbounded queue vs `BLOCK` vs `REJECT`), and the memory used in each case.

A corruption test (flip a byte in one chunk, drop a chunk) showing that the
reassembler rejects the file is cheap and worth including.

## Lint

```bash
uv run ruff check services/storage
uv run ruff format --check services/storage
```

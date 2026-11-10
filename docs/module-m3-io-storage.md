# Module: I/O and storage    Owner: David Molina
## 1. What was implemented

Concrete stdlib classes implement the existing Protocols: `FileChunker`,
`DiskReassembler`, `FileContentStore`, `ThreadBoundedQueue`. Reassembly accepts
out-of-order chunks and identical retries; rejects corrupt hashes, conflicting
duplicates, missing indexes and full-file mismatch. Payloads spool to disk;
output is written only after verification. Temporary disk may approach twice
input size; index metadata grows with chunk count. CAS streams, fsyncs and
publishes using an atomic hard link (requires NTFS/ext4, not exFAT).
Concurrent puts dedup; corrupt preexisting content fails. Queue close wakes
waiters, rejects new puts and lets consumers drain.

## 2. Operating systems concept

Chunking bounds working buffers; temporary files trade RAM for disk I/O.
Content addressing eliminates duplicate physical content. Bounded buffers
propagate producer pressure or reject overload; callers must bound item size.

## 3. Measurement method

```sh
uv run --all-packages python services/storage/check_storage.py
uv run --all-packages --all-extras python experiments/m3_io_storage/scripts/measure.py
```

Plotting uses matplotlib from the worker image extra; storage is stdlib-only.
Raw CSVs, machine details and `measurements.png` are in
`experiments/m3_io_storage/results/`. Windows 11, CPython 3.13, temporary local
disk, warm cache. A synthetic 200 MiB file is hashed, chunked, spooled,
verified and reassembled at three sizes, three repetitions each. This is local
I/O plus hashing, not network throughput. The burst sends 200 distinct 256 KiB
payloads; consumer takes 5 ms/item; bounded capacity 8. Depth and RSS sampled
every 2 ms. Put latency excludes payload generation. Sequential scenarios
share a process, so allocator/cache effects limit RSS comparisons. Sampled
peaks can miss instantaneous peaks. REJECT drops work; its latency does not
represent equivalent completed throughput to BLOCK.

## 4. Results

| Chunk size | Mean MiB/s | Sample standard deviation |
|---|---:|---:|
| 256 KiB | 257.83 | 3.68 |
| 4 MiB | 215.37 | 5.87 |
| 16 MiB | 186.34 | 15.16 |

| Queue | Rejected / 200 | Put p50 ms | Put p95 ms | Sampled peak depth | Peak process RSS MiB |
|---|---:|---:|---:|---:|---:|
| Unbounded | 0 | 0.001 | 0.0063 | 198 | 87.93 |
| BLOCK (8) | 0 | 5.155 | 5.541 | 8 | 40.71 |
| REJECT (8) | 192 | 0.001 | 0.0023 | 6 | 40.46 |

Synthetic dedup: 10 puts, 2 objects, 8 hits, 175000 logical / 35000 physical
bytes = ratio 5.0. This is a functional demonstration, not full-dataset evidence.
For real data rerun with `--dataset dataset/cases`; absent media fails the run.
The current checkout has no case manifests/media.

## 5. Interpretation

Small buffers performed best in this cached local workload. Measurements do
not separate hashing from disk/cache cost, so no bottleneck is asserted.
BLOCK held depth at capacity and transferred consumer delay to the producer;
REJECT bounded accepted work through immediate overload errors. Integrity,
concurrent dedup and close/wakeup checks passed. Components are standalone;
coordinator S3 and RabbitMQ paths remain unchanged. Real-dataset dedup and
transfer on its five heavy cases await the dataset owner's data.

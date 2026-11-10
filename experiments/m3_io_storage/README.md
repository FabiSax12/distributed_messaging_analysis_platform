# M3: I/O and storage

Owner: David Molina. Report: `docs/module-m3-io-storage.md`. Code under test:
`services/storage/` (chunking, content-addressed store, backpressure).

## Required evidence

1. **Throughput vs chunk size.** MB/s of chunked transfer + SHA-256 verified
   reassembly for at least three chunk sizes (e.g. 256 KiB, 4 MiB, 16 MiB), on
   the heavy cases (>= 200 MB), several runs each (mean and spread).
2. **Deduplication ratio on the dataset.** Logical vs physical bytes and
   number of dedup hits after storing the whole dataset in the content store.
3. **Backpressure during a burst.** Queue depth over time and producer
   response time (p50/p95) during a burst, with and without the bounded queue
   (`BLOCK` and `REJECT`), plus memory used in each case.
4. Optional but cheap: a corruption test (flipped byte, missing chunk) showing
   the reassembler rejects the file; sync vs async I/O comparison.

## Layout

```
m3_io_storage/
  scripts/   benchmark and plotting scripts (record machine, disk and command lines)
  results/   raw CSVs and the plot images (.png) used in the report
```

`results/` contents are git-ignored except `.gitkeep` (root `.gitignore`); add
the final CSVs and plots explicitly (`git add -f`) or copy them into
`docs/img/` when they go in the report.

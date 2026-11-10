# M1: Memory

Owner: David Molina. Report: `docs/module-m1-memory.md`.

## Required evidence

1. **Memory footprint per model.** Table with one row per analysis model
   (text, image, audio/Whisper...): RSS and PSS after loading, peak RSS during
   inference, input size, and the smallest `docker --memory` limit it survives.
   Measure with `python -m workers.ops.run_local` (reports `peak_rss_bytes`) and
   `/proc/<pid>/smaps_rollup` or `smem` for PSS.
2. **Memory per worker over time during a burst.** RSS (or cgroup
   `memory.current`) of each worker sampled every second while a burst of cases
   is processed; one line per worker in the plot.
3. **Provoked OOM and recovery.** Run a worker under a limit below what its
   model needs, show the OOM kill (exit code 137, `dmesg`/`docker inspect`
   `OOMKilled`), and show that the sub-task is requeued and completed by a
   worker with enough memory (or rejected by admission control).
4. **Model per process vs shared.** Total memory of N worker processes when
   each loads its own copy of the model, compared with loading it once and
   sharing it through `fork` + copy-on-write or `mmap` (PSS is the right
   metric: RSS double-counts shared pages).

## Layout

```
m1_memory/
  scripts/   measurement and plotting scripts (reproducible, with the exact command lines)
  results/   raw CSVs (one row per sample) and the plot images (.png) used in the report
```

`results/` contents are git-ignored except `.gitkeep` (root `.gitignore`); add
the final CSVs and plots explicitly (`git add -f`) or copy them into
`docs/img/` when they go in the report.

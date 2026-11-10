# M5: Heterogeneous compute

Owner: Camila Hidalgo. Report: `docs/module-m5-heterogeneous-compute.md`.

Benchmark one heavy model on different compute configurations, for example
Whisper or YOLO on CPU vs GPU (Colab or Kaggle), or CPU with different thread
counts and quantized vs non-quantized weights. The results feed the
coordinator's routing policy (which pool/node type gets which operation).

## Required evidence

1. **Table per configuration**: latency (p50/p95 per input), throughput
   (inputs/s or audio-seconds/s), and memory (peak RSS / GPU memory), with the
   hardware, model size and input set recorded for each row. Use inputs from
   the project dataset (voice notes of varied durations, or the image set).
2. **Routing-policy justification**: from the table, which operations go to
   which node type (e.g. long voice notes to the GPU node, short ones and text
   to CPU), the crossover point where the GPU pays off, and the cost per hour of
   each option (`infra/terraform/README.md` has the GPU switch and costs).
3. **Link to Unit 1** (CPU/GPU/NPU): explain the results in terms of
   parallelism, memory bandwidth, data transfer to the device and why an NPU or
   quantization changes the trade-off.

## Layout

```
m5_heterogeneous_compute/
  scripts/   benchmark scripts or exported notebooks (.ipynb / .py) and plotting scripts
  results/   raw CSVs (one row per run) and the plot images (.png) used in the report
```

`results/` contents are git-ignored except `.gitkeep` (root `.gitignore`); add
the final CSVs and plots explicitly (`git add -f`) or copy them into
`docs/img/` when they go in the report.

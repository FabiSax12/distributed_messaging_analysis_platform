# Module: Heterogeneous compute    Owner: Camila Hidalgo

## 1. What was implemented

- `experiments/m5_heterogeneous_compute/scripts/bench.py` + `bench_one.py`: a
  benchmark of the two heavy analysis models of the platform, faster-whisper
  (audio pool) and YOLOv8n (image pool), under controlled CPU configurations.
  It runs the **production operations** (`workers.ops.run_local.run`: ffmpeg
  extraction, hashing, model, findings) on inputs taken from the project
  dataset, one configuration per process, as a worker slot would.
- Configurations (GPU route not available on this host, so the handoff's
  alternative: threads and quantization on CPU):
  - Whisper `tiny` and `base` x compute type `int8` (CTranslate2 8-bit weight
    quantization) and `float32` x `MODEL_THREADS` 1/2/4/8 (9 configurations);
  - YOLOv8n float32 x torch threads 1/2/4/8;
  - Whisper tiny/int8 with P processes x T threads on a fixed budget of 4 and
    8 hardware threads (1x4, 2x2, 4x1, 1x8, 2x4, 4x2, 8x1): the question a
    worker pool actually faces (more slots or more threads per slot).
- Transcription quality per configuration: word error rate against the exact
  script the dataset generator synthesized, so quantization is judged on
  accuracy as well as speed.
- `plot.py`: figures and tables below. Raw CSVs (one row per run) and
  `host.json` (hardware, OS, library versions, memory scope) are in
  `experiments/m5_heterogeneous_compute/results/laptop-i5-11320h/`.
- Finding about the image worker: `MODEL_THREADS`/`OMP_NUM_THREADS` never
  reach YOLO. Ultralytics' `select_device()` runs on the first `predict` and
  calls `torch.set_num_threads(min(8, cpu_count - 1))`. The benchmark pins
  ultralytics' `NUM_THREADS` to control the variable; production uses the
  ultralytics default (see section 5 and the routing decision).

## 2. Operating systems concept

Heterogeneous compute is the scheduling of work onto processing units with
different strengths: CPU cores (few, fast, general, large caches), GPUs (many
simple lanes, high memory bandwidth, separate memory reached over PCIe) and
NPUs (fixed-function low-precision matrix units). The OS-level questions are:

- **Parallelism inside one process vs across processes.** A model can split
  one inference across threads (intra-op parallelism, shared memory, needs
  synchronization per layer) or the scheduler can run several independent
  inferences in separate processes (no synchronization, but each process
  pays its own model memory). The kernel schedules both kinds of threads on
  the same cores; logical CPUs from hyper-threading share execution units.
- **Memory bandwidth and footprint.** Transformer decoding (Whisper) is
  dominated by reading weights for every generated token; smaller weights
  (int8 = 1 byte vs float32 = 4 bytes) mean fewer bytes moved per token and a
  smaller resident set, which is also what admission control (M1) budgets.
- **Transfer cost to an accelerator.** A GPU needs inputs and weights copied
  over PCIe and a kernel launch per operation; for a 5-second voice note or a
  single 640x480 image the fixed cost can exceed the compute it saves.
- **Quantization** trades numeric precision for bandwidth and arithmetic
  throughput; NPUs are built around exactly this (int8/int4 MACs).

## 3. Measurement method

- **Host**: laptop, Intel Core i5-11320H (4 cores / 8 hardware threads,
  Tiger Lake, AVX-512/VNNI), 15.7 GB RAM, Windows 11. Exact
  versions in `host.json`. No other heavy workload ran during the campaign
  (only editing and git in other terminals).
- **Inputs** (seed 42, from the generated dataset): 12 voice notes
  stratified by duration, 4 short (5.5-8.6 s), 4 medium (10.0-34.2 s), 4 long
  (96.3-160.5 s), 610 s of audio in total; 40 images (COCO JPEGs, 640 px).
- **Procedure per configuration**: new process with the configuration in its
  environment (`AUDIO_MODEL`, `AUDIO_COMPUTE_TYPE`, `MODEL_THREADS`,
  `OMP_NUM_THREADS`); time imports, then the worker's `load_model()` (cold
  load from the local disk cache; weights were downloaded beforehand so no
  network time is included), then one warm-up run (discarded), then
  3 repeats over every input. Latency is the wall time of the production
  operation for one input (decode + resample with ffmpeg, SHA-256, model,
  rules): what a worker slot spends per sub-task, **without** broker and S3
  transfer (measured separately in M3).
- **Metrics**: p50/p95 latency over all warm runs (36 per audio
  configuration, 120 per image configuration) and per duration bucket;
  throughput = audio seconds / wall seconds (audio) or images / second;
  memory = process peak working set (Windows `peak_wset`, interpreter +
  libraries + model + buffers; the ffmpeg child process is excluded; no GPU
  memory because no GPU was used); model memory = RSS after `load_model()`
  minus RSS before; WER on the first repeat.
- **Parallel suite**: the 8 medium/long notes (580 s of audio) split
  round-robin over P processes started together, one pass, no warm-up;
  throughput uses the steady makespan (first inference start to last end),
  so model loading is reported separately.

## 4. Results

_Filled from the campaign below._

## 5. Interpretation

_Filled from the campaign below._

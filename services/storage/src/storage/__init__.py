"""M3 I/O and storage building blocks.

Public Protocols have concrete stdlib implementations beside them. Each subpackage is independent
of the coordinator, the workers and any broker, so it can be built, tested
and benchmarked standalone (see ``services/storage/README.md``).

- ``storage.chunking``: split a stream into fixed-size chunks with per-chunk
  SHA-256 and reassemble them verifying the full-file hash.
- ``storage.content_store``: content-addressed store keyed by SHA-256 with
  deduplication statistics.
- ``storage.backpressure``: bounded queue with a block or reject policy.
"""

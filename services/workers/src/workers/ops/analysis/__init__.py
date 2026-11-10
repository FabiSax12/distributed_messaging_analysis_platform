"""Analysis operation registry: ``AnalysisOperation`` -> function.

Register each op here, next to its module in this package, e.g.::

    from .text import text_analyze
    ANALYSIS_OPERATIONS[AnalysisOperation.TEXT_ANALYZE] = text_analyze

An op follows the ``Operation`` protocol in ``workers/ops/base.py`` and returns
``OpOutput(files, meta=AnalysisResult(...).to_meta())``. Try it with
``python -m workers.ops.run_local <operation> <file>``; see "Writing an
analysis operation" in ``services/workers/README.md``.

The runner (`workers/runner/process.py`) dispatches every sub-task through
this registry; an operation that is not registered fails the sub-task with
``processing_error``. Import heavy libraries (torch, whisper...) inside the
op function, not at module level, so importing this package stays cheap on
pools that never run that op.
"""

from __future__ import annotations

from shared.analysis import AnalysisOperation

from ..base import Operation

ANALYSIS_OPERATIONS: dict[AnalysisOperation, Operation] = {}

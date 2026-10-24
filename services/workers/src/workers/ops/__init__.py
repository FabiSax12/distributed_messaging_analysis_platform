"""Operation -> function registry.

TODO: the text, image and audio operations are implemented later and will be
registered here, keyed by `shared.routing.Operation`. Until then the registry
is empty and a worker that receives a sub-task fails with a `KeyError` (the
runner leaves the message for the broker to retry).
"""

from __future__ import annotations

from shared.routing import Operation as Op

from .base import Operation

OPERATIONS: dict[Op, Operation] = {}

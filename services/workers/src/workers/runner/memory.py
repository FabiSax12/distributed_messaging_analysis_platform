"""Container memory metrics (cgroup v2/v1), with process-tree fallback."""

from pathlib import Path

import psutil


def memory_usage():
    for current, limit in (
        ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
        (
            "/sys/fs/cgroup/memory/memory.usage_in_bytes",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
        ),
    ):
        try:
            used = int(Path(current).read_text().strip())
            maximum = int(Path(limit).read_text().strip())
            if maximum < 2**60:
                return used, maximum
        except (OSError, ValueError):
            continue
    parent = psutil.Process()
    used = parent.memory_info().rss
    for child in parent.children(recursive=True):
        try:
            used += child.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return used, psutil.virtual_memory().total

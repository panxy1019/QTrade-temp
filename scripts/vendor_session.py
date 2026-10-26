"""Serialize anonymous vendor sessions shared by cluster research collectors."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def baostock_session(*, blocking: bool = True) -> Iterator[None]:
    # The service runs on Linux. Keep the lease outside the NFS archive mount.
    import fcntl
    root = Path("/root/qtrade-local")
    root.mkdir(parents=True, exist_ok=True)
    with (root / "baostock-session.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


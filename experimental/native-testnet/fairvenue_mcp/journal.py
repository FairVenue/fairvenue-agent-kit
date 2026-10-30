"""Append-only local journal of this server's own actions.

Bot keys cannot read their account (balance, positions, open orders, fills)
through the API, so this journal is the agent's memory of what it submitted
and how each request ended. It holds public action fields and outcome codes
only: never seeds, signatures, signed envelopes or upstream error text.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Only the tail is read back; older history stays on disk untouched.
_TAIL_BYTES = 262_144


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, kind: str, entry: dict[str, Any]) -> None:
        record = {
            "at": datetime.now(tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "kind": kind,
            **entry,
        }
        line = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode()
        if not self.path.parent.is_dir():
            return  # Nothing is journaled before setup creates the private home directory.
        descriptor = os.open(
            self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def tail(self, limit: int) -> list[dict[str, Any]]:
        try:
            with open(self.path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                start = max(0, handle.tell() - _TAIL_BYTES)
                handle.seek(start)
                data = handle.read()
        except FileNotFoundError:
            return []
        lines = data.splitlines()
        if start and lines:
            lines = lines[1:]  # The first line may be cut by the tail window.
        entries: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                entries.append(value)
        return entries

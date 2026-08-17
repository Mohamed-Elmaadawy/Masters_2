"""One tiny shared helper: write a JSON file atomically. Used by evaluation/blinding.py
(scoring/mapping files) and evaluation/runner.py (B2's pre-answer checkpoint) -- pulled
out to here rather than duplicated in both, since both need the exact same guarantee: a
reader never observes a half-written file."""

from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: Path, payload: dict) -> None:
    """Writes `payload` to a temporary file in the same directory, then `os.replace`s
    it into place. `os.replace` is atomic on both POSIX and Windows (unlike a plain
    write, which a reader or a crash mid-write can observe half-finished, and unlike
    `os.rename`, which raises on Windows if the destination already exists)."""
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, path)

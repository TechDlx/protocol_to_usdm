"""Low-level filesystem helpers: atomic JSON writes and path containment."""

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel

from backend.storage.errors import StorageError


def atomic_write_text(path: Path, text: str) -> None:
    """Write to a sibling temp file then os.replace, so readers never see a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_model(path: Path, model: BaseModel) -> None:
    atomic_write_text(path, model.model_dump_json(indent=2) + "\n")


def write_json(path: Path, data: object) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def ensure_within(root: Path, candidate: Path) -> Path:
    """Resolve `candidate` and refuse anything that escapes `root` (e.g. via `..` or symlinks)."""
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise StorageError(f"path escapes storage root: {candidate}")
    return resolved

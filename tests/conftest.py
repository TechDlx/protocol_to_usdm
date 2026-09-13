from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest

from backend.storage.studies import StudyStore

MakePdf = Callable[..., bytes]


@pytest.fixture
def make_pdf() -> MakePdf:
    """Build a real, openable PDF in memory. `marker` makes the bytes (and sha256) unique."""

    def _make(pages: int = 2, marker: str = "") -> bytes:
        doc = pymupdf.open()
        for i in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"Protocol page {i + 1} {marker}")
        data: bytes = doc.tobytes()
        doc.close()
        return data

    return _make


@pytest.fixture
def store(tmp_path: Path) -> StudyStore:
    return StudyStore(tmp_path / "studies", max_upload_bytes=5 * 1024 * 1024)

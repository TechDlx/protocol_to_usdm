"""Study and run folder management.

Layout (all under the configured studies root, one fully isolated folder per study):

    <slug>/study.json
    <slug>/source/<protocol>.pdf
    <slug>/runs/<run-id>/run_state.json   (+ every stage artefact, written by later phases)

Nothing stored in one study folder references another; source paths are study-relative.
"""

import hashlib
import logging
import re
import secrets
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import pymupdf
from pydantic import ValidationError
from slugify import slugify

from backend.models.study import (
    RunState,
    RunStatus,
    SourceDocument,
    StageStatus,
    StudyCreate,
    StudyMeta,
    StudySummary,
)
from backend.storage.errors import (
    InvalidUploadError,
    RunNotFoundError,
    SourceNotFoundError,
    StudyNotFoundError,
)
from backend.storage.fs import ensure_within, write_json, write_model

log = logging.getLogger(__name__)

STUDY_FILE = "study.json"
SOURCE_DIR = "source"
RUNS_DIR = "runs"
RUN_STATE_FILE = "run_state.json"
RUN_CONFIG_FILE = "run_config.json"

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 ._()\-]+")
_CHUNK = 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def sanitize_pdf_filename(raw: str) -> str:
    """Keep protocol filenames human-readable while stripping paths and unsafe characters."""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    stem = _UNSAFE_FILENAME_CHARS.sub("_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._")[:150]
    return f"{stem or 'protocol'}.pdf"


class StudyStore:
    def __init__(self, root: Path, max_upload_bytes: int) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max_upload_bytes
        # Single-process local app: an in-process lock per study serialises study.json updates.
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._create_lock = threading.Lock()

    # ----- paths -------------------------------------------------------------------------

    def study_dir(self, slug: str) -> Path:
        if not SLUG_RE.fullmatch(slug):
            raise StudyNotFoundError(slug)
        return ensure_within(self.root, self.root / slug)

    def _existing_study_dir(self, slug: str) -> Path:
        path = self.study_dir(slug)
        if not (path / STUDY_FILE).is_file():
            raise StudyNotFoundError(slug)
        return path

    def run_dir(self, slug: str, run_id: str) -> Path:
        if not RUN_ID_RE.fullmatch(run_id):
            raise RunNotFoundError(run_id)
        path = ensure_within(self.root, self._existing_study_dir(slug) / RUNS_DIR / run_id)
        if not path.is_dir():
            raise RunNotFoundError(run_id)
        return path

    # ----- studies -----------------------------------------------------------------------

    def create_study(self, data: StudyCreate) -> StudyMeta:
        base = slugify(data.name, max_length=56, word_boundary=True) or "study"
        with self._create_lock:
            slug, n = base, 2
            while (self.root / slug).exists():
                slug, n = f"{base}-{n}", n + 1
            study_dir = self.study_dir(slug)
            (study_dir / SOURCE_DIR).mkdir(parents=True)
            (study_dir / RUNS_DIR).mkdir()
            now = _now()
            meta = StudyMeta(slug=slug, created_at=now, updated_at=now, **data.model_dump())
            write_model(study_dir / STUDY_FILE, meta)
        log.info("study created", extra={"study": slug})
        return meta

    def get_study(self, slug: str) -> StudyMeta:
        path = self._existing_study_dir(slug) / STUDY_FILE
        return StudyMeta.model_validate_json(path.read_text(encoding="utf-8"))

    def list_studies(self) -> list[StudySummary]:
        summaries: list[StudySummary] = []
        for child in sorted(self.root.iterdir()):
            if not (child.is_dir() and SLUG_RE.fullmatch(child.name)):
                continue
            if not (child / STUDY_FILE).is_file():
                continue
            try:
                meta = self.get_study(child.name)
            except (ValidationError, ValueError) as exc:
                # One corrupt study must not take down the whole listing.
                log.warning(
                    "skipping unreadable study", extra={"study": child.name, "error": str(exc)}
                )
                continue
            summaries.append(StudySummary(**meta.model_dump(), runs=self.list_runs(meta.slug)))
        return sorted(summaries, key=lambda s: s.created_at, reverse=True)

    # ----- source documents --------------------------------------------------------------

    def add_source(self, slug: str, filename: str, stream: BinaryIO) -> SourceDocument:
        study_dir = self._existing_study_dir(slug)
        source_dir = study_dir / SOURCE_DIR
        source_dir.mkdir(exist_ok=True)
        safe_name = sanitize_pdf_filename(filename)

        tmp = source_dir / f".upload-{secrets.token_hex(6)}.part"
        try:
            digest, size = self._stream_to(tmp, stream)
            page_count = self._validate_pdf(tmp)
            with self._locks[slug]:
                meta = self.get_study(slug)
                duplicate = next((s for s in meta.sources if s.sha256 == digest), None)
                if duplicate:
                    log.info(
                        "duplicate upload ignored",
                        extra={"study": slug, "file": duplicate.filename},
                    )
                    return duplicate
                taken = {s.filename for s in meta.sources}
                final_name = self._unique_name(safe_name, taken, source_dir)
                final = ensure_within(study_dir, source_dir / final_name)
                tmp.replace(final)
                doc = SourceDocument(
                    filename=final_name,
                    relative_path=final.relative_to(study_dir).as_posix(),
                    sha256=digest,
                    size_bytes=size,
                    page_count=page_count,
                    uploaded_at=_now(),
                )
                meta.sources.append(doc)
                meta.updated_at = _now()
                write_model(study_dir / STUDY_FILE, meta)
        finally:
            tmp.unlink(missing_ok=True)
        log.info(
            "source uploaded", extra={"study": slug, "file": doc.filename, "pages": page_count}
        )
        return doc

    def source_path(self, slug: str, filename: str) -> Path:
        study_dir = self._existing_study_dir(slug)
        doc = next((s for s in self.get_study(slug).sources if s.filename == filename), None)
        if doc is None:
            raise SourceNotFoundError(filename)
        path = ensure_within(study_dir, study_dir / doc.relative_path)
        if not path.is_file():
            raise SourceNotFoundError(filename)
        return path

    def _stream_to(self, dest: Path, stream: BinaryIO) -> tuple[str, int]:
        sha, size = hashlib.sha256(), 0
        with dest.open("wb") as out:
            first = True
            while chunk := stream.read(_CHUNK):
                if first and not chunk.startswith(b"%PDF-"):
                    raise InvalidUploadError("file is not a PDF (missing %PDF- header)")
                first = False
                size += len(chunk)
                if size > self.max_upload_bytes:
                    limit_mb = self.max_upload_bytes // (1024 * 1024)
                    raise InvalidUploadError(f"file exceeds the {limit_mb} MB upload limit")
                sha.update(chunk)
                out.write(chunk)
        if size == 0:
            raise InvalidUploadError("file is empty")
        return sha.hexdigest(), size

    @staticmethod
    def _validate_pdf(path: Path) -> int:
        # Open from bytes, not the path: when MuPDF fails to parse a file opened by path it can
        # keep the OS handle, and on Windows the temp upload then can't be deleted.
        try:
            with pymupdf.open(stream=path.read_bytes(), filetype="pdf") as doc:  # type: ignore[no-untyped-call]
                if doc.needs_pass:
                    raise InvalidUploadError("PDF is password-protected")
                if doc.page_count == 0:
                    raise InvalidUploadError("PDF has no pages")
                return int(doc.page_count)
        except InvalidUploadError:
            raise
        except Exception as exc:
            raise InvalidUploadError(f"PDF could not be opened: {exc}") from exc

    @staticmethod
    def _unique_name(name: str, taken: set[str], directory: Path) -> str:
        stem, candidate, n = name[:-4], name, 2
        while candidate in taken or (directory / candidate).exists():
            candidate, n = f"{stem} ({n}).pdf", n + 1
        return candidate

    # ----- runs --------------------------------------------------------------------------

    def create_run(
        self, slug: str, source_filename: str, config: dict[str, object] | None = None
    ) -> RunState:
        self.source_path(slug, source_filename)  # the run must point at a real source
        runs_dir = self._existing_study_dir(slug) / RUNS_DIR
        now = _now()
        run_id = f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
        run_dir = ensure_within(self.root, runs_dir / run_id)
        run_dir.mkdir(parents=True)
        if config is not None:
            write_json(run_dir / RUN_CONFIG_FILE, config)
        state = RunState(
            run_id=run_id, source_filename=source_filename, created_at=now, updated_at=now
        )
        write_model(run_dir / RUN_STATE_FILE, state)
        log.info("run created", extra={"study": slug, "run": run_id})
        return state

    def update_run(self, slug: str, run_id: str, mutate: Callable[[RunState], None]) -> RunState:
        """Read-modify-write run_state.json under the study lock."""
        with self._locks[slug]:
            state = self.get_run(slug, run_id)
            mutate(state)
            state.updated_at = _now()
            write_model(self.run_dir(slug, run_id) / RUN_STATE_FILE, state)
            return state

    def mark_interrupted_runs(self) -> int:
        """A run left active on disk was interrupted by a server stop; say so instead of lying."""
        count = 0
        for study in self.list_studies():
            for run in study.runs:
                if run.status not in (RunStatus.RUNNING, RunStatus.GENERATING):
                    continue

                def fail(state: RunState) -> None:
                    state.status = RunStatus.FAILED
                    for stage in state.stages.values():
                        if stage.status == StageStatus.RUNNING:
                            stage.status = StageStatus.FAILED
                            stage.error = "interrupted: the server stopped while this stage ran"
                            stage.finished_at = _now()

                self.update_run(study.slug, run.run_id, fail)
                count += 1
        return count

    def get_run(self, slug: str, run_id: str) -> RunState:
        path = self.run_dir(slug, run_id) / RUN_STATE_FILE
        if not path.is_file():
            raise RunNotFoundError(run_id)
        return RunState.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self, slug: str) -> list[RunState]:
        runs_dir = self._existing_study_dir(slug) / RUNS_DIR
        if not runs_dir.is_dir():
            return []
        runs: list[RunState] = []
        for child in runs_dir.iterdir():
            if child.is_dir() and RUN_ID_RE.fullmatch(child.name):
                try:
                    runs.append(self.get_run(slug, child.name))
                except (RunNotFoundError, ValidationError, ValueError):
                    log.warning("skipping unreadable run", extra={"study": slug, "run": child.name})
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

"""Stage B runner: a confirmed review becomes the workbook, or nothing is written.

The gate is strict. The review must be confirmed, and re-validating it now must find no blocking
issue: required values, exact terminology, importer-readable formats and a clean reference graph
(no duplicate or missing names, no dangling references). A workbook is never written from
unreviewed extraction.

Outputs, inside the run folder:
    workbook/<study slug>.xlsx     the USDM workbook (legacy single-workbook dialect)
    workbook_report.json           what was written from which review revision, with warnings

Resumable: when the report says the workbook was written from the current review revision and the
file is unchanged, nothing is rewritten unless forced.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from backend.models.review import ReviewIssue, ReviewStatus
from backend.pipeline.review.service import ReviewService
from backend.pipeline.workbook.writer import write_workbook
from backend.storage.fs import write_model

WORKBOOK_DIR = "workbook"
REPORT_FILE = "workbook_report.json"


class WorkbookReport(BaseModel):
    generated_at: datetime
    file: str  # relative to the run folder
    sha256: str
    size_bytes: int
    review_revision: int
    review_confirmed_at: datetime | None
    ct_version: str
    stale_extraction: bool  # extraction was re-run after the review started
    sheets: dict[str, int]  # workbook sheet -> data rows written
    warnings: list[str] = Field(default_factory=list)
    reused: bool = False  # this request found the workbook already up to date


class WorkbookNotReadyError(RuntimeError):
    def __init__(self, reasons: list[str], issues: list[ReviewIssue] | None = None) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.issues = issues or []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_report(run_dir: Path) -> WorkbookReport | None:
    path = run_dir / REPORT_FILE
    if not path.is_file():
        return None
    return WorkbookReport.model_validate_json(path.read_text(encoding="utf-8"))


def workbook_path(run_dir: Path, slug: str) -> Path:
    return run_dir / WORKBOOK_DIR / f"{slug}.xlsx"


def generate_workbook(
    run_dir: Path, slug: str, review: ReviewService, force: bool = False
) -> WorkbookReport:
    document = review.document()
    if document is None:
        raise WorkbookNotReadyError(["the extraction has not been reviewed yet"])
    if document.status != ReviewStatus.CONFIRMED:
        raise WorkbookNotReadyError(["the review is not confirmed"])
    state = review.state()
    blocking = [i for i in state.validation.issues if i.severity.value == "blocking"]
    if blocking:
        raise WorkbookNotReadyError(
            [f"{len(blocking)} blocking issue(s) in the review; reopen it and resolve them"],
            blocking,
        )

    path = workbook_path(run_dir, slug)
    previous = load_report(run_dir)
    if (
        not force
        and previous is not None
        and previous.review_revision == document.revision
        and path.is_file()
        and _sha256(path) == previous.sha256
    ):
        return previous.model_copy(update={"reused": True})

    summary = write_workbook(document.sheets, path, document.confirmed_at)
    report = WorkbookReport(
        generated_at=datetime.now(UTC),
        file=path.relative_to(run_dir).as_posix(),
        sha256=_sha256(path),
        size_bytes=path.stat().st_size,
        review_revision=document.revision,
        review_confirmed_at=document.confirmed_at,
        ct_version=document.ct_version,
        stale_extraction=state.stale,
        sheets=summary.sheets,
        warnings=summary.warnings,
    )
    write_model(run_dir / REPORT_FILE, report)
    return report

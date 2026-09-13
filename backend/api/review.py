"""Review routes: the reviewed model, edit operations, confirmation, audit, source highlights,
controlled-terminology lookup for the review page's pickers, and the outputs made from a confirmed
review (Stage B workbook, Stage C USDM JSON with its validation report)."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from backend.api.runs import Jobs
from backend.api.studies import Store
from backend.models.extraction import TermCandidate, TerminologyResolution
from backend.models.review import (
    AuditEntry,
    OperationsRequest,
    ReviewState,
    ReviewStatus,
    RevisionRequest,
    SheetLayoutOut,
)
from backend.models.run_config import RunConfig
from backend.models.study import RunState, RunStatus, StageName, StageState, StageStatus
from backend.pipeline.jobs import RunAlreadyActiveError
from backend.pipeline.review.highlight import render_highlight
from backend.pipeline.review.service import (
    ReviewBlockedError,
    ReviewOperationError,
    ReviewService,
    ReviewUnavailableError,
    RevisionConflictError,
    layouts,
)
from backend.pipeline.terminology.ct import CodelistNotConfiguredError, CtField, get_ct_resolver
from backend.pipeline.usdm_gen.stage import REPORT_FILE as USDM_REPORT_FILE
from backend.pipeline.usdm_gen.stage import UsdmNotReadyError, UsdmReport, usdm_path
from backend.pipeline.usdm_gen.stage import load_report as load_usdm_report
from backend.pipeline.workbook.stage import (
    WorkbookNotReadyError,
    WorkbookReport,
    generate_workbook,
    load_report,
    workbook_path,
)
from backend.storage.errors import RunNotFoundError, SourceNotFoundError, StudyNotFoundError
from backend.storage.studies import RUN_CONFIG_FILE, StudyStore

router = APIRouter(prefix="/api/studies/{slug}/runs/{run_id}", tags=["review"])
terminology_router = APIRouter(prefix="/api/terminology", tags=["terminology"])
workbook_router = APIRouter(prefix="/api/workbook", tags=["workbook"])


def _run_dir(store: StudyStore, slug: str, run_id: str) -> Path:
    try:
        return store.run_dir(slug, run_id)
    except (StudyNotFoundError, RunNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found") from None


def _service(store: StudyStore, slug: str, run_id: str) -> ReviewService:
    run_dir = _run_dir(store, slug, run_id)
    config = RunConfig.model_validate_json((run_dir / RUN_CONFIG_FILE).read_text(encoding="utf-8"))
    return ReviewService(run_dir, get_ct_resolver(), config.confidence_threshold)


def _set_run_status(store: StudyStore, slug: str, run_id: str, new: RunStatus) -> None:
    def apply(state: RunState) -> None:
        # Never overwrite an active stage's status; the review outcome is recorded once it ends.
        if state.status not in (RunStatus.RUNNING, RunStatus.GENERATING):
            state.status = new

    store.update_run(slug, run_id, apply)


def _state(service: ReviewService) -> ReviewState:
    try:
        return service.state()
    except ReviewUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.get("/review", response_model=ReviewState)
def get_review(slug: str, run_id: str, store: Store) -> ReviewState:
    return _state(_service(store, slug, run_id))


@router.post("/review/operations", response_model=ReviewState)
def apply_operations(slug: str, run_id: str, body: OperationsRequest, store: Store) -> ReviewState:
    service = _service(store, slug, run_id)
    try:
        doc = service.apply(body.base_revision, body.operations)
    except RevisionConflictError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"message": str(exc), "current_revision": exc.current}
        ) from None
    except ReviewOperationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except ReviewUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    if doc.status == "draft":
        _set_run_status(store, slug, run_id, RunStatus.AWAITING_REVIEW)
    return _state(service)


@router.post("/review/confirm", response_model=ReviewState)
def confirm_review(slug: str, run_id: str, body: RevisionRequest, store: Store) -> ReviewState:
    service = _service(store, slug, run_id)
    try:
        service.confirm(body.base_revision)
    except RevisionConflictError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"message": str(exc), "current_revision": exc.current}
        ) from None
    except ReviewBlockedError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except ReviewUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    _set_run_status(store, slug, run_id, RunStatus.REVIEWED)
    return _state(service)


@router.post("/review/restart", response_model=ReviewState)
def restart_review(slug: str, run_id: str, body: RevisionRequest, store: Store) -> ReviewState:
    """Discard the current review (archived, audited) and start again from the latest extraction."""
    service = _service(store, slug, run_id)
    try:
        service.restart(body.base_revision)
    except RevisionConflictError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"message": str(exc), "current_revision": exc.current}
        ) from None
    except ReviewUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    _set_run_status(store, slug, run_id, RunStatus.AWAITING_REVIEW)
    return _state(service)


@router.get("/review/audit", response_model=list[AuditEntry])
def get_audit(
    slug: str, run_id: str, store: Store, limit: int = Query(default=200, ge=1, le=5000)
) -> list[AuditEntry]:
    return _service(store, slug, run_id).audit(limit)


@router.get("/source-highlight")
def source_highlight(
    slug: str,
    run_id: str,
    store: Store,
    page: int = Query(ge=1),
    quote: str | None = Query(default=None, max_length=2000),
) -> Response:
    run_dir = _run_dir(store, slug, run_id)
    config = RunConfig.model_validate_json((run_dir / RUN_CONFIG_FILE).read_text(encoding="utf-8"))
    try:
        pdf = store.source_path(slug, config.source_filename)
        png, found = render_highlight(pdf, page, quote)
    except SourceNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="source PDF not found") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return Response(
        content=png,
        media_type="image/png",
        headers={"X-Quote-Found": "true" if found else "false", "Cache-Control": "max-age=300"},
    )


# ----- terminology ------------------------------------------------------------------------------


class CodelistOut(BaseModel):
    codelist: str
    codelist_name: str
    ct_version: str
    extensible: bool
    terms: list[TermCandidate]


class ResolveRequest(BaseModel):
    klass: str
    attribute: str
    phrase: str


def _field(klass: str, attribute: str) -> CtField:
    field = CtField(klass, attribute)
    try:
        get_ct_resolver().codelist(field)
    except CodelistNotConfiguredError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"no codelist for {klass}.{attribute}"
        ) from None
    return field


@router.post("/workbook", response_model=WorkbookReport)
def create_workbook(
    slug: str, run_id: str, store: Store, jobs: Jobs, force: bool = False
) -> WorkbookReport:
    """Stage B: write the USDM workbook from the confirmed review (409 with reasons if not)."""
    if jobs.is_active(slug, run_id):
        # The USDM import reads the workbook; never rewrite it underneath.
        raise HTTPException(status.HTTP_409_CONFLICT, detail="the run is busy; try again shortly")
    return _write_workbook(store, slug, run_id, force)


def _write_workbook(store: StudyStore, slug: str, run_id: str, force: bool) -> WorkbookReport:
    service = _service(store, slug, run_id)
    started = datetime.now(UTC)
    try:
        report = generate_workbook(service.run_dir, slug, service, force=force)
    except WorkbookNotReadyError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "reasons": exc.reasons,
                "issues": [i.model_dump(mode="json") for i in exc.issues[:50]],
            },
        ) from None

    def record(state: RunState) -> None:
        current = state.stages.get(StageName.WORKBOOK)
        if report.reused and current is not None and current.status == StageStatus.DONE:
            return  # already recorded as written; asking again changed nothing
        state.stages[StageName.WORKBOOK] = StageState(
            status=StageStatus.SKIPPED if report.reused else StageStatus.DONE,
            started_at=started,
            finished_at=datetime.now(UTC),
            detail=f"{len(report.sheets)} sheets, review revision {report.review_revision}"
            + (f", {len(report.warnings)} warning(s)" if report.warnings else ""),
        )

    store.update_run(slug, run_id, record)
    return report


@router.get("/workbook", response_model=WorkbookReport)
def get_workbook_report(slug: str, run_id: str, store: Store) -> WorkbookReport:
    report = load_report(_run_dir(store, slug, run_id))
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no workbook generated yet")
    return report


@router.get("/workbook/download")
def download_workbook(slug: str, run_id: str, store: Store) -> FileResponse:
    path = workbook_path(_run_dir(store, slug, run_id), slug)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no workbook generated yet")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


class UsdmResult(BaseModel):
    report: UsdmReport
    # Why the JSON may no longer match the review: the workbook or the review changed since.
    stale: list[str]


@router.post("/usdm", response_model=RunState, status_code=status.HTTP_202_ACCEPTED)
def create_usdm(slug: str, run_id: str, store: Store, jobs: Jobs, force: bool = False) -> RunState:
    """Stage C in the background: bring the workbook up to date with the confirmed review (the same
    gate as Stage B), import it into USDM JSON with usdm4-excel and validate the result."""
    if jobs.is_active(slug, run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="the run is already processing")
    _write_workbook(store, slug, run_id, force=False)
    try:
        jobs.start_usdm(slug, run_id, force=force)
    except RunAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except UsdmNotReadyError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"message": str(exc), "reasons": exc.reasons}
        ) from None
    return store.get_run(slug, run_id)


@router.get("/usdm", response_model=UsdmResult)
def get_usdm(slug: str, run_id: str, store: Store) -> UsdmResult:
    run_dir = _run_dir(store, slug, run_id)
    report = load_usdm_report(run_dir)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no USDM generated yet")
    stale: list[str] = []
    workbook = load_report(run_dir)
    if workbook is None or workbook.sha256 != report.workbook_sha256:
        stale.append("the workbook was regenerated after this USDM was produced")
    document = _service(store, slug, run_id).document()
    if document is None or document.revision != report.review_revision:
        stale.append(
            "the review has changed since (revision "
            f"{document.revision if document else '?'}, this USDM is from revision "
            f"{report.review_revision})"
        )
    elif document.status != ReviewStatus.CONFIRMED:
        stale.append("the review was reopened after this USDM was produced")
    return UsdmResult(report=report, stale=stale)


@router.get("/usdm/download")
def download_usdm(slug: str, run_id: str, store: Store) -> FileResponse:
    path = usdm_path(_run_dir(store, slug, run_id), slug)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no USDM generated yet")
    return FileResponse(path, media_type="application/json", filename=path.name, headers=_DOWNLOAD)


@router.get("/usdm/report/download")
def download_usdm_report(slug: str, run_id: str, store: Store) -> FileResponse:
    path = _run_dir(store, slug, run_id) / USDM_REPORT_FILE
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no USDM generated yet")
    return FileResponse(
        path,
        media_type="application/json",
        filename=f"{slug}-usdm-validation.json",
        headers=_DOWNLOAD,
    )


# A FileResponse with only Last-Modified is cached heuristically by the browser.
_DOWNLOAD = {"Cache-Control": "no-store"}


@terminology_router.get("/codelist", response_model=CodelistOut)
def get_codelist(
    klass: str, attribute: str, q: str | None = Query(default=None, max_length=200)
) -> CodelistOut:
    resolver = get_ct_resolver()
    field = _field(klass, attribute)
    codelist = resolver.codelist(field)
    return CodelistOut(
        codelist=codelist["conceptId"],
        codelist_name=codelist.get("name") or "",
        ct_version=(codelist.get("source") or {}).get("effective_date") or resolver.version,
        extensible=bool(codelist.get("extensible")),
        terms=resolver.terms(field, q),
    )


@terminology_router.post("/resolve", response_model=TerminologyResolution | None)
def resolve_phrase(body: ResolveRequest) -> TerminologyResolution | None:
    return get_ct_resolver().resolve(body.phrase, _field(body.klass, body.attribute))


@terminology_router.get("/biomedical-concepts", response_model=CodelistOut)
def get_biomedical_concepts(q: str | None = Query(default=None, max_length=200)) -> CodelistOut:
    """Search the bundled CDISC Biomedical Concept catalogue by name or synonym."""
    from backend.pipeline.terminology.bc import CODELIST, CODELIST_NAME

    resolver = get_ct_resolver()
    return CodelistOut(
        codelist=CODELIST,
        codelist_name=CODELIST_NAME,
        ct_version=resolver.version,
        extensible=True,
        terms=resolver.bcs.search(q),
    )


@terminology_router.post("/resolve-biomedical-concept", response_model=TerminologyResolution | None)
def resolve_biomedical_concept(body: ResolveRequest) -> TerminologyResolution | None:
    return get_ct_resolver().bcs.resolve(body.phrase)


@workbook_router.get("/layouts", response_model=list[SheetLayoutOut])
def get_layouts() -> list[SheetLayoutOut]:
    """Every workbook sheet the pipeline fills, with columns, formats and references."""
    return layouts()

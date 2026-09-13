"""Review routes: the reviewed model, edit operations, confirmation, audit, source highlights,
and controlled-terminology lookup for the review page's pickers."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel

from backend.api.studies import Store
from backend.models.extraction import TermCandidate, TerminologyResolution
from backend.models.review import AuditEntry, OperationsRequest, ReviewState, RevisionRequest
from backend.models.run_config import RunConfig
from backend.models.study import RunState, RunStatus
from backend.pipeline.review.highlight import render_highlight
from backend.pipeline.review.service import (
    ReviewBlockedError,
    ReviewOperationError,
    ReviewService,
    ReviewUnavailableError,
    RevisionConflictError,
)
from backend.pipeline.terminology.ct import CodelistNotConfiguredError, CtField, get_ct_resolver
from backend.storage.errors import RunNotFoundError, SourceNotFoundError, StudyNotFoundError
from backend.storage.studies import RUN_CONFIG_FILE, StudyStore

router = APIRouter(prefix="/api/studies/{slug}/runs/{run_id}", tags=["review"])
terminology_router = APIRouter(prefix="/api/terminology", tags=["terminology"])


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
        if state.status != RunStatus.RUNNING:
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

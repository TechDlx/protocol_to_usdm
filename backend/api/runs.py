"""Run routes: create a run for a source PDF, track its stages, and read its artefacts."""

import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.api.studies import Store
from backend.models.document import ParsedDocument
from backend.models.run_config import RunConfig
from backend.models.segmentation import (
    AgentInputChange,
    M11TemplateSectionOut,
    SectionMapping,
    SectionOverrideRequest,
)
from backend.models.study import RunState, StageName
from backend.pipeline.agents.registry import AGENTS
from backend.pipeline.extract import (
    EXTRACTION_FILE,
    PROVENANCE_FILE,
    REFERENCE_VALIDATION_FILE,
    changed_agent_inputs,
    load_extraction,
)
from backend.pipeline.extractors.registry import UnknownExtractorError, get_extractor
from backend.pipeline.ingest import (
    PAGE_IMAGES_DIR,
    PARSED_DOCUMENT_FILE,
    SECTION_MAPPING_FILE,
    load_parsed_document,
    segment,
)
from backend.pipeline.jobs import ExtractionNotReadyError, JobRunner, RunAlreadyActiveError
from backend.pipeline.segmentation.m11 import load_template
from backend.pipeline.segmentation.overrides import (
    OverrideError,
    audit_change,
    clear_override,
    set_override,
)
from backend.storage.errors import RunNotFoundError, SourceNotFoundError, StudyNotFoundError
from backend.storage.fs import ensure_within
from backend.storage.studies import RUN_CONFIG_FILE

router = APIRouter(prefix="/api/studies/{slug}/runs", tags=["runs"])

_PAGE_IMAGE = re.compile(r"^page-\d{4}\.png$")


def get_jobs(request: Request) -> JobRunner:
    jobs: JobRunner = request.app.state.jobs
    return jobs


Jobs = Annotated[JobRunner, Depends(get_jobs)]


class RunCreate(BaseModel):
    source_filename: str
    pdf_backend: str = "pymupdf"
    page_image_dpi: int = Field(default=150, ge=50, le=300)


def _run_dir(store: Store, slug: str, run_id: str) -> Path:
    try:
        return store.run_dir(slug, run_id)
    except (StudyNotFoundError, RunNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found") from None


def _artefact(store: Store, slug: str, run_id: str, name: str) -> FileResponse:
    path = _run_dir(store, slug, run_id) / name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{name} not produced yet")
    return FileResponse(path, media_type="application/json")


@router.get("", response_model=list[RunState])
def list_runs(slug: str, store: Store) -> list[RunState]:
    try:
        return store.list_runs(slug)
    except StudyNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="study not found") from None


@router.post("", response_model=RunState, status_code=status.HTTP_201_CREATED)
def create_run(slug: str, body: RunCreate, store: Store, jobs: Jobs) -> RunState:
    try:
        get_extractor(body.pdf_backend)
        config = RunConfig(
            source_filename=body.source_filename,
            pdf_backend=body.pdf_backend,
            page_image_dpi=body.page_image_dpi,
        )
        run = store.create_run(slug, body.source_filename, config.model_dump(mode="json"))
    except StudyNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="study not found") from None
    except SourceNotFoundError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="source PDF not found in this study"
        ) from None
    except UnknownExtractorError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    jobs.start_ingestion(slug, run.run_id)
    return store.get_run(slug, run.run_id)


@router.get("/{run_id}", response_model=RunState)
def get_run(slug: str, run_id: str, store: Store) -> RunState:
    _run_dir(store, slug, run_id)
    return store.get_run(slug, run_id)


@router.post("/{run_id}/ingest", response_model=RunState, status_code=status.HTTP_202_ACCEPTED)
def rerun_ingestion(
    slug: str, run_id: str, store: Store, jobs: Jobs, force: bool = False
) -> RunState:
    """Resume or repeat ingestion. Parsing is skipped if its output is current, unless forced."""
    _run_dir(store, slug, run_id)
    try:
        jobs.start_ingestion(slug, run_id, force=force)
    except RunAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return store.get_run(slug, run_id)


class ExtractRequest(BaseModel):
    sheets: list[str] | None = Field(
        default=None, description="Agent sheet keys to run; all agents when omitted."
    )
    force: bool = Field(
        default=False, description="Re-run agents even if their inputs are unchanged."
    )


@router.post("/{run_id}/extract", response_model=RunState, status_code=status.HTTP_202_ACCEPTED)
def start_extraction(
    slug: str, run_id: str, body: ExtractRequest, store: Store, jobs: Jobs
) -> RunState:
    """Run the extraction agents. This calls the Claude API and costs money."""
    _run_dir(store, slug, run_id)
    try:
        jobs.start_extraction(slug, run_id, sheets=body.sheets, force=body.force)
    except RunAlreadyActiveError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except ExtractionNotReadyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return store.get_run(slug, run_id)


@router.get("/{run_id}/extraction")
def get_extraction(slug: str, run_id: str, store: Store) -> FileResponse:
    return _artefact(store, slug, run_id, EXTRACTION_FILE)


@router.get("/{run_id}/provenance")
def get_provenance(slug: str, run_id: str, store: Store) -> FileResponse:
    return _artefact(store, slug, run_id, PROVENANCE_FILE)


@router.get("/{run_id}/reference-validation")
def get_reference_validation(slug: str, run_id: str, store: Store) -> FileResponse:
    return _artefact(store, slug, run_id, REFERENCE_VALIDATION_FILE)


@router.get("/{run_id}/document")
def get_document(slug: str, run_id: str, store: Store) -> FileResponse:
    return _artefact(store, slug, run_id, PARSED_DOCUMENT_FILE)


@router.get("/{run_id}/section-mapping")
def get_section_mapping(slug: str, run_id: str, store: Store) -> FileResponse:
    return _artefact(store, slug, run_id, SECTION_MAPPING_FILE)


def _remap(store: Store, jobs: JobRunner, slug: str, run_id: str) -> tuple[Path, ParsedDocument]:
    """Checks before a mapping change: the run is idle and parsed."""
    run_dir = _run_dir(store, slug, run_id)
    if jobs.is_active(slug, run_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="the run is processing; change the mapping when it ends",
        )
    document = load_parsed_document(run_dir)
    if document is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="the protocol has not been parsed yet")
    return run_dir, document


def _resegment(
    store: Store,
    slug: str,
    run_id: str,
    run_dir: Path,
    document: ParsedDocument,
    action: str,
    section_id: str,
) -> SectionMapping:
    """Re-map with the current overrides, audit the section's change, refresh the stage note."""
    before_path = run_dir / SECTION_MAPPING_FILE
    before = (
        SectionMapping.model_validate_json(before_path.read_text(encoding="utf-8"))
        if before_path.is_file()
        else None
    )
    config = RunConfig.model_validate_json((run_dir / RUN_CONFIG_FILE).read_text(encoding="utf-8"))
    mapping = segment(run_dir, document, config)
    audit_change(run_dir, action, section_id, before, mapping)
    flagged = sum(a.needs_review for a in mapping.assignments)
    overridden = sum(a.reviewer_override for a in mapping.assignments)

    def detail(state: RunState) -> None:
        stage = state.stages.get(StageName.SEGMENT)
        if stage is not None:
            stage.detail = (
                f"{flagged} of {len(mapping.assignments)} sections flagged for review, "
                f"{overridden} mapped by a reviewer"
            )

    store.update_run(slug, run_id, detail)
    return mapping


@router.put("/{run_id}/section-mapping/{section_id}", response_model=SectionMapping)
def override_section_mapping(
    slug: str, run_id: str, section_id: str, body: SectionOverrideRequest, store: Store, jobs: Jobs
) -> SectionMapping:
    """Map a protocol section to an M11 section (or mark it as not protocol content) by hand.
    Kept across re-segmentation; extraction picks it up on its next run."""
    run_dir, document = _remap(store, jobs, slug, run_id)
    try:
        set_override(run_dir, document, section_id, body.m11_number, body.excluded)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="section not found") from None
    except OverrideError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return _resegment(store, slug, run_id, run_dir, document, "set", section_id)


@router.delete("/{run_id}/section-mapping/{section_id}", response_model=SectionMapping)
def clear_section_mapping(
    slug: str, run_id: str, section_id: str, store: Store, jobs: Jobs
) -> SectionMapping:
    """Return a section to the automatic mapping."""
    run_dir, document = _remap(store, jobs, slug, run_id)
    try:
        clear_override(run_dir, section_id)
    except KeyError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="this section has no reviewer mapping"
        ) from None
    return _resegment(store, slug, run_id, run_dir, document, "clear", section_id)


@router.get("/{run_id}/extraction/input-changes", response_model=list[AgentInputChange])
def extraction_input_changes(slug: str, run_id: str, store: Store) -> list[AgentInputChange]:
    """Agents whose protocol sections changed since extraction (after a mapping change)."""
    run_dir = _run_dir(store, slug, run_id)
    extraction = load_extraction(run_dir)
    document = load_parsed_document(run_dir)
    mapping_path = run_dir / SECTION_MAPPING_FILE
    if extraction is None or document is None or not mapping_path.is_file():
        return []
    mapping = SectionMapping.model_validate_json(mapping_path.read_text(encoding="utf-8"))
    return changed_agent_inputs(extraction, document, mapping)


@router.get("/{run_id}/pages/{filename}")
def get_page_image(slug: str, run_id: str, filename: str, store: Store) -> FileResponse:
    if not _PAGE_IMAGE.fullmatch(filename):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="page image not found")
    run_dir = _run_dir(store, slug, run_id)
    path = ensure_within(run_dir, run_dir / PAGE_IMAGES_DIR / filename)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="page image not found")
    return FileResponse(path, media_type="image/png")


agents_router = APIRouter(prefix="/api/agents", tags=["agents"])
m11_router = APIRouter(prefix="/api/m11", tags=["m11"])


@m11_router.get("/template", response_model=list[M11TemplateSectionOut])
def get_m11_template() -> list[M11TemplateSectionOut]:
    """The ICH M11 sections a protocol section can be mapped to, in template order."""
    return [
        M11TemplateSectionOut(number=s.number, title=s.title, level=s.level, optional=s.optional)
        for s in load_template().sections
    ]


class AgentInfo(BaseModel):
    sheet: str
    workbook_sheets: list[str]
    m11_sections: list[str]


@agents_router.get("", response_model=list[AgentInfo])
def list_agents() -> list[AgentInfo]:
    return [
        AgentInfo(
            sheet=a.sheet,
            workbook_sheets=list(a.workbook_sheets),
            m11_sections=list(a.m11_sections),
        )
        for a in AGENTS.values()
    ]

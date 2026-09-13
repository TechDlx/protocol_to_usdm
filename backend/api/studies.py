"""Study management routes: list/create studies, upload and fetch protocol PDFs."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from backend.models.study import SourceDocument, StudyCreate, StudyMeta, StudySummary
from backend.storage.errors import InvalidUploadError, SourceNotFoundError, StudyNotFoundError
from backend.storage.studies import StudyStore

router = APIRouter(prefix="/api/studies", tags=["studies"])


def get_store(request: Request) -> StudyStore:
    store: StudyStore = request.app.state.store
    return store


Store = Annotated[StudyStore, Depends(get_store)]


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


@router.get("", response_model=list[StudySummary])
def list_studies(store: Store) -> list[StudySummary]:
    return store.list_studies()


@router.post("", response_model=StudyMeta, status_code=status.HTTP_201_CREATED)
def create_study(body: StudyCreate, store: Store) -> StudyMeta:
    return store.create_study(body)


@router.get("/{slug}", response_model=StudySummary)
def get_study(slug: str, store: Store) -> StudySummary:
    try:
        meta = store.get_study(slug)
        return StudySummary(**meta.model_dump(), runs=store.list_runs(slug))
    except StudyNotFoundError:
        raise _not_found("study") from None


# Sync handler on purpose: FastAPI runs it in a worker thread, so streaming a large PDF to
# disk and opening it with PyMuPDF never blocks the event loop.
@router.post("/{slug}/sources", response_model=SourceDocument, status_code=status.HTTP_201_CREATED)
def upload_source(slug: str, file: UploadFile, store: Store) -> SourceDocument:
    try:
        return store.add_source(slug, file.filename or "protocol.pdf", file.file)
    except StudyNotFoundError:
        raise _not_found("study") from None
    except InvalidUploadError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


@router.get("/{slug}/sources/{filename}")
def get_source(slug: str, filename: str, store: Store) -> FileResponse:
    try:
        path = store.source_path(slug, filename)
    except (StudyNotFoundError, SourceNotFoundError):
        raise _not_found("source document") from None
    return FileResponse(path, media_type="application/pdf", filename=path.name)

"""Study, source-document and run metadata persisted in each study folder."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.extraction import AgentRun

SCHEMA_VERSION = 1


class StudyCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    sponsor: str = Field(default="", max_length=200)
    protocol_identifier: str = Field(default="", max_length=100)


class SourceDocument(BaseModel):
    filename: str
    # Relative to the study folder, so a study folder can be moved without breaking.
    relative_path: str
    sha256: str
    size_bytes: int
    page_count: int
    uploaded_at: datetime


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PARSED = "parsed"  # ingestion + segmentation done; extraction not yet run
    AWAITING_REVIEW = "awaiting_review"
    REVIEWED = "reviewed"  # review confirmed; ready for workbook generation
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class StageName(StrEnum):
    INGEST = "ingest"  # PDF -> parsed_document.json + page_images/
    SEGMENT = "segment"  # parsed document -> section_mapping.json
    EXTRACT = "extract"  # agents -> extraction.json, provenance.json, reference_validation.json


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"  # output already on disk for identical inputs (resumed run)
    FAILED = "failed"


class StageState(BaseModel):
    status: StageStatus = StageStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    detail: str | None = None


class RunState(BaseModel):
    run_id: str
    status: RunStatus = RunStatus.CREATED
    source_filename: str | None = None
    created_at: datetime
    updated_at: datetime
    stages: dict[StageName, StageState] = Field(default_factory=dict)
    agents: dict[str, AgentRun] = Field(default_factory=dict)


class StudyMeta(BaseModel):
    schema_version: int = SCHEMA_VERSION
    slug: str
    name: str
    sponsor: str = ""
    protocol_identifier: str = ""
    created_at: datetime
    updated_at: datetime
    sources: list[SourceDocument] = Field(default_factory=list)

    @field_validator("sources")
    @classmethod
    def _unique_filenames(cls, sources: list[SourceDocument]) -> list[SourceDocument]:
        names = [s.filename for s in sources]
        if len(names) != len(set(names)):
            raise ValueError("duplicate source filenames in study.json")
        return sources


class StudySummary(StudyMeta):
    """A study as listed in the UI: its metadata plus the runs found on disk."""

    runs: list[RunState] = Field(default_factory=list)

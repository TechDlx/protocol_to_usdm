"""Per-run settings persisted as runs/<run-id>/run_config.json.

Only settings the implemented stages actually use are defined. The run configuration page adds
the rest (models, CT versions, confidence threshold, concurrency...) as those stages arrive.
"""

from pydantic import BaseModel, Field

from backend.pipeline.llm import DEFAULT_EXTRACTION_MODEL
from backend.pipeline.segmentation.m11 import DEFAULT_REVIEW_THRESHOLD

RUN_CONFIG_SCHEMA_VERSION = 1


class RunConfig(BaseModel):
    schema_version: int = RUN_CONFIG_SCHEMA_VERSION
    source_filename: str
    pdf_backend: str = "pymupdf"
    # 150 DPI keeps body text legible for vision models while a 125-page protocol stays ~50 MB.
    page_image_dpi: int = Field(default=150, ge=50, le=300)
    segmentation_review_threshold: float = Field(default=DEFAULT_REVIEW_THRESHOLD, ge=0, le=1)

    extraction_model: str = DEFAULT_EXTRACTION_MODEL
    extraction_effort: str | None = None  # None = the model's default effort
    concurrency_limit: int = Field(default=5, ge=1, le=32)
    # Extracted values below this confidence are force-flagged for review.
    confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    # CDISC CT package version, pinned when extraction first runs. A later run against a different
    # CT version is refused rather than silently mixing terminology releases.
    ct_version: str | None = None

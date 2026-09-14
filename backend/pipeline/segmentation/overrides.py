"""Reviewer overrides of the section mapping: stored, audited, applied on every segmentation.

Files, inside the run folder:
    section_overrides.json          the reviewer's mapping per section (survives re-segmentation)
    section_mapping_audit.jsonl     append-only: who-changed-what, with old and new mapping
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from backend.models.document import ParsedDocument
from backend.models.segmentation import (
    SectionAssignment,
    SectionMapping,
    SectionOverride,
    SectionOverrides,
)
from backend.pipeline.segmentation.m11 import load_template
from backend.storage.fs import write_model

OVERRIDES_FILE = "section_overrides.json"
AUDIT_FILE = "section_mapping_audit.jsonl"


class OverrideError(ValueError):
    """The requested override is not valid (unknown M11 section, contradictory request)."""


def load_overrides(run_dir: Path) -> SectionOverrides:
    path = run_dir / OVERRIDES_FILE
    if not path.is_file():
        return SectionOverrides()
    return SectionOverrides.model_validate_json(path.read_text(encoding="utf-8"))


def _describe(assignment: SectionAssignment | None) -> dict[str, object]:
    if assignment is None:
        return {}
    return {
        "m11_number": assignment.m11_number,
        "m11_title": assignment.m11_title,
        "method": assignment.method.value,
        "confidence": assignment.confidence,
    }


def _audit(
    run_dir: Path,
    action: str,
    section_id: str,
    doc_title: str,
    before: SectionAssignment | None,
    after: SectionAssignment | None,
) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "section_id": section_id,
        "doc_title": doc_title,
        "old": _describe(before),
        "new": _describe(after),
    }
    with (run_dir / AUDIT_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def set_override(
    run_dir: Path,
    document: ParsedDocument,
    section_id: str,
    m11_number: str | None,
    excluded: bool,
) -> SectionOverrides:
    """Record the reviewer's mapping of a section. Raises KeyError for an unknown section."""
    section = next((s for s in document.sections if s.id == section_id), None)
    if section is None:
        raise KeyError(section_id)
    if excluded == (m11_number is not None):
        raise OverrideError("give an M11 section number, or mark the section as excluded")
    if m11_number is not None:
        try:
            load_template().get(m11_number)
        except KeyError:
            raise OverrideError(f"'{m11_number}' is not a section of the M11 template") from None
    overrides = load_overrides(run_dir)
    overrides.overrides[section_id] = SectionOverride(
        section_id=section_id,
        doc_title=section.title,
        m11_number=m11_number,
        excluded=excluded,
        updated_at=datetime.now(UTC),
    )
    write_model(run_dir / OVERRIDES_FILE, overrides)
    return overrides


def clear_override(run_dir: Path, section_id: str) -> SectionOverrides:
    """Return a section to the computed mapping. Raises KeyError when it has no override."""
    overrides = load_overrides(run_dir)
    del overrides.overrides[section_id]
    write_model(run_dir / OVERRIDES_FILE, overrides)
    return overrides


def audit_change(
    run_dir: Path,
    action: str,
    section_id: str,
    before: SectionMapping | None,
    after: SectionMapping,
) -> None:
    old = (
        next((a for a in before.assignments if a.section_id == section_id), None)
        if before
        else None
    )
    new = after.assignment(section_id)
    _audit(run_dir, action, section_id, new.doc_title, old, new)

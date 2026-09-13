"""The review working copy (reviewed.json) and its append-only audit trail (review_audit.jsonl).

A review starts as a copy of extraction.json. Reviewers change it through operations (set a value,
add/delete/move a row, accept a flagged value); each saved change increments the revision and
appends one audit line with the workbook cell, old and new value. Clients send the revision they
edited, so two tabs cannot silently overwrite each other.

Controlled-terminology values are re-resolved on the server for every change: a reviewer either
picks a code from the field's codelist, or types a phrase that is validated like extracted text.
"""

import json
import threading
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.models.extraction import (
    ExtractedField,
    Extraction,
    Provenance,
    ValueOrigin,
)
from backend.models.review import (
    AcceptValue,
    AddRow,
    AuditEntry,
    ColumnOut,
    DeleteRow,
    MoveRow,
    ReviewDocument,
    ReviewOperation,
    ReviewState,
    ReviewStatus,
    ReviewValidation,
    SetValue,
    SheetLayoutOut,
)
from backend.pipeline.extract import load_extraction
from backend.pipeline.identifiers.names import NameRegistry, criterion_name
from backend.pipeline.review.validation import validate_review
from backend.pipeline.terminology.ct import CtResolver
from backend.pipeline.workbook.cells import resolve_cell
from backend.pipeline.workbook.formats import FORMAT_HINTS
from backend.pipeline.workbook.layout import ELIGIBILITY, SHEETS, SheetKind, SheetSpec
from backend.pipeline.workbook.sources import empty_record, ensure_rows, sheet_rows
from backend.storage.fs import write_model

REVIEWED_FILE = "reviewed.json"
AUDIT_FILE = "review_audit.jsonl"
ARCHIVE_DIR = "review_archive"
LOCAL_ACTOR = "local-user"  # single-user local app; no authentication (Phase 0 decision)

_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


class ReviewUnavailableError(RuntimeError):
    """No extraction to review yet."""


class RevisionConflictError(RuntimeError):
    def __init__(self, current: int) -> None:
        super().__init__(f"the review changed since you loaded it (now revision {current}); reload")
        self.current = current


class ReviewOperationError(ValueError):
    pass


class ReviewBlockedError(RuntimeError):
    def __init__(self, validation: ReviewValidation) -> None:
        super().__init__(
            f"{validation.blocking} blocking issue(s) must be resolved before confirming"
        )
        self.validation = validation


def _now() -> datetime:
    return datetime.now(UTC)


def layouts() -> list[SheetLayoutOut]:
    return [
        SheetLayoutOut(
            key=spec.key,
            workbook_sheet=spec.workbook_sheet,
            title=spec.title,
            kind=spec.kind.value,
            source=spec.source,
            first_row=spec.first_row,
            leading_group=spec.leading_group,
            columns=[
                ColumnOut(
                    letter=letter,
                    header=c.header,
                    field=c.field,
                    required=c.required,
                    multiline=c.multiline,
                    ct_klass=c.ct.klass if c.ct else None,
                    ct_attribute=c.ct.attribute if c.ct else None,
                    multi=c.multi,
                    other_allowed=c.other_allowed,
                    format=c.format.value if c.format else None,
                    format_hint=FORMAT_HINTS[c.format] if c.format else None,
                    choices=list(c.choices),
                    group=c.group,
                    entity=c.entity,
                    ref=list(c.ref),
                    ref_literals=list(c.ref_literals),
                    bc=c.bc,
                )
                for letter, c in zip(spec.letters(), spec.columns, strict=True)
            ],
        )
        for spec in SHEETS.values()
    ]


class ReviewService:
    def __init__(self, run_dir: Path, resolver: CtResolver, confidence_threshold: float) -> None:
        self.run_dir = run_dir
        self.resolver = resolver
        self.confidence_threshold = confidence_threshold
        self._lock = _locks[str(run_dir.resolve())]

    # ----- reading -----------------------------------------------------------------------------

    def _extraction(self) -> Extraction:
        extraction = load_extraction(self.run_dir)
        if extraction is None:
            raise ReviewUnavailableError("run extraction before reviewing")
        return extraction

    def _load(self) -> ReviewDocument | None:
        path = self.run_dir / REVIEWED_FILE
        if not path.is_file():
            return None
        return ReviewDocument.model_validate_json(path.read_text(encoding="utf-8"))

    def _new_document(self, extraction: Extraction, revision: int = 0) -> ReviewDocument:
        now = _now()
        doc = ReviewDocument(
            revision=revision,
            source_sha256=extraction.source_sha256,
            ct_version=extraction.ct_version,
            base_extraction_generated_at=extraction.generated_at,
            created_at=now,
            updated_at=now,
            sheets=extraction.sheets.model_copy(deep=True),
        )
        for spec in SHEETS.values():
            if spec.kind == SheetKind.TABLE:
                for row in sheet_rows(doc.sheets, spec):
                    row.row_id = self._next_row_id(doc, spec)
        return doc

    @staticmethod
    def _next_row_id(doc: ReviewDocument, spec: SheetSpec) -> str:
        seq = doc.row_seqs.get(spec.row_prefix, doc.next_row_seq)
        doc.row_seqs[spec.row_prefix] = seq + 1
        return f"{spec.row_prefix}-{seq}"

    def document(self) -> ReviewDocument | None:
        """The review document if a review has been started, without creating one."""
        with self._lock:
            return self._load()

    def open(self) -> ReviewDocument:
        """The review document, created from the extraction on first open."""
        with self._lock:
            doc = self._load()
            if doc is None:
                doc = self._new_document(self._extraction())
                write_model(self.run_dir / REVIEWED_FILE, doc)
            return doc

    def state(self) -> ReviewState:
        doc = self.open()
        extraction = load_extraction(self.run_dir)
        return ReviewState(
            document=doc,
            validation=validate_review(doc.sheets, self.confidence_threshold, self.resolver),
            stale=extraction is not None
            and extraction.generated_at != doc.base_extraction_generated_at,
            confidence_threshold=self.confidence_threshold,
            layouts=layouts(),
        )

    def audit(self, limit: int = 200) -> list[AuditEntry]:
        path = self.run_dir / AUDIT_FILE
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [AuditEntry.model_validate_json(line) for line in lines[-limit:] if line.strip()]

    # ----- writing -----------------------------------------------------------------------------

    def _save(self, doc: ReviewDocument, entries: list[AuditEntry]) -> None:
        doc.updated_at = _now()
        write_model(self.run_dir / REVIEWED_FILE, doc)
        # The audit trail is append-only: never rewritten, only extended.
        with (self.run_dir / AUDIT_FILE).open("a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(entry.model_dump_json() + "\n")

    def _entry(self, doc: ReviewDocument, action: str, **fields: Any) -> AuditEntry:
        return AuditEntry(
            ts=_now(), actor=LOCAL_ACTOR, action=action, revision=doc.revision, **fields
        )

    def apply(self, base_revision: int, operations: list[ReviewOperation]) -> ReviewDocument:
        with self._lock:
            doc = self._load()
            if doc is None:
                raise ReviewUnavailableError("open the review before editing it")
            if doc.revision != base_revision:
                raise RevisionConflictError(doc.revision)

            working = doc.model_copy(deep=True)
            working.revision += 1
            entries: list[AuditEntry] = []
            for operation in operations:
                entry = self._apply_one(working, operation)
                if entry is not None:
                    entries.append(entry)
            if not entries:
                return doc  # nothing actually changed: no new revision, no audit noise

            if working.status == ReviewStatus.CONFIRMED:
                working.status, working.confirmed_at = ReviewStatus.DRAFT, None
                entries.insert(
                    0, self._entry(working, "reopen", detail="edited after confirmation")
                )
            self._save(working, entries)
            return working

    def confirm(self, base_revision: int) -> ReviewDocument:
        with self._lock:
            doc = self._require(base_revision)
            validation = validate_review(doc.sheets, self.confidence_threshold, self.resolver)
            if validation.blocking:
                raise ReviewBlockedError(validation)
            doc.revision += 1
            doc.status, doc.confirmed_at = ReviewStatus.CONFIRMED, _now()
            self._save(
                doc,
                [
                    self._entry(
                        doc, "confirm", detail=f"{validation.warnings} warning(s) outstanding"
                    )
                ],
            )
            return doc

    def restart(self, base_revision: int) -> ReviewDocument:
        """Discard this review and restart from the latest extraction; the old one is archived."""
        with self._lock:
            old = self._require(base_revision)
            archive = self.run_dir / ARCHIVE_DIR
            archive.mkdir(exist_ok=True)
            stamp = _now().strftime("%Y%m%dT%H%M%SZ")
            write_model(archive / f"reviewed-r{old.revision}-{stamp}.json", old)
            doc = self._new_document(self._extraction(), revision=old.revision + 1)
            self._save(
                doc,
                [
                    self._entry(
                        doc,
                        "restart",
                        detail=f"revision {old.revision} archived to {ARCHIVE_DIR}",
                    )
                ],
            )
            return doc

    def _require(self, base_revision: int) -> ReviewDocument:
        doc = self._load()
        if doc is None:
            raise ReviewUnavailableError("open the review first")
        if doc.revision != base_revision:
            raise RevisionConflictError(doc.revision)
        return doc

    # ----- operations --------------------------------------------------------------------------

    def _spec(self, sheet: str) -> SheetSpec:
        spec = SHEETS.get(sheet)
        if spec is None:
            raise ReviewOperationError(f"unknown sheet {sheet!r}")
        return spec

    def _rows(self, doc: ReviewDocument, spec: SheetSpec, create: bool = False) -> list[Any]:
        if spec.kind == SheetKind.KEY_VALUE:
            raise ReviewOperationError(f"{spec.workbook_sheet} has no rows to add, delete or move")
        if not create:
            return sheet_rows(doc.sheets, spec)
        rows = ensure_rows(doc.sheets, spec)
        if rows is None:
            raise ReviewOperationError(f"there is no record to hold {spec.title.lower()} rows")
        return rows

    def _row(self, doc: ReviewDocument, spec: SheetSpec, row_id: str | None) -> tuple[Any, int]:
        if spec.kind == SheetKind.KEY_VALUE:
            records = sheet_rows(doc.sheets, spec)
            if not records:
                raise ReviewOperationError(f"there is no {spec.title.lower()} record")
            return records[0], 0
        rows = self._rows(doc, spec)
        for index, row in enumerate(rows):
            if row.row_id == row_id:
                return row, index
        raise ReviewOperationError(f"row {row_id!r} not found in {spec.workbook_sheet}")

    def _apply_one(self, doc: ReviewDocument, operation: ReviewOperation) -> AuditEntry | None:
        spec = self._spec(operation.sheet)
        if isinstance(operation, SetValue):
            return self._set(doc, spec, operation)
        if isinstance(operation, AcceptValue):
            return self._accept(doc, spec, operation)
        if isinstance(operation, AddRow):
            return self._add_row(doc, spec, operation)
        if isinstance(operation, DeleteRow):
            return self._delete_row(doc, spec, operation)
        return self._move_row(doc, spec, operation)

    def _editable(self, spec: SheetSpec, field: str) -> Any:
        try:
            column = spec.column(field)
        except KeyError:
            raise ReviewOperationError(
                f"{spec.workbook_sheet} has no editable field {field!r}"
            ) from None
        return column

    def _set(self, doc: ReviewDocument, spec: SheetSpec, op: SetValue) -> AuditEntry | None:
        column = self._editable(spec, op.field)
        row, index = self._row(doc, spec, op.row_id)
        old: ExtractedField[str] = getattr(row, op.field)
        value = (op.value or "").strip() or None
        old_code = old.terminology.code if old.terminology else None

        terminology = None
        if column.ct is None:
            if op.code is not None:
                raise ReviewOperationError(f"{column.header} is not a terminology field")
            if column.bc and value is not None:
                terminology = resolve_cell(column, value, self.resolver)
        elif op.code is not None and (column.multi or column.other_allowed):
            raise ReviewOperationError(
                f"{column.header} is resolved from its text; send the terms, not a code"
            )
        elif op.code is not None:
            terminology = self.resolver.by_code(op.code, column.ct)
            if terminology is None:
                raise ReviewOperationError(
                    f"{op.code} is not a term of the {column.header} codelist"
                )
            value = value or terminology.preferred_term
        elif value is not None:
            terminology = resolve_cell(column, value, self.resolver)
        new_code = terminology.code if terminology else None

        if value == old.value and new_code == old_code:
            return None

        previous = old.provenance
        setattr(
            row,
            op.field,
            ExtractedField(
                value=value,
                terminology=terminology,
                provenance=Provenance(
                    origin=ValueOrigin.HUMAN,
                    source_section_id=previous.source_section_id if previous else None,
                    source_page=previous.source_page if previous else None,
                    raw_phrase=previous.raw_phrase if previous else None,
                    confidence=1.0,
                    verified=False,
                    note="entered by the reviewer",
                ),
            ),
        )
        return self._entry(
            doc,
            "set",
            sheet=spec.key,
            workbook_sheet=spec.workbook_sheet,
            cell=spec.cell(op.field, index),
            row_id=op.row_id,
            field=op.field,
            old_value=old.value,
            new_value=value,
            old_code=old_code,
            new_code=new_code,
        )

    def _accept(self, doc: ReviewDocument, spec: SheetSpec, op: AcceptValue) -> AuditEntry | None:
        self._editable(spec, op.field)
        row, index = self._row(doc, spec, op.row_id)
        value: ExtractedField[str] = getattr(row, op.field)
        p = value.provenance
        if p is None or p.origin != ValueOrigin.EXTRACTED:
            raise ReviewOperationError("only extracted values can be accepted")
        if p.reviewer_accepted:
            return None
        p.reviewer_accepted = True
        return self._entry(
            doc,
            "accept",
            sheet=spec.key,
            workbook_sheet=spec.workbook_sheet,
            cell=spec.cell(op.field, index),
            row_id=op.row_id,
            field=op.field,
            old_value=value.value,
            new_value=value.value,
            detail=f"accepted at confidence {p.confidence:.2f}, verified={p.verified}",
        )

    def _add_row(self, doc: ReviewDocument, spec: SheetSpec, op: AddRow) -> AuditEntry:
        rows = self._rows(doc, spec, create=True)
        index = 0
        if op.after_row_id is not None:
            _, after = self._row(doc, spec, op.after_row_id)
            index = after + 1
        record: Any = empty_record(spec, row_id=self._next_row_id(doc, spec))
        placeholder = self._placeholder(doc, spec)
        if placeholder is not None:
            field, name = placeholder
            setattr(
                record,
                field,
                ExtractedField(
                    value=name,
                    provenance=Provenance(
                        origin=ValueOrigin.DERIVED,
                        confidence=1.0,
                        verified=True,
                        note="placeholder name",
                    ),
                ),
            )
        rows.insert(index, record)
        return self._entry(
            doc,
            "add_row",
            sheet=spec.key,
            workbook_sheet=spec.workbook_sheet,
            cell=f"{spec.workbook_sheet}!{spec.row_number(index)}:{spec.row_number(index)}",
            row_id=record.row_id,
            new_value=placeholder[1] if placeholder else None,
        )

    @staticmethod
    def _placeholder(doc: ReviewDocument, spec: SheetSpec) -> tuple[str, str] | None:
        """A unique placeholder for a new row's name, keeping the reference graph valid.

        Two-level sheets get none: a new row there continues the entry above until the reviewer
        starts a new one.
        """
        column = next((c for c in spec.columns if c.entity and not c.group and c.field), None)
        if column is None or column.field is None or column.entity is None:
            return None
        kind = column.entity
        taken = {
            getattr(existing, c.field).value
            for sheet_spec in SHEETS.values()
            for c in sheet_spec.columns
            if c.entity == kind and c.field
            for existing in sheet_rows(doc.sheets, sheet_spec)
            if getattr(existing, c.field).value
        }
        if spec.key == ELIGIBILITY.key:
            n = 1
            while criterion_name(None, n) in taken:
                n += 1
            return column.field, criterion_name(None, n)
        names = NameRegistry()
        for name in taken:
            names.claim(name)
        singular = spec.title.lower().removesuffix("s")
        return column.field, names.claim(f"New {singular}")

    def _delete_row(self, doc: ReviewDocument, spec: SheetSpec, op: DeleteRow) -> AuditEntry:
        rows = self._rows(doc, spec)
        row, index = self._row(doc, spec, op.row_id)
        snapshot = {
            c.header: getattr(row, c.field).value for c in spec.columns if c.field is not None
        }
        rows.pop(index)
        return self._entry(
            doc,
            "delete_row",
            sheet=spec.key,
            workbook_sheet=spec.workbook_sheet,
            cell=f"{spec.workbook_sheet}!{spec.row_number(index)}:{spec.row_number(index)}",
            row_id=op.row_id,
            old_value=json.dumps(snapshot, ensure_ascii=False),
        )

    def _move_row(self, doc: ReviewDocument, spec: SheetSpec, op: MoveRow) -> AuditEntry | None:
        rows = self._rows(doc, spec)
        _, index = self._row(doc, spec, op.row_id)
        target = min(op.to_index, len(rows) - 1)
        if target == index:
            return None
        rows.insert(target, rows.pop(index))
        return self._entry(
            doc,
            "move_row",
            sheet=spec.key,
            workbook_sheet=spec.workbook_sheet,
            cell=f"{spec.workbook_sheet}!{spec.row_number(target)}:{spec.row_number(target)}",
            row_id=op.row_id,
            detail=f"moved from row {spec.row_number(index)} to row {spec.row_number(target)}",
        )

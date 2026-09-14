"""Reference graph validation over the intermediate model.

Built from the workbook layouts: a column marked `entity` names an entity of that kind, a column
marked `ref` refers to one by name. usdm4's importer keys cross-references by class and exact name,
so names must be unique within a kind and references must match a name of an allowed kind exactly
(including case). Duplicate or missing names and dangling references are reported with anchors
precise enough for the review page to jump to the cell. Stage B refuses to write a workbook while
any issue remains.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from backend.models.extraction import (
    ExtractionSheets,
    ReferenceAnchor,
    ReferenceIssue,
    ReferenceIssueKind,
    ReferenceValidation,
)
from backend.pipeline.workbook.layout import SHEETS, SheetKind, SheetSpec
from backend.pipeline.workbook.sources import group_active, sheet_rows


@dataclass
class _Reference:
    target: str
    kinds: tuple[str, ...]
    location: str
    anchor: ReferenceAnchor | None


@dataclass
class ReferenceGraph:
    #: (kind, name) -> locations
    entities: dict[tuple[str, str], list[str]] = field(default_factory=lambda: defaultdict(list))
    anchors: dict[tuple[str, str], list[ReferenceAnchor]] = field(
        default_factory=lambda: defaultdict(list)
    )
    references: list[_Reference] = field(default_factory=list)
    missing: list[tuple[str, ReferenceAnchor | None]] = field(default_factory=list)

    def entity(
        self,
        kind: str,
        name: str | None,
        location: str,
        anchor: ReferenceAnchor | None = None,
    ) -> None:
        if not name:
            self.missing.append((location, anchor))
            return
        self.entities[(kind, name)].append(location)
        if anchor is not None:
            self.anchors[(kind, name)].append(anchor)

    def reference(
        self,
        kinds: tuple[str, ...],
        target: str | None,
        location: str,
        anchor: ReferenceAnchor | None = None,
    ) -> None:
        if target:
            self.references.append(_Reference(target, kinds, location, anchor))

    def names(self, kinds: tuple[str, ...]) -> list[str]:
        return sorted({name for (kind, name) in self.entities if kind in kinds})

    def validate(self) -> ReferenceValidation:
        issues: list[ReferenceIssue] = []
        for (kind, name), locations in sorted(self.entities.items()):
            if len(locations) > 1:
                issues.append(
                    ReferenceIssue(
                        kind=ReferenceIssueKind.DUPLICATE_NAME,
                        name=name,
                        locations=locations,
                        message=f"{kind} name {name!r} is used {len(locations)} times; "
                        "names must be unique",
                        anchors=self.anchors.get((kind, name), []),
                    )
                )
        for location, anchor in self.missing:
            issues.append(
                ReferenceIssue(
                    kind=ReferenceIssueKind.MISSING_NAME,
                    name="",
                    locations=[location],
                    message=f"{location} has no name, so nothing can reference it",
                    anchors=[anchor] if anchor else [],
                )
            )
        for ref in self.references:
            if any((kind, ref.target) in self.entities for kind in ref.kinds):
                continue
            wanted = " or ".join(ref.kinds)
            near = [
                name for name in self.names(ref.kinds) if name.casefold() == ref.target.casefold()
            ]
            hint = f" (did you mean {near[0]!r}? names are case-sensitive)" if near else ""
            issues.append(
                ReferenceIssue(
                    kind=ReferenceIssueKind.DANGLING_REFERENCE,
                    name=ref.target,
                    locations=[ref.location],
                    message=f"{ref.location} refers to {wanted} {ref.target!r}, which does not "
                    f"exist{hint}",
                    anchors=[ref.anchor] if ref.anchor else [],
                )
            )
        return ReferenceValidation(
            valid=not issues, entities=sum(len(v) for v in self.entities.values()), issues=issues
        )


def _register(graph: ReferenceGraph, sheets: ExtractionSheets, spec: SheetSpec) -> None:
    for index, row in enumerate(sheet_rows(sheets, spec)):
        row_id = None if spec.kind == SheetKind.KEY_VALUE else getattr(row, "row_id", None)
        where = (
            spec.workbook_sheet
            if spec.kind == SheetKind.KEY_VALUE
            else f"{spec.workbook_sheet} row {spec.row_number(index)}"
        )
        for column in spec.columns:
            if column.field is None or not (column.entity or column.ref):
                continue
            if column.group and not group_active(spec, row, column.group):
                continue  # a continuation row: the entity is the one above
            anchor = ReferenceAnchor(sheet=spec.key, row_id=row_id, field=column.field)
            value = getattr(row, column.field).value
            location = f"{where} {column.header}"
            if column.entity:
                graph.entity(column.entity, value, location, anchor)
                continue
            targets = [t.strip() for t in (value or "").split(",")] if column.multi else [value]
            for target in targets:
                if target and target not in column.ref_literals:
                    graph.reference(column.ref, target, location, anchor)


def build_graph(sheets: ExtractionSheets) -> ReferenceGraph:
    graph = ReferenceGraph()
    for spec in SHEETS.values():
        _register(graph, sheets, spec)
    return graph


def validate_references(sheets: ExtractionSheets) -> ReferenceValidation:
    return build_graph(sheets).validate()

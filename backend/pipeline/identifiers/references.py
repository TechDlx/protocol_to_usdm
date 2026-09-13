"""Reference graph validation over the intermediate model.

Every named entity is registered in the study-wide namespace; every name reference must point at a
registered entity. Duplicate or missing names and dangling references are reported with their
locations. Stage B refuses to write a workbook while any issue remains.

Only the implemented sheets contribute today (study, arms, eligibility criteria). Sheets added in
later phases register their entities and references here.
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


@dataclass
class ReferenceGraph:
    entities: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    anchors: dict[str, list[ReferenceAnchor]] = field(default_factory=lambda: defaultdict(list))
    references: list[tuple[str, str]] = field(default_factory=list)  # (target name, location)
    missing: list[tuple[str, ReferenceAnchor | None]] = field(default_factory=list)

    def entity(
        self, name: str | None, location: str, anchor: ReferenceAnchor | None = None
    ) -> None:
        if not name:
            self.missing.append((location, anchor))
            return
        self.entities[name.casefold()].append(f"{location} ({name})")
        if anchor is not None:
            self.anchors[name.casefold()].append(anchor)

    def reference(self, target: str | None, location: str) -> None:
        if target:
            self.references.append((target, location))

    def validate(self) -> ReferenceValidation:
        issues: list[ReferenceIssue] = []
        for key, locations in sorted(self.entities.items()):
            if len(locations) > 1:
                issues.append(
                    ReferenceIssue(
                        kind=ReferenceIssueKind.DUPLICATE_NAME,
                        name=key,
                        locations=locations,
                        message=f"name {key!r} is used by {len(locations)} entities; "
                        "names must be unique across the study",
                        anchors=self.anchors.get(key, []),
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
        for target, location in self.references:
            if target.casefold() not in self.entities:
                issues.append(
                    ReferenceIssue(
                        kind=ReferenceIssueKind.DANGLING_REFERENCE,
                        name=target,
                        locations=[location],
                        message=f"{location} references {target!r}, which does not exist",
                    )
                )
        return ReferenceValidation(
            valid=not issues, entities=sum(len(v) for v in self.entities.values()), issues=issues
        )


def build_graph(sheets: ExtractionSheets) -> ReferenceGraph:
    graph = ReferenceGraph()
    if sheets.study is not None:
        graph.entity(
            sheets.study.name.value,
            "study",
            ReferenceAnchor(sheet="study", row_id=None, field="name"),
        )
        for i, date in enumerate(sheets.study.governance_dates, start=1):
            graph.entity(
                date.name.value,
                f"dates row {i}",
                ReferenceAnchor(sheet="dates", row_id=date.row_id, field="name"),
            )
    for i, arm in enumerate(sheets.study_design_arms or [], start=1):
        graph.entity(
            arm.name.value,
            f"studyDesignArms row {i}",
            ReferenceAnchor(sheet="study_design_arms", row_id=arm.row_id, field="name"),
        )
    for i, criterion in enumerate(sheets.eligibility_criteria or [], start=1):
        graph.entity(
            criterion.name.value,
            f"eligibility criteria row {i}",
            ReferenceAnchor(sheet="eligibility_criteria", row_id=criterion.row_id, field="name"),
        )
    return graph


def validate_references(sheets: ExtractionSheets) -> ReferenceValidation:
    return build_graph(sheets).validate()

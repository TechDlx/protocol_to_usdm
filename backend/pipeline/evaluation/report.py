"""Evaluation report models and their Markdown rendering."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class Tier(StrEnum):
    CODE = "code"  # controlled terminology, compared as C-codes
    REFERENCE = "reference"  # names of other entities, compared through the row alignment
    VALUE = "value"  # formatted values (numbers, units, dates, booleans), compared normalised
    TEXT = "text"  # prose, compared by fuzzy similarity
    IDENTIFIER = "identifier"  # generated entity names; reported, not part of the headline


class Tally(BaseModel):
    """Field counts. A field is a non-empty cell, or one item of a multi-valued cell (so each
    schedule mark is a field)."""

    gold: float = 0.0  # fields filled in the reference
    generated: float = 0.0  # fields filled in the generated workbook
    matched: float = 0.0  # fields that agree
    slots: float = 0.0  # fields filled on either side (a disagreement counts once)

    def add(self, other: "Tally") -> None:
        self.gold += other.gold
        self.generated += other.generated
        self.matched += other.matched
        self.slots += other.slots

    @computed_field  # type: ignore[prop-decorator]
    @property
    def precision(self) -> float | None:
        return round(self.matched / self.generated, 4) if self.generated else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recall(self) -> float | None:
        return round(self.matched / self.gold, 4) if self.gold else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return round(2 * p * r / (p + r), 4) if p and r else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def accuracy(self) -> float | None:
        return round(self.matched / self.slots, 4) if self.slots else None


class Difference(BaseModel):
    row: str  # a readable label of the row (from the reference when it has one)
    column: str
    outcome: str  # mismatch | missing (reference only) | extra (generated only)
    gold: str = ""
    generated: str = ""


class ColumnScore(BaseModel):
    header: str
    tier: Tier
    tally: Tally = Field(default_factory=Tally)


class UnitScore(BaseModel):
    """One table of a sheet; two-level sheets are split into one unit per level."""

    unit: str  # "" for a one-level sheet, else the level (objective, endpoint, ...)
    gold_rows: int
    generated_rows: int
    aligned_rows: int
    tally: Tally = Field(default_factory=Tally)
    identifiers: Tally = Field(default_factory=Tally)
    columns: list[ColumnScore] = Field(default_factory=list)
    differences: list[Difference] = Field(default_factory=list)  # a capped sample


class SheetScore(BaseModel):
    sheet: str  # workbook sheet (or view: timeline columns / rows, studyDesign grid)
    key: str  # layout key
    tally: Tally = Field(default_factory=Tally)
    identifiers: Tally = Field(default_factory=Tally)
    units: list[UnitScore] = Field(default_factory=list)


class EvalReport(BaseModel):
    generated_at: datetime
    git_commit: str | None = None
    git_dirty: bool | None = None
    reference: str  # reference workbook path
    reference_sha256: str
    generated: str  # generated workbook path
    generated_sha256: str
    source: str  # how the generated workbook was produced
    ct_version: str | None = None
    extraction: dict[str, object] = Field(default_factory=dict)  # agents, model, cost
    overall: Tally = Field(default_factory=Tally)  # every tier but identifiers
    identifiers: Tally = Field(default_factory=Tally)
    tiers: dict[Tier, Tally] = Field(default_factory=dict)
    sheets: list[SheetScore] = Field(default_factory=list)
    #: Reference sheets without a counterpart in the pipeline's layouts are not scored.
    not_in_reference: list[str] = Field(default_factory=list)
    #: Filled reference cells outside the pipeline's scope, by location.
    out_of_scope: dict[str, int] = Field(default_factory=dict)
    settings: dict[str, float] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recall_including_out_of_scope(self) -> float | None:
        total = self.overall.gold + sum(self.out_of_scope.values())
        return round(self.overall.matched / total, 4) if total else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _delta(now: float | None, before: float | None) -> str:
    if now is None or before is None:
        return ""
    change = 100 * (now - before)
    return f" ({'+' if change >= 0 else ''}{change:.1f})"


def render_markdown(report: EvalReport, previous: EvalReport | None = None) -> str:
    o, p = report.overall, previous.overall if previous else None
    accuracy = _delta(o.accuracy, p.accuracy if p else None)
    lines = [
        "# Evaluation against the CDISC Pilot reference workbook",
        "",
        f"- Generated: {report.generated_at:%Y-%m-%d %H:%M:%S} UTC",
        f"- Commit: {report.git_commit or 'unknown'}"
        + (" (uncommitted changes)" if report.git_dirty else ""),
        f"- Source: {report.source}",
        f"- CT version: {report.ct_version or 'unknown'}",
    ]
    if report.extraction:
        lines.append(
            "- Extraction: "
            + ", ".join(f"{k} {v}" for k, v in report.extraction.items() if v not in (None, ""))
        )
    if previous:
        lines.append(
            f"- Compared with: {previous.generated_at:%Y-%m-%d %H:%M:%S} UTC "
            f"({previous.git_commit or 'unknown'}); changes in percentage points in brackets"
        )
    lines += [
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Field-level accuracy | {_pct(o.accuracy)}{accuracy} |",
        f"| Precision | {_pct(o.precision)}{_delta(o.precision, p.precision if p else None)} |",
        f"| Recall | {_pct(o.recall)}{_delta(o.recall, p.recall if p else None)} |",
        f"| F1 | {_pct(o.f1)}{_delta(o.f1, p.f1 if p else None)} |",
        "| Recall including reference content outside the pipeline's scope | "
        f"{_pct(report.recall_including_out_of_scope)} |",
        f"| Fields: reference / generated / matched | {o.gold:.0f} / {o.generated:.0f} / "
        f"{o.matched:.1f} |",
        "",
        "Accuracy is matched fields over fields filled on either side; precision is over generated "
        "fields, recall over reference fields. Generated entity names are not scored (they are "
        "created by code; references are scored through the row alignment instead): "
        f"identifier agreement {_pct(report.identifiers.accuracy)}.",
        "",
        "## By tier",
        "",
        "| Tier | Accuracy | Precision | Recall | Reference fields |",
        "|---|---|---|---|---|",
    ]
    for tier, t in report.tiers.items():
        lines.append(
            f"| {tier.value} | {_pct(t.accuracy)} | {_pct(t.precision)} | {_pct(t.recall)} | "
            f"{t.gold:.0f} |"
        )
    before = {s.key: s for s in previous.sheets} if previous else {}
    lines += [
        "",
        "## By sheet",
        "",
        "| Sheet | Rows ref / gen / aligned | Accuracy | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|",
    ]
    for sheet in report.sheets:
        t = sheet.tally
        rows = " + ".join(f"{u.gold_rows}/{u.generated_rows}/{u.aligned_rows}" for u in sheet.units)
        was = before.get(sheet.key)
        lines.append(
            f"| {sheet.sheet} | {rows} | {_pct(t.accuracy)}"
            f"{_delta(t.accuracy, was.tally.accuracy if was else None)} | {_pct(t.precision)} | "
            f"{_pct(t.recall)} | {_pct(t.f1)} |"
        )
    if report.not_in_reference:
        lines += ["", f"Not in the reference (not scored): {', '.join(report.not_in_reference)}."]
    if report.out_of_scope:
        lines += [
            "",
            "## Reference content outside the pipeline's scope",
            "",
            "| Location | Filled cells |",
            "|---|---|",
        ]
        lines += [f"| {k} | {v} |" for k, v in report.out_of_scope.items()]
    lines += ["", "## Sample differences", ""]
    for sheet in report.sheets:
        for unit in sheet.units:
            if not unit.differences:
                continue
            title = f"{sheet.sheet}{f' ({unit.unit})' if unit.unit else ''}"
            lines += [f"### {title}", "", "| Row | Column | Outcome | Reference | Generated |"]
            lines.append("|---|---|---|---|---|")
            for d in unit.differences:
                lines.append(
                    f"| {_cell(d.row)} | {d.column} | {d.outcome} | {_cell(d.gold)} | "
                    f"{_cell(d.generated)} |"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cell(value: str, limit: int = 80) -> str:
    flat = " ".join(value.split()).replace("|", "\\|")
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"

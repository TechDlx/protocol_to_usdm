"""Stage C runner: the reviewed workbook becomes USDM JSON, validated.

The workbook is imported with usdm4-excel (the CDISC reference importer), so the JSON is exactly
what the USDM tooling makes of the reviewed workbook; nothing here edits the JSON. It is then
validated with the usdm4 DDF rule library, which runs offline. CDISC CORE runs too when its
cache is ready (building it needs CDISC Library access); otherwise the report says it did not run
and why.

Outputs, inside the run folder:
    usdm/<study slug>.json     the USDM v4 JSON
    usdm_report.json           import issues, rule findings, CORE status, entity counts

Resumable: when the report was produced from the current workbook (same sha256) and the JSON is
unchanged, nothing is regenerated unless forced.
"""

import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from simple_error_log.errors import Errors
from usdm4 import USDM4
from usdm4.rules.results import RuleStatus
from usdm4_excel import USDM4Excel

from backend.config import get_settings
from backend.pipeline.workbook.stage import load_report as load_workbook_report
from backend.pipeline.workbook.stage import workbook_path
from backend.storage.fs import write_model

log = logging.getLogger(__name__)

USDM_DIR = "usdm"
REPORT_FILE = "usdm_report.json"
MAX_FINDINGS = 2000  # per report; the counts stay exact


class FindingKind(StrEnum):
    EXPECTED = "expected"  # the pipeline or the importer does not produce this yet
    REVIEW = "review"  # the reviewed values need a change; the note says where


# What the reviewer should make of the rule findings the pipeline is known to produce. A finding
# not listed here deserves a closer look.
_GAP = FindingKind.EXPECTED
_REVIEW = FindingKind.REVIEW
_ROLES = "Study roles and their organisations are not extracted yet."
FINDING_NOTES: dict[str, tuple[FindingKind, str]] = {
    "DDF00031": (
        _GAP,
        "The importer relates the anchor (Fixed Reference) timing to its own timepoint; the "
        "CDISC Pilot reference workbook gets the same finding.",
    ),
    "DDF00083": (_GAP, "Identifiers are assigned by the importer, not by this pipeline."),
    "DDF00101": (_GAP, "Procedures are not extracted yet; activities use biomedical concepts."),
    "DDF00153": (_GAP, "Timeline planned durations are not extracted yet."),
    "DDF00172": (_GAP, _ROLES),
    "DDF00185": (_GAP, "Administrable products are not extracted yet."),
    "DDF00192": (_GAP, _ROLES),
    "DDF00201": (_GAP, _ROLES),
    "DDF00236": (
        _GAP,
        "Biomedical concept definitions come from the usdm4 catalogue, which lists the label "
        "among the synonyms.",
    ),
    "DDF00009": (_REVIEW, "Timings sheet: give the timeline one Fixed Reference timing."),
    "DDF00025": (_REVIEW, "Timings sheet: remove the window from the Fixed Reference timing."),
    "DDF00033": (_REVIEW, "Interventions sheet: give the administration a duration."),
    "DDF00034": (
        _REVIEW,
        "Interventions sheet: a varying duration needs a reason, and a reason needs "
        "'duration will vary' set.",
    ),
    "DDF00039": (
        _REVIEW,
        "Interventions sheet: a duration either varies (no quantity) or has a quantity; an "
        "empty duration is written as not varying.",
    ),
    "DDF00041": (_REVIEW, "Objectives sheet: at least one endpoint must be Primary."),
    "DDF00084": (_REVIEW, "Objectives sheet: exactly one objective must be Primary."),
    "DDF00097": (
        _REVIEW,
        "Populations sheet: enter the planned age range. USDM needs both ends, so an open range "
        "('18 years or older') needs an upper bound chosen by the reviewer, e.g. 18..100 YEARS.",
    ),
    "DDF00177": (_REVIEW, "Interventions sheet: a dose needs a route and a route needs a dose."),
    "DDF00178": (_REVIEW, "Interventions sheet: a dose needs a frequency."),
    "DDF00188": (
        _REVIEW,
        "Populations sheet: planned sex must be Female, Male or both (not the term 'Both').",
    ),
    "DDF00213": (
        _REVIEW,
        "Study design / interventions: a single group design expects one intervention, other "
        "models more than one.",
    ),
    "DDF00258": (
        _REVIEW,
        "Study design sheet: keep only one of Randomized, Stratification and Stratified "
        "Randomisation among the characteristics.",
    ),
}


class ImportIssue(BaseModel):
    level: str  # Error | Warning
    message: str
    location: str = ""


class RuleFinding(BaseModel):
    rule_id: str
    status: str  # Failure | Exception
    level: str
    message: str
    klass: str = ""
    attribute: str = ""
    path: str = ""
    rule_text: str = ""
    kind: FindingKind | None = None  # None: not a finding the pipeline is known to produce
    note: str | None = None


class RulesSummary(BaseModel):
    engine: str = "usdm4 rule library"
    rules: int = 0
    passed: int = 0
    failed: int = 0
    exceptions: int = 0
    not_implemented: int = 0
    findings: int = 0
    expected_findings: int = 0  # findings of kind EXPECTED


class CoreSummary(BaseModel):
    ran: bool
    reason: str | None = None  # why CORE did not run, or why it failed
    rules_executed: int = 0
    findings: int = 0
    execution_errors: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)


class UsdmReport(BaseModel):
    generated_at: datetime
    file: str | None  # relative to the run folder; None when the import produced nothing
    sha256: str | None = None
    size_bytes: int = 0
    usdm_version: str = ""
    system: str = ""  # the tool recorded in the JSON as its creator
    workbook_file: str
    workbook_sha256: str
    review_revision: int
    ct_version: str
    import_errors: list[ImportIssue] = Field(default_factory=list)
    import_warnings: list[ImportIssue] = Field(default_factory=list)
    rules: RulesSummary = Field(default_factory=RulesSummary)
    findings: list[RuleFinding] = Field(default_factory=list)
    core: CoreSummary = Field(default_factory=lambda: CoreSummary(ran=False))
    entities: dict[str, int] = Field(default_factory=dict)  # instanceType -> count
    seconds: dict[str, float] = Field(default_factory=dict)  # import / rules / core
    reused: bool = False


class UsdmNotReadyError(RuntimeError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_report(run_dir: Path) -> UsdmReport | None:
    path = run_dir / REPORT_FILE
    if not path.is_file():
        return None
    return UsdmReport.model_validate_json(path.read_text(encoding="utf-8"))


def usdm_path(run_dir: Path, slug: str) -> Path:
    return run_dir / USDM_DIR / f"{slug}.json"


def check_ready(run_dir: Path, slug: str) -> None:
    """Stage C needs a workbook written by Stage B."""
    workbook = load_workbook_report(run_dir)
    if workbook is None or not workbook_path(run_dir, slug).is_file():
        raise UsdmNotReadyError(["the USDM workbook has not been generated yet"])


def _import_issues(errors: Errors | None) -> tuple[list[ImportIssue], list[ImportIssue]]:
    found: list[ImportIssue] = []
    for item in errors.to_dict(Errors.WARNING) if errors is not None else []:
        location = item.get("location") or {}
        where = ".".join(
            str(location[k]).rsplit(".", 1)[-1]
            for k in ("class_name", "method_name")
            if location.get(k)
        )
        # usdm4-excel reports a sheet location (sheet, row, column) for cell-level problems.
        cells = ", ".join(
            f"{k} {location[k]}"
            for k in ("sheet", "row", "column")
            if location.get(k) not in (None, "", "?")
        )
        found.append(
            ImportIssue(
                level=str(item.get("level", "")),
                message=str(item.get("message", "")).split("\n\nDetails")[0],
                location=cells or where,
            )
        )
    errors_ = [i for i in found if i.level == "Error"]
    warnings = [i for i in found if i.level != "Error"]
    return errors_, warnings


def _instances(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        kind = node.get("instanceType")
        if isinstance(kind, str):
            yield kind
        for value in node.values():
            yield from _instances(value)
    elif isinstance(node, list):
        for value in node:
            yield from _instances(value)


def entity_counts(document: Any) -> dict[str, int]:
    return dict(sorted(Counter(_instances(document)).items()))


def _rules(usdm: USDM4, json_file: Path) -> tuple[RulesSummary, list[RuleFinding]]:
    results = usdm.validate(str(json_file))
    findings = [
        RuleFinding(
            rule_id=str(row.get("rule_id") or ""),
            status=str(row.get("status") or ""),
            level=str(row.get("level") or ""),
            message=str(row.get("message") or row.get("exception") or ""),
            klass=str(row.get("klass") or ""),
            attribute=str(row.get("attribute") or ""),
            path=str(row.get("path") or ""),
            rule_text=str(row.get("rule_text") or ""),
        )
        for row in results.to_dict()
    ]
    for finding in findings:
        if finding.rule_id in FINDING_NOTES:
            finding.kind, finding.note = FINDING_NOTES[finding.rule_id]
    summary = RulesSummary(
        rules=results.count(),
        passed=len(results.by_status(RuleStatus.SUCCESS)),
        failed=len(results.by_status(RuleStatus.FAILURE)),
        exceptions=len(results.by_status(RuleStatus.EXCEPTION)),
        not_implemented=len(results.by_status(RuleStatus.NOT_IMPLEMENTED)),
        findings=len(findings),
        expected_findings=sum(1 for f in findings if f.kind == FindingKind.EXPECTED),
    )
    return summary, findings[:MAX_FINDINGS]


def _core(usdm: USDM4, json_file: Path) -> CoreSummary:
    status = usdm.core_cache_status()
    if not status.ready:
        missing = ", ".join(status.details) if status.details else "resources"
        return CoreSummary(
            ran=False,
            reason="the CDISC CORE cache is not built (it needs CDISC Library API access); "
            f"missing: {missing}",
        )
    key = get_settings().cdisc_api_key
    try:
        result = usdm.validate_core(str(json_file), api_key=key.get_secret_value() if key else None)
    except Exception as exc:  # CORE is optional: report the failure, keep the rules results
        log.exception("CDISC CORE validation failed")
        return CoreSummary(ran=False, reason=f"CDISC CORE failed: {type(exc).__name__}")
    return CoreSummary(
        ran=True,
        rules_executed=result.rules_executed,
        findings=result.finding_count,
        execution_errors=result.execution_error_count,
        results=[
            {
                "rule_id": f.rule_id,
                "description": f.description,
                "message": f.message,
                "count": f.error_count,
            }
            for f in result.findings
        ],
    )


def generate_usdm(run_dir: Path, slug: str, force: bool = False) -> UsdmReport:
    check_ready(run_dir, slug)
    workbook = load_workbook_report(run_dir)
    assert workbook is not None
    xlsx = workbook_path(run_dir, slug)
    workbook_sha = _sha256(xlsx)
    out = usdm_path(run_dir, slug)

    previous = load_report(run_dir)
    if (
        not force
        and previous is not None
        and previous.workbook_sha256 == workbook_sha
        and previous.sha256 is not None
        and out.is_file()
        and _sha256(out) == previous.sha256
    ):
        return previous.model_copy(update={"reused": True})

    seconds: dict[str, float] = {}
    started = time.monotonic()
    excel = USDM4Excel()
    wrapper = excel.from_excel(str(xlsx))
    seconds["import"] = round(time.monotonic() - started, 1)
    import_errors, import_warnings = _import_issues(excel.errors())

    report = UsdmReport(
        generated_at=datetime.now(UTC),
        file=None,
        workbook_file=xlsx.relative_to(run_dir).as_posix(),
        workbook_sha256=workbook_sha,
        review_revision=workbook.review_revision,
        ct_version=workbook.ct_version,
        import_errors=import_errors,
        import_warnings=import_warnings,
    )
    out.unlink(missing_ok=True)
    if wrapper is None:
        report.core = CoreSummary(ran=False, reason="the workbook import produced no USDM")
        report.seconds = seconds
        write_model(run_dir / REPORT_FILE, report)
        return report

    text = wrapper.to_json()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    document = json.loads(text)
    report.file = out.relative_to(run_dir).as_posix()
    report.sha256 = _sha256(out)
    report.size_bytes = out.stat().st_size
    report.usdm_version = str(document.get("usdmVersion") or "")
    report.system = " ".join(
        str(document.get(k)) for k in ("systemName", "systemVersion") if document.get(k)
    )
    report.entities = entity_counts(document)

    usdm = USDM4()
    started = time.monotonic()
    report.rules, report.findings = _rules(usdm, out)
    seconds["rules"] = round(time.monotonic() - started, 1)
    started = time.monotonic()
    report.core = _core(usdm, out)
    if report.core.ran:
        seconds["core"] = round(time.monotonic() - started, 1)
    report.seconds = seconds

    write_model(run_dir / REPORT_FILE, report)
    return report

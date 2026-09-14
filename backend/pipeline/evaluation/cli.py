"""Command line for the evaluation harness (run through goldstandard/eval.py).

Default: run Stage A on the CDISC Pilot protocol in the evaluation workspace with review disabled
(resumable: unchanged agents are not sent to the model again), write the workbook straight from
the extraction, and score it against the CDISC Pilot reference workbook.

Results are written to goldstandard/results/<UTC time>_<commit>.json and .md, so accuracy can be
tracked across commits; the Markdown report shows the change since the previous result.
"""

import argparse
import hashlib
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from backend.config import REPO_ROOT, get_settings
from backend.logging_setup import JsonFormatter, Redactor
from backend.models.extraction import AgentRun
from backend.pipeline.evaluation.compare import evaluate
from backend.pipeline.evaluation.reader import read_workbook
from backend.pipeline.evaluation.report import EvalReport, render_markdown
from backend.pipeline.evaluation.runner import (
    EvaluationError,
    UnavailableLlm,
    existing_extraction,
    extraction_summary,
    run_stage_a,
    write_unreviewed_workbook,
)
from backend.pipeline.llm import AnthropicLlm, StructuredLlm
from backend.pipeline.terminology.ct import get_ct_resolver

GOLD_DIR = REPO_ROOT / "goldstandard"
REFERENCE_WORKBOOK = GOLD_DIR / "cdisc_pilot" / "CDISC_Pilot_Study.xlsx"
REFERENCE_PROTOCOL = GOLD_DIR / "cdisc_pilot" / "CDISC_Pilot_Study.pdf"
WORKSPACE = GOLD_DIR / "runs" / "cdisc-pilot"
RESULTS = GOLD_DIR / "results"
LOG_FILE = "evaluation.log"


def _shown(path: Path) -> str:
    """A path as recorded in tracked results: relative to the repository when inside it."""
    resolved = path.resolve()
    return (
        resolved.relative_to(REPO_ROOT).as_posix()
        if resolved.is_relative_to(REPO_ROOT)
        else str(path)
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _log_to(path: Path, secrets: list[str]) -> None:
    """JSON-lines log of the run (every model call with tokens and cost), secrets redacted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonFormatter(Redactor(secrets)))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def previous_result(results: Path, before: str) -> EvalReport | None:
    """The latest result written before `before` (a results file stem)."""
    earlier = sorted(p for p in results.glob("*.json") if p.stem < before)
    for path in reversed(earlier):
        try:
            return EvalReport.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
    return None


def write_results(report: EvalReport, results: Path) -> tuple[Path, Path]:
    results.mkdir(parents=True, exist_ok=True)
    commit = report.git_commit or "nogit"
    base = f"{report.generated_at:%Y%m%dT%H%M%SZ}_{commit}{'-dirty' if report.git_dirty else ''}"
    stem, n = base, 2
    while (results / f"{stem}.json").exists():  # two evaluations within one second
        stem, n = f"{base}-{n}", n + 1
    json_path, md_path = results / f"{stem}.json", results / f"{stem}.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report, previous_result(results, stem)), encoding="utf-8")
    return json_path, md_path


def _print_update(run: AgentRun) -> None:
    if run.status.value in ("done", "skipped", "failed"):
        note = " (re-processed stored output)" if run.reprocessed else ""
        cost = f" ${run.usage.cost_usd:.3f}" if run.usage and run.status.value == "done" else ""
        print(f"  {run.sheet}: {run.status.value}{note}{cost}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="goldstandard/eval.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--run-dir",
        type=Path,
        help="score an existing run's extraction.json instead of running Stage A (no model calls)",
    )
    source.add_argument(
        "--workbook",
        type=Path,
        help="score a workbook as it is, e.g. one written from a confirmed review",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-run every agent even if its inputs are unchanged (calls the model; costs money)",
    )
    parser.add_argument("--workspace", type=Path, default=WORKSPACE, help="Stage A run folder")
    parser.add_argument("--reference", type=Path, default=REFERENCE_WORKBOOK)
    parser.add_argument("--protocol", type=Path, default=REFERENCE_PROTOCOL)
    parser.add_argument("--results", type=Path, default=RESULTS, help="where results are written")
    args = parser.parse_args(argv)

    settings = get_settings()
    resolver = get_ct_resolver()
    extraction_info: dict[str, object] = {}
    try:
        if args.workbook is not None:
            workbook = args.workbook
            described = f"workbook {_shown(workbook)}"
            ct_version: str | None = None
        else:
            if args.run_dir is not None:
                run_dir = args.run_dir
                extraction = existing_extraction(run_dir)
                described = f"extraction of run {_shown(run_dir)} (review disabled)"
            else:
                run_dir = args.workspace
                _log_to(run_dir / LOG_FILE, settings.secret_values())
                key = settings.anthropic_api_key
                llm: StructuredLlm = (
                    AnthropicLlm(key.get_secret_value()) if key is not None else UnavailableLlm()
                )
                print(f"Stage A on {args.protocol.name} in {run_dir}", flush=True)
                extraction = run_stage_a(
                    run_dir, args.protocol, llm, resolver, force=args.force, on_update=_print_update
                )
                described = f"Stage A on {args.protocol.name}, review disabled"
            workbook, warnings = write_unreviewed_workbook(run_dir, extraction)
            extraction_info = extraction_summary(extraction, run_dir)
            if warnings:
                extraction_info["writer warnings"] = len(warnings)
            ct_version = extraction.ct_version
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Scoring {workbook} against {args.reference}", flush=True)
    status = _git("status", "--porcelain")
    report = evaluate(
        read_workbook(workbook),
        read_workbook(args.reference),
        resolver,
        generated_at=datetime.now(UTC),
        git_commit=_git("rev-parse", "--short", "HEAD"),
        git_dirty=bool(status) if status is not None else None,
        reference=_shown(args.reference),
        reference_sha256=_sha256(args.reference),
        generated=_shown(workbook),
        generated_sha256=_sha256(workbook),
        source=described,
        ct_version=ct_version or resolver.version,
        extraction=extraction_info,
    )
    json_path, md_path = write_results(report, args.results)
    o = report.overall
    print(
        f"Field-level accuracy {100 * (o.accuracy or 0):.1f}%  precision "
        f"{100 * (o.precision or 0):.1f}%  recall {100 * (o.recall or 0):.1f}%  "
        f"F1 {100 * (o.f1 or 0):.1f}%"
    )
    print(f"Results: {json_path}\n         {md_path}")
    # Scored either way, but a run with failed agents is not a clean measurement.
    return 1 if extraction_info.get("failed agents") else 0

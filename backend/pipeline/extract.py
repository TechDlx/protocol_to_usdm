"""Stage A3-A6: run the per-sheet extraction agents and assemble the intermediate model.

Outputs, inside the run folder:
    extraction/<sheet>.json      one file per agent: its run record and its records
    extraction.json              the assembled intermediate model
    provenance.json              every value with its source, confidence and review flags
    reference_validation.json    duplicate / missing names and dangling references
    run.log                      one JSON line per model call (model, tokens, latency, cost)

Agents run concurrently, bounded by the run's concurrency limit. Each writes its own file, so a
failing agent never corrupts another sheet. An agent whose inputs (protocol sections, prompt,
schema, model) are unchanged is not sent to the model again: its stored output is reused, and if
only deterministic post-processing or quote verification changed, records are rebuilt from the
stored model output for free.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.models.document import ParsedDocument
from backend.models.extraction import (
    AgentRun,
    AgentStatus,
    ExtractedField,
    Extraction,
    ExtractionSheets,
    ProvenanceEntry,
    TerminologyStatus,
    ValueOrigin,
)
from backend.models.run_config import RunConfig
from backend.models.segmentation import SectionMapping
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    decode_literal_escapes,
)
from backend.pipeline.agents.context import VERIFICATION_VERSION, input_hash
from backend.pipeline.agents.registry import AGENTS
from backend.pipeline.identifiers.references import validate_references
from backend.pipeline.llm import LlmOutputError, LlmRequest, StructuredLlm
from backend.pipeline.terminology.ct import CtResolver
from backend.storage.fs import write_json, write_model

log = logging.getLogger(__name__)

EXTRACTION_DIR = "extraction"
EXTRACTION_FILE = "extraction.json"
PROVENANCE_FILE = "provenance.json"
REFERENCE_VALIDATION_FILE = "reference_validation.json"
RUN_LOG_FILE = "run.log"

AgentUpdate = Callable[[AgentRun], None]


class AgentOutput(BaseModel):
    run: AgentRun
    records: Any = None
    # The validated structured output exactly as the model returned it. Kept so post-processing
    # changes can be re-applied without another (paid) model call.
    model_output: dict[str, Any] | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _append_run_log(run_dir: Path, event: dict[str, Any]) -> None:
    with (run_dir / RUN_LOG_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now().isoformat(), **event}, default=str) + "\n")


async def _run_agent(
    agent: SheetAgent,
    *,
    run_dir: Path,
    document: ParsedDocument,
    mapping: SectionMapping,
    config: RunConfig,
    study: StudyMeta,
    llm: StructuredLlm,
    resolver: CtResolver,
    semaphore: asyncio.Semaphore,
    force: bool,
    on_update: AgentUpdate,
) -> AgentOutput:
    out_path = run_dir / EXTRACTION_DIR / f"{agent.sheet}.json"
    run = AgentRun(sheet=agent.sheet, status=AgentStatus.QUEUED)
    on_update(run)

    context = agent.context(document, mapping)
    run.section_ids = context.section_ids
    if context.used_fallback:
        run.warnings.append(
            f"no sections mapped to M11 {', '.join(agent.m11_sections)}; used the wider "
            f"fallback {', '.join(agent.fallback_m11_sections)}"
        )
    if not context.sections:
        run.status, run.error = AgentStatus.FAILED, "no protocol sections relevant to this sheet"
        on_update(run)
        return AgentOutput(run=run)

    user_content = agent.user_content(context, resolver)
    run.input_hash = input_hash(
        PROMPT_VERSION,
        agent.sheet,
        agent.prompt_version,
        config.extraction_model,
        config.extraction_effort or "",
        resolver.version,
        SYSTEM_PROMPT,
        agent.schema_fingerprint(),
        user_content,
    )
    run.postprocess_version = f"{agent.postprocess_version}+verify{VERIFICATION_VERSION}"
    context_warnings = list(run.warnings)

    if not force and out_path.is_file():
        try:
            previous: AgentOutput | None = AgentOutput.model_validate_json(
                out_path.read_text(encoding="utf-8")
            )
        except ValidationError:
            previous = None
        reusable = (
            previous is not None
            and previous.run.input_hash == run.input_hash
            and previous.run.status in (AgentStatus.DONE, AgentStatus.SKIPPED)
        )
        if reusable and previous is not None:
            run.status, run.usage = AgentStatus.SKIPPED, previous.run.usage
            run.started_at, run.finished_at = previous.run.started_at, previous.run.finished_at
            if previous.run.postprocess_version == run.postprocess_version:
                run.warnings = previous.run.warnings
                on_update(run)
                return AgentOutput(
                    run=run, records=previous.records, model_output=previous.model_output
                )
            if previous.model_output is not None:
                # Same model inputs, newer deterministic rules: rebuild records from the stored
                # model output instead of paying for another call.
                output = agent.output_model.model_validate(
                    decode_literal_escapes(previous.model_output)
                )
                records, warnings = agent.to_records(output, context, resolver, study)
                run.warnings = context_warnings + warnings
                run.reprocessed = True
                result = AgentOutput(run=run, records=records, model_output=previous.model_output)
                write_model(out_path, result)
                on_update(run)
                return result

    async with semaphore:
        run.status, run.started_at = AgentStatus.RUNNING, _now()
        on_update(run)
        model_output: dict[str, Any] | None = None
        try:
            output, usage = await llm.extract(
                LlmRequest(
                    model=config.extraction_model,
                    system=SYSTEM_PROMPT,
                    user_content=user_content,
                    max_tokens=agent.max_tokens,
                    effort=config.extraction_effort,
                ),
                agent.output_model,
            )
            run.usage = usage
            _append_run_log(
                run_dir, {"event": "llm_call", "sheet": agent.sheet, **usage.model_dump()}
            )
            # Stored exactly as returned, for audit; post-processing uses a normalised copy.
            model_output = output.model_dump(mode="json")
            normalised = agent.output_model.model_validate(decode_literal_escapes(model_output))
            records, warnings = agent.to_records(normalised, context, resolver, study)
            run.warnings.extend(warnings)
            run.status = AgentStatus.DONE
        except Exception as exc:
            if isinstance(exc, LlmOutputError) and exc.usage is not None:
                run.usage = exc.usage
                _append_run_log(
                    run_dir, {"event": "llm_call", "sheet": agent.sheet, **exc.usage.model_dump()}
                )
            log.exception("extraction agent failed", extra={"sheet": agent.sheet})
            run.status, run.error = AgentStatus.FAILED, f"{type(exc).__name__}: {exc}"
            records = None
        run.finished_at = _now()

    result = AgentOutput(run=run, records=records, model_output=model_output)
    if run.status == AgentStatus.DONE:
        # A failed attempt leaves the previous successful output file untouched.
        write_model(out_path, result)
    on_update(run)
    return result


def _flatten(
    sheet: str, obj: BaseModel, row: int | None, prefix: str, threshold: float
) -> list[ProvenanceEntry]:
    entries: list[ProvenanceEntry] = []
    for name in type(obj).model_fields:
        value = getattr(obj, name)
        path = f"{prefix}{name}"
        if isinstance(value, ExtractedField):
            if value.is_empty and value.provenance is None:
                continue
            p, t = value.provenance, value.terminology
            reasons: list[str] = []
            if p is not None and p.confidence < threshold:
                reasons.append(f"confidence {p.confidence:.2f} below {threshold:.2f}")
            if p is not None and not p.verified:
                reasons.append("source not verified")
            if t is not None and t.status != TerminologyStatus.EXACT:
                reasons.append(f"terminology {t.status.value}")
            entries.append(
                ProvenanceEntry(
                    sheet=sheet,
                    row=row,
                    field=path,
                    value=None if value.value is None else str(value.value),
                    origin=p.origin if p else ValueOrigin.DERIVED,
                    source_section_id=p.source_section_id if p else None,
                    source_page=p.source_page if p else None,
                    raw_phrase=p.raw_phrase if p else None,
                    confidence=p.confidence if p else 1.0,
                    verified=p.verified if p else False,
                    terminology_status=t.status if t else None,
                    code=t.code if t else None,
                    needs_review=bool(reasons),
                    review_reasons=reasons,
                )
            )
        elif isinstance(value, list):
            for i, item in enumerate(value, start=1):
                if isinstance(item, BaseModel):
                    entries.extend(_flatten(sheet, item, row, f"{path}[{i}].", threshold))
    return entries


def provenance_entries(sheets: ExtractionSheets, threshold: float) -> list[ProvenanceEntry]:
    entries: list[ProvenanceEntry] = []
    if sheets.study is not None:
        entries.extend(_flatten("study", sheets.study, None, "", threshold))
    for i, arm in enumerate(sheets.study_design_arms or [], start=1):
        entries.extend(_flatten("study_design_arms", arm, i, "", threshold))
    for i, criterion in enumerate(sheets.eligibility_criteria or [], start=1):
        entries.extend(_flatten("eligibility_criteria", criterion, i, "", threshold))
    return entries


async def run_extraction(
    run_dir: Path,
    document: ParsedDocument,
    mapping: SectionMapping,
    config: RunConfig,
    study: StudyMeta,
    llm: StructuredLlm,
    resolver: CtResolver,
    sheets: list[str] | None = None,
    force: bool = False,
    on_update: AgentUpdate = lambda _run: None,
) -> Extraction:
    selected = [AGENTS[s] for s in (sheets or list(AGENTS))]
    semaphore = asyncio.Semaphore(config.concurrency_limit)
    (run_dir / EXTRACTION_DIR).mkdir(exist_ok=True)

    outputs = await asyncio.gather(
        *(
            _run_agent(
                agent,
                run_dir=run_dir,
                document=document,
                mapping=mapping,
                config=config,
                study=study,
                llm=llm,
                resolver=resolver,
                semaphore=semaphore,
                force=force,
                on_update=on_update,
            )
            for agent in selected
        )
    )
    current = {output.run.sheet: output for output in outputs}
    return assemble(run_dir, document, config, resolver.version, current)


def assemble(
    run_dir: Path,
    document: ParsedDocument,
    config: RunConfig,
    ct_version: str,
    current: dict[str, AgentOutput] | None = None,
) -> Extraction:
    """Build extraction.json and its companions.

    `current` holds this invocation's agent outcomes; they win. Agents not run this time
    contribute their last successful output file, so running a subset of agents keeps the other
    sheets. An agent that failed this time contributes no records, even if an older output file
    exists: stale results must never pass for current ones.
    """
    agents: dict[str, AgentRun] = {}
    records: dict[str, Any] = {}
    for key in AGENTS:
        output = (current or {}).get(key)
        if output is None:
            path = run_dir / EXTRACTION_DIR / f"{key}.json"
            if not path.is_file():
                continue
            output = AgentOutput.model_validate_json(path.read_text(encoding="utf-8"))
        agents[key] = output.run
        if output.run.status in (AgentStatus.DONE, AgentStatus.SKIPPED):
            records[key] = output.records
    sheets = ExtractionSheets.model_validate(records)

    extraction = Extraction(
        source_sha256=document.source.sha256,
        ct_version=ct_version,
        generated_at=_now(),
        agents=agents,
        sheets=sheets,
    )
    write_model(run_dir / EXTRACTION_FILE, extraction)
    entries = provenance_entries(sheets, config.confidence_threshold)
    write_json(run_dir / PROVENANCE_FILE, [e.model_dump(mode="json") for e in entries])
    write_model(run_dir / REFERENCE_VALIDATION_FILE, validate_references(sheets))
    return extraction


def load_extraction(run_dir: Path) -> Extraction | None:
    path = run_dir / EXTRACTION_FILE
    if not path.is_file():
        return None
    return Extraction.model_validate_json(path.read_text(encoding="utf-8"))

"""Base class for per-sheet extraction agents.

An agent declares which ICH M11 sections it reads, the Pydantic schema the model must return,
its instructions and a worked example. Everything after the model call — quote verification,
terminology resolution, naming — is deterministic code in `to_records`.
"""

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from backend.models.document import ParsedDocument
from backend.models.extraction import ExtractedField, Provenance, ValueOrigin
from backend.models.segmentation import SectionMapping
from backend.models.study import StudyMeta
from backend.pipeline.agents.common import Cited
from backend.pipeline.agents.context import AgentContext, build_context, provenance_for
from backend.pipeline.terminology.ct import CtField, CtResolver


class SheetAgent(ABC):
    #: Key in extraction.json `sheets` and in agent status.
    sheet: ClassVar[str]
    #: Human-facing workbook sheet name(s) this agent fills.
    workbook_sheets: ClassVar[tuple[str, ...]]
    #: Bump when what the model sees or returns changes (instructions, example, schema).
    #: A change re-calls the model.
    prompt_version: ClassVar[str]
    #: Bump when `to_records` changes. Stored model output is re-processed without a model call.
    postprocess_version: ClassVar[str] = "1"
    #: M11 sections read with everything beneath them.
    m11_sections: ClassVar[tuple[str, ...]]
    #: M11 sections read for their own content only, without their M11 subsections.
    m11_exact_sections: ClassVar[tuple[str, ...]] = ()
    fallback_m11_sections: ClassVar[tuple[str, ...]] = ()
    #: Parsed-document section ids always included regardless of mapping (e.g. "title-page").
    extra_section_ids: ClassVar[tuple[str, ...]] = ()
    output_model: ClassVar[type[BaseModel]]
    max_tokens: ClassVar[int] = 32000

    def context(self, document: ParsedDocument, mapping: SectionMapping) -> AgentContext:
        return build_context(
            document,
            mapping,
            list(self.m11_sections),
            list(self.fallback_m11_sections),
            extra_section_ids=list(self.extra_section_ids),
            exact_m11_numbers=list(self.m11_exact_sections),
        )

    @abstractmethod
    def instructions(self, resolver: CtResolver) -> str: ...

    @abstractmethod
    def example(self) -> str: ...

    @abstractmethod
    def to_records(
        self, output: Any, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[Any, list[str]]:
        """Turn validated model output into intermediate-model records, plus warnings."""

    def user_content(self, context: AgentContext, resolver: CtResolver) -> str:
        return (
            f"<task>\n{self.instructions(resolver)}\n</task>\n\n"
            f"<example>\n{self.example()}\n</example>\n\n"
            f"{context.rendered}"
        )

    def schema_fingerprint(self) -> str:
        return json.dumps(self.output_model.model_json_schema(), sort_keys=True)

    # ----- helpers for to_records ------------------------------------------------------------

    @staticmethod
    def terms(resolver: CtResolver, field: CtField) -> list[str]:
        return [t["preferredTerm"] for t in resolver.codelist(field).get("terms") or []]

    @staticmethod
    def extracted(
        cited: Cited | None,
        context: AgentContext,
        resolver: CtResolver | None = None,
        ct: CtField | None = None,
    ) -> ExtractedField[str]:
        if cited is None or cited.value is None or not cited.value.strip():
            return ExtractedField()
        value = cited.value.strip()
        return ExtractedField(
            value=value,
            provenance=provenance_for(cited, context),
            terminology=resolver.resolve(value, ct) if resolver and ct else None,
        )

    @staticmethod
    def judged(value: str | None, basis: ExtractedField[str], note: str) -> ExtractedField[str]:
        """A value the model chose rather than transcribed, attributed to the evidence it rests on.

        It inherits the basis field's source and verification, never more than 0.8 confidence.
        """
        if value is None:
            return ExtractedField()
        p = basis.provenance
        return ExtractedField(
            value=value,
            provenance=Provenance(
                origin=ValueOrigin.EXTRACTED,
                source_section_id=p.source_section_id if p else None,
                source_page=p.source_page if p else None,
                confidence=min(p.confidence, 0.8) if p else 0.5,
                verified=p.verified if p else False,
                note=note,
            ),
        )

    @staticmethod
    def derived(value: str | None, note: str) -> ExtractedField[str]:
        """A value computed deterministically by this application (names, fixed defaults)."""
        if value is None:
            return ExtractedField()
        return ExtractedField(
            value=value,
            provenance=Provenance(
                origin=ValueOrigin.DERIVED, confidence=1.0, verified=True, note=note
            ),
        )

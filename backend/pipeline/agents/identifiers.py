"""`studyOrganizations` and `studyIdentifiers`: who identifies the study, and with which numbers.

One agent fills both sheets because each identifier is issued by an organization: the model lists
organizations under short keys and points identifiers at them, and names are assigned here, so the
`organization` reference is correct by construction.

USDM requires every organization to carry an identifier scheme and identifier (a DUNS number, for
instance). Protocols rarely state these for the sponsor, so they stay empty for the reviewer. The
well-known public registries get their identifier from a small fixed table, marked as generated.
"""

import re
from pathlib import Path

import usdm4
from pydantic import BaseModel, Field
from usdm4.ct.iso.iso3166.library import Library as Iso3166Library

from backend.models.extraction import (
    ExtractedField,
    IdentifiersSheet,
    OrganizationRecord,
    StudyIdentifierRecord,
)
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.identifiers.names import NameRegistry, safe_name
from backend.pipeline.terminology.ct import ORGANIZATION_TYPE, CtResolver
from backend.pipeline.workbook.layout import ORGANIZATIONS


class AddressOut(BaseModel):
    lines: list[str] = Field(description="Street address lines, as printed.")
    city: str | None
    district: str | None = Field(description="District or county, only if printed.")
    state: str | None = Field(description="State, province or region, only if printed.")
    postal_code: str | None
    country_code: str | None = Field(
        description="ISO 3166 alpha-3 code of the printed country, e.g. USA, GBR, DEU."
    )
    quote: Cited = Field(
        description="value: the address as one line; quote: part of the printed address."
    )


class OrganizationOut(BaseModel):
    key: str = Field(description="A short key you invent, used by identifiers to refer to it.")
    name: Cited = Field(
        description="The organization's name as printed, e.g. 'Examplar Pharma Ltd'."
    )
    type: Cited = Field(description="The kind of organization, as a controlled-terminology phrase.")
    identifier_scheme: Cited = Field(
        description="The scheme of an organization identifier the protocol prints, e.g. 'DUNS'. "
        "Null unless printed."
    )
    identifier: Cited = Field(description="That organization identifier. Null unless printed.")
    address: AddressOut | None = Field(description="The postal address, only if printed.")


class IdentifierOut(BaseModel):
    identifier: Cited = Field(description="The study identifier exactly as printed.")
    organization_key: str = Field(description="The key of the organization that issued it.")


class IdentifiersOut(BaseModel):
    organizations: list[OrganizationOut]
    identifiers: list[IdentifierOut]


# Public registries: USDM needs an identifier for the registry organization itself. These are
# generated, visible to the reviewer, and never presented as extracted.
_REGISTRIES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"clinicaltrials\.gov|\bNCT\d{8}\b", re.I), "URL", "https://clinicaltrials.gov"),
    (re.compile(r"eudract|\b\d{4}-\d{6}-\d{2}\b", re.I), "URL", "https://eudract.ema.europa.eu"),
    (
        re.compile(r"\bCTIS\b|\bEU CT\b|\b\d{4}-\d{6}-\d{2}-\d{2}\b", re.I),
        "URL",
        "https://euclinicaltrials.eu",
    ),
    (re.compile(r"isrctn", re.I), "URL", "https://www.isrctn.com"),
]


def _registry(text: str) -> tuple[str, str] | None:
    for pattern, scheme, identifier in _REGISTRIES:
        if pattern.search(text):
            return scheme, identifier
    return None


_iso: Iso3166Library | None = None


def _country(code: str | None) -> str | None:
    global _iso
    if not code or not code.strip():
        return None
    if _iso is None:
        _iso = Iso3166Library(str(Path(usdm4.__file__).parent))
        _iso.load()
    found: str | None
    found, _ = _iso.code_or_decode(code.strip().upper() if len(code.strip()) <= 3 else code.strip())
    return found


def _address_text(address: AddressOut) -> str | None:
    """The importer's pipe-separated form: lines|district|city|state|postal code|country code."""
    country = _country(address.country_code)
    if not address.city or not country:
        return None
    clean = [p.replace("|", " ").strip() for p in address.lines if p.strip()] or [""]
    parts = [
        *clean,
        address.district or "",
        address.city,
        address.state or "",
        address.postal_code or "",
        country,
    ]
    return "|".join(p.strip() for p in parts)


class IdentifiersAgent(SheetAgent):
    sheet = "identifiers"
    workbook_sheets = ("studyOrganizations", "studyIdentifiers")
    prompt_version = "1"
    m11_sections = ("0", "1.1")
    m11_exact_sections = ("1", "11.2.2")
    extra_section_ids = ("title-page",)
    output_model = IdentifiersOut
    max_tokens = 12000

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
List the study's identifiers and the organizations that issued them, for the USDM \
`studyIdentifiers` and `studyOrganizations` sheets.

- Identifiers: the sponsor protocol number, registry numbers (ClinicalTrials.gov NCT number, EudraCT \
number, EU CT number, ISRCTN, WHO UTN), and regulatory numbers such as an IND number. Copy each \
exactly as printed. Do not list document version numbers, compound codes or amendment numbers.
- Every identifier needs its issuing organization: the sponsor for the protocol number, the \
registry for a registry number ("ClinicalTrials.gov", "EudraCT", ...), the regulator for an IND.
- An organization that issues no identifier is not listed.
- type: {terms_hint(self.terms(resolver, ORGANIZATION_TYPE))}. A pharmaceutical sponsor is \
"Drug Company"; a trial registry is "Study Registry"; a regulator is "Regulatory Agency".
- For a registry you may quote the identifier line as the evidence for its name and type."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="title-page" number="" title="Title Page" pages="1-1">
[[PAGE 1]]
Protocol Number: EX-204    NCT Number: NCT01234567
Sponsor: Examplar Pharma Ltd, 1 Example Way, Cambridge, CB1 2AB, United Kingdom
</section>

Output:
{"organizations": [
 {"key": "sponsor", "name": {"value": "Examplar Pharma Ltd", "quote": "Sponsor: Examplar Pharma Ltd", "section_id": "title-page", "confidence": 0.95},
  "type": {"value": "Drug Company", "quote": "Sponsor: Examplar Pharma Ltd", "section_id": "title-page", "confidence": 0.8},
  "identifier_scheme": {"value": null, "quote": null, "section_id": null, "confidence": 0},
  "identifier": {"value": null, "quote": null, "section_id": null, "confidence": 0},
  "address": {"lines": ["1 Example Way"], "city": "Cambridge", "district": null, "state": null, "postal_code": "CB1 2AB", "country_code": "GBR",
   "quote": {"value": "1 Example Way, Cambridge, CB1 2AB, United Kingdom", "quote": "1 Example Way, Cambridge, CB1 2AB, United Kingdom", "section_id": "title-page", "confidence": 0.9}}},
 {"key": "ctgov", "name": {"value": "ClinicalTrials.gov", "quote": "NCT Number: NCT01234567", "section_id": "title-page", "confidence": 0.8},
  "type": {"value": "Study Registry", "quote": "NCT Number: NCT01234567", "section_id": "title-page", "confidence": 0.8},
  "identifier_scheme": {"value": null, "quote": null, "section_id": null, "confidence": 0},
  "identifier": {"value": null, "quote": null, "section_id": null, "confidence": 0},
  "address": null}],
 "identifiers": [
 {"identifier": {"value": "EX-204", "quote": "Protocol Number: EX-204", "section_id": "title-page", "confidence": 0.95}, "organization_key": "sponsor"},
 {"identifier": {"value": "NCT01234567", "quote": "NCT Number: NCT01234567", "section_id": "title-page", "confidence": 0.95}, "organization_key": "ctgov"}]}"""

    def to_records(
        self, output: IdentifiersOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[IdentifiersSheet, list[str]]:
        warnings: list[str] = []
        names = NameRegistry()
        by_key: dict[str, str] = {}
        issued: dict[str, list[str]] = {}
        for ident in output.identifiers:
            if ident.identifier.value:
                issued.setdefault(ident.organization_key, []).append(ident.identifier.value)

        organizations: list[OrganizationRecord] = []
        for i, org in enumerate(output.organizations, start=1):
            label = self.extracted(org.name, context)
            if label.value is None:
                warnings.append(f"organization {i} dropped: no name")
                continue
            name = names.claim(safe_name(label.value, f"Organization {i}"))
            by_key[org.key] = name
            scheme = self.extracted(org.identifier_scheme, context)
            identifier = self.extracted(org.identifier, context)
            registry = _registry(" ".join([label.value, *issued.get(org.key, [])]))
            if scheme.is_empty and identifier.is_empty and registry is not None:
                note = "generated: the registry's own web address identifies it"
                scheme, identifier = (
                    self.derived(registry[0], note),
                    self.derived(registry[1], note),
                )
            address = ExtractedField[str]()
            if org.address is not None:
                basis = self.extracted(org.address.quote, context)
                text = _address_text(org.address)
                if text is None:
                    warnings.append(
                        f"address of {name} left empty: it needs at least a city and a country "
                        "with an ISO 3166 code"
                    )
                else:
                    address = self.reformatted(
                        text,
                        basis,
                        "arranged as lines|district|city|state|postal code|country code",
                    )
            organizations.append(
                OrganizationRecord(
                    name=self.derived(name, "generated from the organization name"),
                    label=label,
                    type=self.cell(org.type, context, resolver, ORGANIZATIONS.column("type")),
                    identifier_scheme=scheme,
                    identifier=identifier,
                    address=address,
                )
            )

        identifiers: list[StudyIdentifierRecord] = []
        for ident in output.identifiers:
            value = self.extracted(ident.identifier, context)
            if value.value is None:
                continue
            org_name = by_key.get(ident.organization_key)
            if org_name is None:
                warnings.append(
                    f"identifier {value.value} has no listed issuing organization; choose one"
                )
            identifiers.append(
                StudyIdentifierRecord(
                    identifier=value,
                    organization=self.judged(org_name, value, "the issuer the model named"),
                )
            )
        if not identifiers:
            warnings.append("no study identifiers found")
        return IdentifiersSheet(organizations=organizations, identifiers=identifiers), warnings

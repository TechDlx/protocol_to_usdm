"""Terminology resolution for one workbook cell, as its column declares it.

Shared by the agents (turning model output into records), the review (re-resolving a reviewer's
edit) and validation, so a cell is judged the same way everywhere.
"""

from backend.models.extraction import TerminologyResolution
from backend.pipeline.terminology.ct import MULTI_SEPARATOR, CtResolver, combine
from backend.pipeline.workbook.layout import ColumnSpec

OTHER_PREFIX = "other"


def _phrase(column: ColumnSpec, item: str) -> str:
    """For "Other=<reason>" cells the term is the part before '='."""
    if column.other_allowed and item.strip().casefold().startswith(OTHER_PREFIX):
        return item.split("=", 1)[0].strip()
    return item.strip()


def other_problem(column: ColumnSpec, value: str | None) -> str | None:
    """An 'Other' choice without its reason, which the importer cannot read."""
    if not column.other_allowed or not value:
        return None
    items = value.split(MULTI_SEPARATOR) if column.multi else [value]
    for item in items:
        text = item.strip()
        if text.casefold().startswith(OTHER_PREFIX):
            head, sep, reason = text.partition("=")
            if head.strip().casefold() == OTHER_PREFIX and (not sep or not reason.strip()):
                return f"'{text}' needs the reason after '=', e.g. Other=<reason>"
    return None


def resolve_cell(
    column: ColumnSpec, value: str | None, resolver: CtResolver
) -> TerminologyResolution | None:
    if value is None or not value.strip():
        return None
    if column.bc:
        items = [i.strip() for i in value.split(MULTI_SEPARATOR) if i.strip()]
        return combine([resolver.bcs.resolve(item) for item in items])
    if column.ct is None:
        return None
    if column.multi:
        items = [i for i in value.split(MULTI_SEPARATOR) if i.strip()]
        return resolver.resolve_many(
            MULTI_SEPARATOR.join(_phrase(column, i) for i in items), column.ct
        )
    return resolver.resolve(_phrase(column, value), column.ct)

"""Cell formats the usdm4-excel importer parses, checked before a workbook is written.

Each check mirrors the importer's own parser (usdm4_excel/import_/types and base_sheet), so a value
the review accepts is a value the import reads the way the reviewer expects. Checks return
`(blocking, message)` for a problem, or None when the value is fine; an empty value is always fine
here (required-ness is checked separately).

The formatting helpers are what agents use to write values in these formats deterministically.
"""

import re
from enum import StrEnum
from typing import Any

from backend.pipeline.terminology.ct import CtResolver


class ValueFormat(StrEnum):
    BOOLEAN = "boolean"  # Y / N
    QUANTITY = "quantity"  # "125 mg"; the unit is optional
    RANGE = "range"  # "18..75 YEARS"; the unit is required
    COUNT = "count"  # "300" or "280..320"; no unit
    DATE = "date"  # 2024-01-15
    GEOGRAPHIC_SCOPE = "geographic_scope"  # "Global", "Region: Europe", "Country: GBR"
    ENROLLMENT = "enrollment"  # "Global: 300", "Country: GBR=40"
    AMENDMENT_REASON = "amendment_reason"  # a codelist term, or "Other=<text>"
    DURATION = "duration"  # "2 weeks" (timing values)
    WINDOW = "window"  # "-3..3 days" (timing windows)


FORMAT_HINTS: dict[ValueFormat, str] = {
    ValueFormat.BOOLEAN: "Y or N",
    ValueFormat.QUANTITY: "a whole number and an optional CDISC unit, e.g. 125 mg",
    ValueFormat.RANGE: "lower..upper and a CDISC unit, e.g. 18..75 YEARS",
    ValueFormat.COUNT: "a whole number or a range, e.g. 300 or 280..320",
    ValueFormat.DATE: "yyyy-mm-dd",
    ValueFormat.GEOGRAPHIC_SCOPE: "Global, Region: <name> or Country: <code>, comma-separated",
    ValueFormat.ENROLLMENT: "Global: <number>, Region: <name>=<number> or Country: <code>=<number>",
    ValueFormat.AMENDMENT_REASON: "a term from the codelist, or Other=<reason>",
    ValueFormat.DURATION: "a whole number and a time unit, e.g. 2 weeks or 0 days",
    ValueFormat.WINDOW: "lower..upper and a time unit, e.g. -3..3 days",
}

# Time units the timing importer can encode as ISO 8601 (usdm4_excel/import_/iso8601/duration.py).
TIME_UNITS = {
    "y",
    "yrs",
    "yr",
    "years",
    "year",
    "mths",
    "mth",
    "months",
    "month",
    "w",
    "wks",
    "wk",
    "weeks",
    "week",
    "d",
    "dys",
    "dy",
    "days",
    "day",
    "h",
    "hrs",
    "hr",
    "hours",
    "hour",
    "m",
    "mins",
    "min",
    "minutes",
    "minute",
    "s",
    "secs",
    "sec",
    "seconds",
    "second",
}
_DURATION = re.compile(r"(?P<value>\d+)\s*(?P<unit>[A-Za-z]+)")

_BOOLEAN = {"true", "yes", "1", "y", "t", "false", "no", "0", "n", "f"}
_INTEGER = re.compile(r"[+-]?\d+")
_DECIMAL = re.compile(r"[+-]?\d+(\.\d{1,5})?")
# The importer's patterns (types/quantity_type.py, types/range_type.py), anchored at both ends here
# so trailing text that the importer would swallow into the unit is caught.
_QUANTITY = re.compile(r"(?P<value>[+-]?\d+)(?P<fraction>\.\d{0,5})?(\s*(?P<unit>.+))?")
_RANGE = re.compile(r"(?P<lower>[+-]?\d+)(\s*\.\.\s*(?P<upper>[+-]?\d+))?( \s*(?P<unit>.+))?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

Problem = tuple[bool, str]


def _unit_problem(unit: str, resolver: CtResolver, plural_ok: bool) -> str | None:
    if resolver.unit(unit) is not None:
        return None
    if plural_ok and unit.lower().endswith("s") and resolver.unit(unit[:-1]) is not None:
        return None
    return f"unit '{unit}' is not a CDISC unit (use e.g. mg, YEARS, WEEKS)"


def check(fmt: ValueFormat, value: str | None, resolver: CtResolver) -> Problem | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if fmt == ValueFormat.BOOLEAN:
        return None if text.lower() in _BOOLEAN else (True, "must be Y or N")
    if fmt == ValueFormat.DATE:
        return None if _DATE.fullmatch(text) else (True, "must be a date as yyyy-mm-dd")
    if fmt == ValueFormat.QUANTITY:
        return _check_quantity(text, resolver, unit_required=False)
    if fmt == ValueFormat.COUNT:
        if ".." in text:
            m = _RANGE.fullmatch(text)
            ok = m is not None and not m.group("unit")
            return None if ok else (True, "must be a whole number or a range such as 280..320")
        return None if _INTEGER.fullmatch(text) else (True, "must be a whole number")
    if fmt == ValueFormat.RANGE:
        m = _RANGE.fullmatch(text)
        if m is None or not m.group("upper"):
            return (True, "must be lower..upper followed by a unit, e.g. 18..75 YEARS")
        if not m.group("unit"):
            return (True, "needs a unit after the range, e.g. 18..75 YEARS")
        problem = _unit_problem(m.group("unit").strip(), resolver, plural_ok=True)
        return (True, problem) if problem else None
    if fmt == ValueFormat.GEOGRAPHIC_SCOPE:
        for item in _split(text):
            key = item.split(":")[0].strip().upper()
            if key == "GLOBAL" and ":" not in item:
                continue
            if (
                key in ("REGION", "COUNTRY")
                and len(item.split(":")) == 2
                and item.split(":")[1].strip()
            ):
                continue
            return (True, f"'{item}' must be Global, Region: <name> or Country: <code>")
        return None
    if fmt == ValueFormat.ENROLLMENT:
        for item in _split(text):
            parts = item.split(":")
            key = parts[0].strip().upper()
            if len(parts) != 2:
                return (True, f"'{item}' must look like Global: 300 or Country: GBR=40")
            rest = parts[1].strip()
            if key == "GLOBAL":
                number_problem = _check_quantity(rest, resolver, unit_required=False)
                if number_problem:
                    return (True, f"'{item}': the number {number_problem[1]}")
            elif key in ("REGION", "COUNTRY", "COHORT", "SITE"):
                target, _, number = rest.partition("=")
                if not target.strip() or not _INTEGER.fullmatch(number.strip()):
                    return (True, f"'{item}' must look like {key.title()}: <name>=<number>")
            else:
                return (True, f"'{item}' must start with Global, Region, Country, Cohort or Site")
        return None
    if fmt == ValueFormat.AMENDMENT_REASON:
        return None  # checked as terminology; see review validation
    if fmt == ValueFormat.DURATION:
        m = _DURATION.fullmatch(text)
        if m is None or m.group("unit").lower() not in TIME_UNITS:
            return (True, "must be a whole number and a time unit, e.g. 2 weeks")
        return None
    if fmt == ValueFormat.WINDOW:
        m = _RANGE.fullmatch(text)
        if m is None or not m.group("upper") or not m.group("unit"):
            return (True, "must be lower..upper and a time unit, e.g. -3..3 days")
        unit = m.group("unit").strip()
        if unit.lower() not in TIME_UNITS:
            return (True, f"unit '{unit}' is not a time unit (days, weeks, hours, ...)")
        problem = _unit_problem(unit, resolver, plural_ok=True)
        return (True, problem) if problem else None
    raise ValueError(f"unknown format {fmt}")


def _check_quantity(text: str, resolver: CtResolver, unit_required: bool) -> Problem | None:
    m = _QUANTITY.fullmatch(text)
    if m is None:
        return (True, "must be a number optionally followed by a unit, e.g. 125 mg")
    unit = (m.group("unit") or "").strip()
    if not unit:
        if unit_required:
            return (True, "needs a unit")
    else:
        problem = _unit_problem(unit, resolver, plural_ok=False)
        if problem:
            return (True, problem)
    if m.group("fraction") and m.group("fraction").strip(".0"):
        return (False, "the decimal part is dropped by the usdm4-excel importer")
    return None


def _split(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


# ----- formatting helpers for agents ---------------------------------------------------------


def unit_value(unit: str | None, resolver: CtResolver) -> str | None:
    """The unit's CDISC submission value (e.g. 'YEARS'), or None when it is not a CDISC unit."""
    if not unit or not unit.strip():
        return None
    term: dict[str, Any] | None = resolver.unit(unit.strip())
    if term is None and unit.strip().lower().endswith("s"):
        term = resolver.unit(unit.strip()[:-1])
    return (term.get("submissionValue") or None) if term else None


def number_text(value: str | float | int | None) -> str | None:
    """A number as plain text ('300', '2.5'), or None if it is not a number."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not _DECIMAL.fullmatch(text):
        return None
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_quantity(value: str | None, unit: str | None, resolver: CtResolver) -> str | None:
    """'125 mg'. A unit that is not a CDISC unit is kept as written, so review flags it."""
    number = number_text(value)
    if number is None:
        return None
    if not unit or not unit.strip():
        return number
    return f"{number} {unit_value(unit, resolver) or unit.strip()}"


def format_range(
    lower: str | None, upper: str | None, unit: str | None, resolver: CtResolver
) -> str | None:
    """'18..75 YEARS'. Both ends are required: an open range ("18 or older") cannot be written."""
    lo, hi = number_text(lower), number_text(upper)
    if lo is None or hi is None:
        return None
    body = f"{lo}..{hi}"
    if not unit or not unit.strip():
        return body
    return f"{body} {unit_value(unit, resolver) or unit.strip()}"


def format_boolean(value: bool | None) -> str | None:
    return None if value is None else ("Y" if value else "N")

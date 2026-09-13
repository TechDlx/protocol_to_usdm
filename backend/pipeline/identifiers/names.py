"""Deterministic names for cross-referenceable entities.

USDM workbooks cross-reference entities by `name` in one study-wide namespace, so names must be
unique, stable across re-runs, and safe inside comma-separated reference lists. The model never
produces names; they are derived from extracted content here.
"""

import re

_UNSAFE = re.compile(r"[,;\"'\n\r\t]+")
_WS = re.compile(r"\s+")
MAX_NAME_LENGTH = 60


def safe_name(text: str, fallback: str) -> str:
    """A readable name: no commas/quotes (they break reference lists), trimmed, bounded."""
    cleaned = _WS.sub(" ", _UNSAFE.sub(" ", text or "")).strip()
    return cleaned[:MAX_NAME_LENGTH].rstrip() or fallback


class NameRegistry:
    """Hands out unique names within one namespace, deterministically by call order."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def claim(self, preferred: str) -> str:
        candidate, n = preferred, 2
        while candidate.casefold() in self._used:
            suffix = f" {n}"
            candidate = f"{preferred[: MAX_NAME_LENGTH - len(suffix)]}{suffix}"
            n += 1
        self._used.add(candidate.casefold())
        return candidate


def criterion_name(category_code: str | None, index: int) -> str:
    """IN01, IN02 ... / EX01 ... numbered within their category (the CDISC Pilot convention)."""
    prefix = {"C25532": "IN", "C25370": "EX"}.get(category_code or "", "IE")
    return f"{prefix}{index:02d}"


def governance_date_name(category: str, type_term: str | None, index: int) -> str:
    kind = re.sub(r"[^A-Z0-9]+", "_", (type_term or "DATE").upper()).strip("_") or "DATE"
    return f"{category.upper()}_{kind}_{index}"

"""Name -> PDF extraction backend. run_config.json selects one by name."""

from backend.pipeline.extractors.base import PdfExtractor
from backend.pipeline.extractors.pymupdf_extractor import PyMuPdfExtractor

_BACKENDS: dict[str, type[PdfExtractor]] = {
    PyMuPdfExtractor.name: PyMuPdfExtractor,
}


class UnknownExtractorError(ValueError):
    pass


def available_extractors() -> list[str]:
    return sorted(_BACKENDS)


def get_extractor(name: str) -> PdfExtractor:
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise UnknownExtractorError(
            f"unknown PDF backend {name!r}; available: {', '.join(available_extractors())}"
        ) from None

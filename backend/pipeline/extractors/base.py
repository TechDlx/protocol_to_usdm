"""Common interface for PDF extraction backends.

A backend turns a protocol PDF into a ParsedDocument and writes one image per page. Everything
downstream (segmentation, agents, review UI) depends only on ParsedDocument, so backends can be
swapped per run via run_config.json without touching the rest of the pipeline.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from backend.models.document import ParsedDocument, SourceInfo


class PdfExtractor(ABC):
    #: Stable identifier stored in run_config.json and parsed_document.json.
    name: str
    #: Bump when output changes for the same input, so cached parses are invalidated.
    version: str

    @abstractmethod
    def extract(
        self,
        pdf_path: Path,
        source: SourceInfo,
        page_images_dir: Path,
        image_path_prefix: str,
        dpi: int,
    ) -> ParsedDocument:
        """Parse `pdf_path`.

        Page images are written to `page_images_dir`; `PageInfo.image_path` records them as
        `image_path_prefix/<file>` so the stored paths are relative to the run folder.
        """

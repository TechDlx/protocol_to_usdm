"""Cell-level evaluation against the CDISC Pilot reference workbook.

    python -m uv run python goldstandard/eval.py              # Stage A (resumable) + score
    python -m uv run python goldstandard/eval.py --run-dir studies/<study>/runs/<run-id>
    python -m uv run python goldstandard/eval.py --workbook path/to/workbook.xlsx
    python -m uv run python goldstandard/eval.py --help

The harness lives in backend/pipeline/evaluation/; results go to goldstandard/results/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.pipeline.evaluation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

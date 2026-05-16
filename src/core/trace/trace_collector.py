"""Trace collector – receives finished TraceContext and persists them.

The collector is the bridge between in-memory TraceContext objects and
the on-disk JSON Lines log used by the Dashboard.  It is intentionally
decoupled from the logging module so that trace persistence remains
predictable and testable.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from src.core.settings import get_trace_file_path
from src.core.trace.trace_context import TraceContext

logger = logging.getLogger(__name__)

class TraceCollector:
    """Collects finished traces and appends them to a JSON Lines file.

    Args:
        traces_path: File path for the ``traces.jsonl`` output.
            Parent directories are created automatically.
    """

    def __init__(self, traces_path: Optional[str | Path] = None) -> None:
        self._path = Path(traces_path) if traces_path is not None else get_trace_file_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def collect(self, trace: TraceContext) -> None:
        """Persist a single trace as one JSON line.

        If the trace has not been finished yet, ``finish()`` is called
        automatically so the output always contains timing data.

        Args:
            trace: A populated :class:`TraceContext`.
        """
        if trace.finished_at is None:
            trace.finish()

        line = json.dumps(trace.to_dict(), ensure_ascii=False)
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.exception("Failed to write trace %s", trace.trace_id)

    @property
    def path(self) -> Path:
        """Return the resolved path of the traces file."""
        return self._path

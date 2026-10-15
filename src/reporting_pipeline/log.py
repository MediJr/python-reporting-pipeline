"""Structured logging: JSON lines in the run folder, short readable lines on the console (stderr)."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

LOGGER_NAME = "reporting_pipeline"


class JsonLinesHandler(logging.Handler):
    """One JSON object per event: ts (UTC), level, event, run_id and any ``data`` passed in ``extra``."""

    def __init__(self, path: Path, run_id: str) -> None:
        super().__init__(logging.DEBUG)
        self.handle_file = path.open("a", encoding="utf-8", newline="\n")
        self.run_id = run_id

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "run_id": self.run_id,
            "event": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if data:
            entry["data"] = data
        self.handle_file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        self.handle_file.flush()

    def close(self) -> None:
        self.handle_file.close()
        super().close()


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    return logger


def configure_console(verbose: int) -> None:
    logger = get_logger()
    for handler in [h for h in logger.handlers if getattr(h, "_rp_console", False)]:
        logger.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG if verbose > 1 else logging.INFO if verbose else logging.WARNING)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler._rp_console = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False

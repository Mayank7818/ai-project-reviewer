"""Logging setup.

A single place that configures the root logger so application logs and uvicorn
logs share one format. Called once from the application factory.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(debug: bool = False) -> None:
    """Attach a single stdout handler to the root logger.

    Idempotent: repeated calls replace the handlers rather than stacking them,
    which matters under uvicorn's auto-reload.
    """
    level = logging.DEBUG if debug else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(handler)

    # Let uvicorn's loggers bubble up to the root handler instead of printing
    # in their own format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # The HTTP stack logs every connection and header exchange at DEBUG. That is
    # occasionally useful when debugging a GitHub call by hand, and never useful
    # in a log anyone else reads - it buries the application's own lines and
    # records upstream detail this service has no reason to keep.
    for name in ("httpcore", "httpx", "hpack", "h11"):
        logging.getLogger(name).setLevel(max(level, logging.INFO))


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper so modules never import `logging` directly."""
    return logging.getLogger(name)

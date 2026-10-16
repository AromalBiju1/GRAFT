"""Application logging setup.

Every module in GRAFT uses ``logging.getLogger(__name__)``, but nothing called
``basicConfig`` or ``dictConfig``. With no handler on the root logger, records
at WARNING and above still surface via Python's last-resort handler, while
everything at INFO or below — including the startup-warmup timing in
:mod:`api.main` and the embedder-fallback warnings in :mod:`embeddings` —
was silently discarded.

This module is the single place that decides where records go. ``configure_logging``
is idempotent and safe to call from a FastAPI lifespan handler, a CLI entry
point, or a test.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

#: Env var that raises the verbosity: DEBUG when "debug", INFO when "info".
LOG_LEVEL_ENV = "GRAFT_LOG_LEVEL"

#: Loggers for third-party libraries that are otherwise extremely chatty at
#: INFO/DEBUG and drown out our own records.
NOISY_LOGGERS = (
    "chromadb",
    "httpx",
    "httpcore",
    "sentence_transformers",
    "transformers",
    "urllib3",
)

DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def resolve_level(level: str | int | None = None) -> int:
    """Return a numeric log level from a name or number."""
    if isinstance(level, int):
        return level
    import os

    raw = level if level is not None else os.environ.get(LOG_LEVEL_ENV, "INFO")
    if isinstance(raw, str):
        return getattr(logging, raw.strip().upper(), logging.INFO)
    return logging.INFO


def configure_logging(level: str | int | None = None, *, force: bool = False) -> None:
    """Configure root logging for the application.

    Idempotent: calling twice is a no-op unless ``force`` is set, so importing
    this module and then starting the server does not double up handlers.

    Args:
        level: level name or number. Defaults to ``$GRAFT_LOG_LEVEL`` or INFO.
        force: replace existing root handlers instead of leaving them alone.
    """
    root = logging.getLogger()
    if root.handlers and not force:
        # Already configured. Still honour an explicitly requested level, so a
        # caller asking for DEBUG after something else initialised logging gets
        # DEBUG rather than silently keeping the earlier level.
        if level is not None:
            root.setLevel(resolve_level(level))
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolve_level(level))

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str, level: str | int | None = None) -> logging.Logger:
    """Return a module logger, configuring logging on first use.

    Convenience for entry points and CLI modules that log before the app has
    started.
    """
    configure_logging(level)
    return logging.getLogger(name)


def logging_config_dict(level: str | int | None = None) -> dict[str, Any]:
    """Return a ``logging.config.dictConfig`` schema.

    Useful for deployments that want to configure logging declaratively rather
    than by importing this module.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": DEFAULT_FORMAT}},
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"}
        },
        "root": {"handlers": ["console"], "level": logging.getLevelName(resolve_level(level))},
        "loggers": {name: {"level": "WARNING"} for name in NOISY_LOGGERS},
    }


__all__ = [
    "DEFAULT_FORMAT",
    "LOG_LEVEL_ENV",
    "NOISY_LOGGERS",
    "configure_logging",
    "get_logger",
    "logging_config_dict",
    "resolve_level",
]

"""Logging configuration helpers for the FastAPI server."""

from __future__ import annotations

import logging.config
import os
from pathlib import Path
from typing import Any, Dict

_logging_configured = False


def configure_logging() -> None:
    """Setup uvicorn-compatible logging with rotating file handlers."""
    global _logging_configured
    if _logging_configured:
        return

    log_dir = Path(os.getenv("UVICORN_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    log_level = os.getenv("UVICORN_LOG_LEVEL", "INFO")
    log_path = log_dir / os.getenv("UVICORN_LOG_FILE", "uvicorn.log")
    access_log_path = log_dir / os.getenv("UVICORN_ACCESS_LOG_FILE", "uvicorn-access.log")

    logging_config: Dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": "%(levelprefix)s %(client_addr)s - \"%(request_line)s\" %(status_code)s",
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "default",
                "filename": str(log_path),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
                "delay": True,
            },
            "access_stream": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "stream": "ext://sys.stdout",
            },
            "access_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "access",
                "filename": str(access_log_path),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
                "delay": True,
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default", "file"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["default", "file"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access_stream", "access_file"],
                "level": log_level,
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["default", "file"],
            "level": log_level,
        },
    }

    logging.config.dictConfig(logging_config)
    _logging_configured = True


__all__ = ["configure_logging"]

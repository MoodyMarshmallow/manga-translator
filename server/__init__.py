"""Manga translator backend package."""

from fastapi import FastAPI

from .main import app as _app

app: FastAPI = _app

__all__ = ["app"]

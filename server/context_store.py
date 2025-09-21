"""Lightweight conversation context persistence for translations."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, TypedDict, cast


class ContextEntry(TypedDict, total=False):
    kr: str
    en: str
    timestamp: float


class ContextStore:
    def __init__(self, path: Path, max_entries: int = 200, max_context_return: int = 40) -> None:
        self._path = path
        self._max_entries = max_entries
        self._max_context_return = max_context_return
        self._lock = threading.Lock()
        self._data: Dict[str, List[ContextEntry]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                raw: Any = json.load(fh)
        except (json.JSONDecodeError, OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        raw_dict = cast(Dict[str, List[dict[str, object]]], raw)
        data: Dict[str, List[ContextEntry]] = {}
        for key, value in raw_dict.items():
            items: List[ContextEntry] = []
            for entry in value:
                kr_value = entry.get("kr")
                en_value = entry.get("en")
                timestamp_value = entry.get("timestamp")
                kr_text = kr_value if isinstance(kr_value, str) else ""
                en_text = en_value if isinstance(en_value, str) else ""
                if isinstance(timestamp_value, (int, float)):
                    timestamp = float(timestamp_value)
                elif isinstance(timestamp_value, str):
                    try:
                        timestamp = float(timestamp_value)
                    except ValueError:
                        timestamp = time.time()
                else:
                    timestamp = time.time()
                mapped = cast(
                    ContextEntry,
                    {
                        "kr": kr_text,
                        "en": en_text,
                        "timestamp": timestamp,
                    },
                )
                items.append(mapped)
            if items:
                data[key] = items
        self._data = data

    def _persist(self) -> None:
        tmp_path = self._path.with_suffix(".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False)
            tmp_path.replace(self._path)
        except OSError:
            tmp_path.unlink(missing_ok=True)

    def get_recent(self, conversation_id: str, limit: int | None = None) -> List[ContextEntry]:
        if not conversation_id:
            return []
        with self._lock:
            history = self._data.get(conversation_id, [])
            limit = limit or self._max_context_return
            return history[-limit:]

    def append(self, conversation_id: str, entries: List[ContextEntry]) -> None:
        if not conversation_id or not entries:
            return
        now = time.time()
        clean_entries: List[ContextEntry] = []
        for entry in entries:
            if not entry.get("kr") and not entry.get("en"):
                continue
            mapped = cast(
                ContextEntry,
                {
                    "kr": entry.get("kr", ""),
                    "en": entry.get("en", ""),
                    "timestamp": entry.get("timestamp", now),
                },
            )
            clean_entries.append(mapped)
        if not clean_entries:
            return
        with self._lock:
            history = self._data.setdefault(conversation_id, [])
            history.extend(clean_entries)
            if len(history) > self._max_entries:
                del history[:-self._max_entries]
            self._persist()


_default_path = Path(__file__).with_name("conversation_history.json")
context_store = ContextStore(_default_path)


__all__ = ["ContextEntry", "ContextStore", "context_store"]

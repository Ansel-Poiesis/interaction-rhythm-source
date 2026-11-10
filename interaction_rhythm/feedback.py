"""本机反馈保存与导出。

反馈默认只写入本地 `data/feedback.json`，不调用任何外部服务；用户可以按需把
反馈包导出为 JSON，自行交给开发者或支持方。这样在保持“默认不出站”边界的同时，
给访客/用户一个低摩擦的反馈通路。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from interaction_rhythm.runtime_paths import resolve_data_dir


ALLOWED_CATEGORIES = ("bug", "idea", "question", "praise", "other")
MAX_ENTRIES = 200
MAX_SUBJECT_LENGTH = 200
MAX_MESSAGE_LENGTH = 4000
MAX_CONTACT_LENGTH = 200


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def _data_dir() -> Path:
    return resolve_data_dir(_app_root())


def _feedback_path() -> Path:
    return _data_dir() / "feedback.json"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class FeedbackStore:
    """线程安全地追加、读取并导出本地反馈。"""

    def __init__(self, data_dir: Path | None = None, app_version: str = "0.0.0"):
        self._data_dir = data_dir or _data_dir()
        self._version = str(app_version or "0.0.0").strip()
        self._path = self._data_dir / "feedback.json"
        self._lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [entry for entry in raw if isinstance(entry, dict)]
        except Exception:
            pass
        return []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    def add(
        self,
        category: str,
        subject: str,
        message: str,
        contact: str = "",
    ) -> dict[str, Any]:
        category = str(category or "").strip().lower() or "other"
        if category not in ALLOWED_CATEGORIES:
            raise ValueError("unsupported feedback category")
        subject = str(subject or "").strip()[:MAX_SUBJECT_LENGTH]
        message = str(message or "").strip()[:MAX_MESSAGE_LENGTH]
        contact = str(contact or "").strip()[:MAX_CONTACT_LENGTH]
        if not subject or not message:
            raise ValueError("subject and message are required")

        now = time.time()
        entry = {
            "id": uuid.uuid4().hex,
            "created_at": now,
            "created_at_iso": datetime.fromtimestamp(now).astimezone().isoformat(
                timespec="seconds"
            ),
            "category": category,
            "subject": subject,
            "message": message,
            "contact": contact,
            "app_version": self._version,
        }

        with self._lock:
            entries = self._load()
            entries.append(entry)
            if len(entries) > MAX_ENTRIES:
                entries = entries[-MAX_ENTRIES:]
            self._save(entries)
        return entry

    def list_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = self._load()
        return list(reversed(entries))

    def export_bundle(self) -> dict[str, Any]:
        with self._lock:
            entries = self._load()
        return {
            "format": "interaction-rhythm-feedback",
            "version": 1,
            "app_version": self._version,
            "exported_at": _now_iso(),
            "count": len(entries),
            "entries": list(reversed(entries)),
        }


def feedback_path_for(app_root: Path) -> Path:
    """返回给定应用根目录对应的反馈文件路径。"""
    return resolve_data_dir(app_root) / "feedback.json"

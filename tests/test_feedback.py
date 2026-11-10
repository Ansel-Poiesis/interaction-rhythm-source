"""本地反馈保存与导出的边界测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from interaction_rhythm.feedback import FeedbackStore


def test_feedback_add_list_export(tmp_path: Path) -> None:
    store = FeedbackStore(data_dir=tmp_path, app_version="0.11.3")
    entry = store.add(
        category="bug",
        subject="窗口偶尔无法拖动",
        message="重新打开后恢复。",
        contact="someone@example.com",
    )

    assert entry["category"] == "bug"
    assert entry["subject"] == "窗口偶尔无法拖动"
    assert entry["app_version"] == "0.11.3"

    entries = store.list_entries()
    assert len(entries) == 1
    assert entries[0]["id"] == entry["id"]

    bundle = store.export_bundle()
    assert bundle["format"] == "interaction-rhythm-feedback"
    assert bundle["count"] == 1
    assert bundle["entries"][0]["message"] == "重新打开后恢复。"

    on_disk = json.loads((tmp_path / "feedback.json").read_text(encoding="utf-8"))
    assert isinstance(on_disk, list)
    assert on_disk[0]["contact"] == "someone@example.com"


def test_feedback_requires_subject_and_message(tmp_path: Path) -> None:
    store = FeedbackStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        store.add(category="idea", subject="", message="")
    with pytest.raises(ValueError):
        store.add(category="idea", subject="标题", message="   ")


def test_feedback_rejects_unknown_category(tmp_path: Path) -> None:
    store = FeedbackStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        store.add(category="spam", subject="标题", message="内容")


def test_feedback_truncates_long_fields(tmp_path: Path) -> None:
    store = FeedbackStore(data_dir=tmp_path)
    entry = store.add(
        category="other",
        subject="长" * 500,
        message="长" * 9000,
        contact="长" * 500,
    )
    assert len(entry["subject"]) == 200
    assert len(entry["message"]) == 4000
    assert len(entry["contact"]) == 200

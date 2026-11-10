"""Small local diagnostics logger for maintenance builds."""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from interaction_rhythm.runtime_paths import resolve_data_dir


def app_root() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def data_dir() -> Path:
    return resolve_data_dir(app_root())


def diagnostics_dir() -> Path:
    path = data_dir() / "diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def diagnostics_log_path() -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d")
    return diagnostics_dir() / f"diagnostics-{stamp}.jsonl"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def log_event(event: str, detail: Any | None = None, error: BaseException | None = None) -> None:
    try:
        record: dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "app": config.APP_NAME,
            "version": config.APP_VERSION,
            "event": str(event),
            "frozen": bool(getattr(sys, "frozen", False)),
        }
        if detail is not None:
            record["detail"] = _jsonable(detail)
        if error is not None:
            record["error"] = {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            }
        with diagnostics_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def log_exception(event: str, error: BaseException, detail: Any | None = None) -> None:
    log_event(event, detail=detail, error=error)


def latest_events(limit: int = 80) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 80), 500))
    rows: list[dict[str, Any]] = []
    try:
        for path in sorted(diagnostics_dir().glob("diagnostics-*.jsonl"), reverse=True):
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(rows) >= limit:
                    return rows
    except Exception:
        return rows
    return rows

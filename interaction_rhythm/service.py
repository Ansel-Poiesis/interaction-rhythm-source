"""与 HTTP/pywebview 无关的应用服务边界。"""
from __future__ import annotations

import hmac
from datetime import datetime
import queue
import secrets
import threading
import time
import uuid
from http.cookies import SimpleCookie


class SessionGuard:
    COOKIE_NAME = "interaction_rhythm_session"

    def __init__(self, host: str, port: int, token: str | None = None):
        self.host = host
        self.port = int(port)
        self.token = token or secrets.token_urlsafe(32)
        self.allowed_hosts = {f"{host}:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{value}" for value in self.allowed_hosts}

    def cookie_header(self) -> str:
        return (
            f"{self.COOKIE_NAME}={self.token}; Path=/; HttpOnly; "
            "SameSite=Strict"
        )

    def authorize(self, host: str, origin: str | None, cookie_header: str) -> bool:
        if (host or "").lower() not in self.allowed_hosts:
            return False
        if origin and origin.rstrip("/").lower() not in self.allowed_origins:
            return False
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header or "")
        except Exception:
            return False
        morsel = cookie.get(self.COOKIE_NAME)
        return bool(morsel and hmac.compare_digest(morsel.value, self.token))


class ReplayJobManager:
    def __init__(self, replay_fn, max_jobs: int = 32):
        self._replay_fn = replay_fn
        self._max_jobs = max(4, int(max_jobs))
        self._jobs: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._queue = queue.Queue(maxsize=self._max_jobs)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def submit(self, date: str) -> dict:
        job_id = uuid.uuid4().hex
        now = time.time()
        record = {
            "job_id": job_id,
            "date": date,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._prune_locked()
            self._jobs[job_id] = record
            self._events[job_id] = threading.Event()
            submitted = dict(record)
        try:
            self._queue.put_nowait(job_id)
        except queue.Full as exc:
            with self._lock:
                self._jobs.pop(job_id, None)
                self._events.pop(job_id, None)
            raise RuntimeError("replay queue is full") from exc
        return submitted

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._run(job_id)
            finally:
                self._queue.task_done()

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                return
            record["status"] = "running"
            record["updated_at"] = time.time()
            date = record["date"]
        try:
            result = self._replay_fn(date)
            update = {"status": "completed", "result": result}
        except Exception as exc:
            update = {"status": "failed", "error": str(exc)}
        with self._lock:
            record = self._jobs.get(job_id)
            if record:
                record.update(update)
                record["updated_at"] = time.time()
                self._events[job_id].set()

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            record = self._jobs.get(job_id)
            return dict(record) if record else None

    def wait(self, job_id: str, timeout: float | None = None) -> dict | None:
        with self._lock:
            event = self._events.get(job_id)
        if not event:
            return None
        event.wait(timeout)
        return self.get(job_id)

    def _prune_locked(self) -> None:
        if len(self._jobs) < self._max_jobs:
            return
        completed = sorted(
            (
                (record["updated_at"], job_id)
                for job_id, record in self._jobs.items()
                if record["status"] in {"completed", "failed"}
            )
        )
        for _, job_id in completed[: max(1, len(self._jobs) - self._max_jobs + 1)]:
            self._jobs.pop(job_id, None)
            self._events.pop(job_id, None)


class AgentDeepReadJobManager:
    def __init__(self, deep_read_fn, max_jobs: int = 8):
        self._deep_read_fn = deep_read_fn
        self._max_jobs = max(2, int(max_jobs))
        self._jobs: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._queue = queue.Queue(maxsize=self._max_jobs)
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def submit(self, end_date: str, purpose_id: str, thinking_enabled: bool = False, scope: str = "week") -> dict:
        job_id = uuid.uuid4().hex
        now = time.time()
        record = {
            "job_id": job_id,
            "end_date": str(end_date or "").strip(),
            "purpose_id": str(purpose_id or "").strip(),
            "thinking_enabled": bool(thinking_enabled),
            "scope": scope if scope in {"day", "week"} else "week",
            "status": "queued",
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._prune_locked()
            self._jobs[job_id] = record
            self._events[job_id] = threading.Event()
            submitted = dict(record)
        try:
            self._queue.put_nowait(job_id)
        except queue.Full as exc:
            with self._lock:
                self._jobs.pop(job_id, None)
                self._events.pop(job_id, None)
            raise RuntimeError("agent deep read queue is full") from exc
        return submitted

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self._run(job_id)
            finally:
                self._queue.task_done()

    def _run(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if not record:
                return
            record["status"] = "running"
            record["updated_at"] = time.time()
            payload = {
                "end_date": record["end_date"],
                "purpose_id": record["purpose_id"],
                "thinking_enabled": record["thinking_enabled"],
                "scope": record["scope"],
            }
        try:
            result = self._deep_read_fn(payload)
            update = {"status": "completed", "result": result}
        except Exception as exc:
            update = {"status": "failed", "error": str(exc)}
        with self._lock:
            record = self._jobs.get(job_id)
            if record:
                record.update(update)
                record["updated_at"] = time.time()
                self._events[job_id].set()

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            record = self._jobs.get(job_id)
            return dict(record) if record else None

    def wait(self, job_id: str, timeout: float | None = None) -> dict | None:
        with self._lock:
            event = self._events.get(job_id)
        if not event:
            return None
        event.wait(timeout)
        return self.get(job_id)

    def _prune_locked(self) -> None:
        if len(self._jobs) < self._max_jobs:
            return
        completed = sorted(
            (
                (record["updated_at"], job_id)
                for job_id, record in self._jobs.items()
                if record["status"] in {"completed", "failed"}
            )
        )
        for _, job_id in completed[: max(1, len(self._jobs) - self._max_jobs + 1)]:
            self._jobs.pop(job_id, None)
            self._events.pop(job_id, None)


class ApplicationService:
    """业务查询和命令的稳定入口。"""

    def __init__(self, db, shutdown_callback=None):
        self.db = db
        self.shutdown_callback = shutdown_callback
        self.replay_jobs = ReplayJobManager(self._generate_replay)
        self.agent_jobs = AgentDeepReadJobManager(self._generate_agent_deep_read)
        self.email_reports = None
        self._email_reports_lock = threading.Lock()

    def email_report_manager(self):
        with self._email_reports_lock:
            if self.email_reports is None:
                from interaction_rhythm.email_reports import EmailReports
                self.email_reports = EmailReports(self.db)
            return self.email_reports

    def _generate_agent_deep_read(self, payload: dict) -> dict:
        from mimo_client import agent_deep_read

        feed = self.weekly_observations(
            payload["end_date"],
            payload["purpose_id"],
        )
        if payload.get("scope") == "day":
            feed["daily_evidence"] = self.agent_day(payload["end_date"])
        return agent_deep_read(
            feed,
            thinking_enabled=bool(payload.get("thinking_enabled")),
            db=self.db,
        )

    def _generate_replay(self, date: str) -> dict:
        from mimo_client import mimo_day_replay

        try:
            return mimo_day_replay(self.db, date)
        except Exception as exc:
            fallback = self.db.day_replay(date)
            fallback.update({
                "ok": True,
                "fallback": True,
                "fallback_reason": str(exc),
            })
            return fallback

    def submit_replay(self, date: str) -> dict:
        return self.replay_jobs.submit(date)

    def replay_status(self, job_id: str) -> dict | None:
        return self.replay_jobs.get(job_id)

    def submit_agent_deep_read(
        self,
        end_date: str,
        purpose_id: str,
        thinking_enabled: bool = False,
        scope: str = "week",
    ) -> dict:
        return self.agent_jobs.submit(end_date, purpose_id, thinking_enabled, scope)

    def agent_deep_read_status(self, job_id: str) -> dict | None:
        return self.agent_jobs.get(job_id)

    def agent_corrections(self, end_date: str, purpose_id: str) -> dict:
        return {
            "ok": True,
            "end_date": str(end_date or "").strip(),
            "purpose_id": str(purpose_id or "").strip(),
            "corrections": self.db.list_agent_corrections(end_date, purpose_id),
        }

    def save_agent_correction(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("缺少校正内容")
        kind = str(body.get("kind") or "").strip().lower()
        if kind not in {"correction", "ignore", "rename", "retract"}:
            raise ValueError("unsupported correction kind")
        observation_id = str(body.get("observation_id") or "").strip()
        end_date = str(body.get("end_date") or "").strip()
        purpose_id = str(body.get("purpose_id") or "").strip()
        if not observation_id or not end_date or not purpose_id:
            raise ValueError("缺少 observation_id / end_date / purpose_id")
        if kind == "correction" and not str(body.get("corrected_text") or "").strip():
            raise ValueError("correction 需要 corrected_text")
        if kind == "rename" and not str(body.get("label") or "").strip():
            raise ValueError("rename 需要 label")
        saved = self.db.save_agent_correction(
            observation_id=observation_id,
            end_date=end_date,
            purpose_id=purpose_id,
            kind=kind,
            original_text=str(body.get("original_text") or "").strip(),
            corrected_text=str(body.get("corrected_text") or "").strip(),
            label=str(body.get("label") or "").strip(),
            evidence_hash=str(body.get("evidence_hash") or "").strip(),
        )
        return {"ok": True, "correction": saved}

    def app_labels(self) -> dict:
        return {"ok": True, "labels": self.db.app_label_map()}

    def save_app_label(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("缺少标签内容")
        app = str(body.get("app") or "").strip()
        label = str(body.get("label") or "").strip()
        if not app:
            raise ValueError("缺少应用名")
        saved = self.db.save_app_label(app, label)
        return {"ok": True, "label": saved}

    def trend(self, weeks: int = 12) -> dict:
        return self.db.trend_summary(weeks)

    def return_intervals(self, days: int = 7) -> dict:
        return self.db.return_interval_summary(days)

    def today(self) -> dict:
        return self.db.today_stats()

    def day(self, date: str) -> dict:
        return self.db.day_stats(date)

    def day_metrics(self, date: str) -> dict:
        return self.db.day_metrics(date)

    def personal_reference(self, metric_id: str, date: str) -> dict:
        return self.db.personal_reference(metric_id, date)

    def heatmap(self, days: int, tomorrow: bool, end_date: str) -> dict:
        return {
            "days": days,
            "data": self.db.heatmap_data(days, tomorrow, end_date),
        }

    def month_heatmap(self, month: str) -> dict:
        return self.db.month_heatmap(month)

    def year_heatmap(self, year: str) -> dict:
        return self.db.year_heatmap(year)

    def hour_detail(self, date: str, hour: int) -> dict:
        return self.db.hour_detail(date, hour)

    def applications(self, date: str) -> dict:
        return {"date": date, "apps": self.db.app_chart(date)}

    def application_detail(self, app: str, date: str) -> dict:
        return self.db.app_detail(app, date)

    def application_analysis(self, app: str, days: int) -> dict:
        return self.db.app_analysis(app, days)

    def export(self, days: int) -> dict:
        return self.db.export_data(days)

    def day_replay(self, date: str) -> dict:
        return self.db.day_replay(date)

    def ai_config(self) -> dict:
        from interaction_rhythm.ai_config import load_ai_config, public_base_url
        from mimo_client import current_agent_prompt

        config = load_ai_config()
        return {
            "ok": True,
            "provider": config.provider,
            "base_url": public_base_url(config.base_url),
            "model": config.model,
            "cloud_enabled": bool(config.cloud_enabled),
            "key_configured": bool(config.api_key),
            "agent_prompt": current_agent_prompt(),
        }

    def ai_preview(self, date: str) -> dict:
        from mimo_client import ai_context_preview

        return ai_context_preview(self.db, date)

    def media_snapshot(self, date: str) -> dict:
        return self.db.media_snapshot(date)

    def insights(self, days: int) -> dict:
        return self.db.insights(days)

    @staticmethod
    def agent_purposes() -> dict:
        from interaction_rhythm.agent_observations import purpose_catalog

        return purpose_catalog()

    def weekly_observations(self, end_date: str, purpose_id: str) -> dict:
        from interaction_rhythm.agent_observations import (
            apply_user_corrections,
            build_observation_feed,
        )
        from interaction_rhythm.theory_catalog import REVIEWED_THEORY_CARDS

        feed = build_observation_feed(
            self.db,
            end_date,
            purpose_id,
            REVIEWED_THEORY_CARDS,
        )
        return apply_user_corrections(
            feed,
            self.db.list_agent_corrections(end_date, purpose_id),
        )

    def agent_day(self, date: str) -> dict:
        """Return the complete, traceable single-day Agent evidence surface."""
        from mimo_client import _compact_day_context

        context = _compact_day_context(self.db, date)
        coverage = context.get("coverage") or {}
        status = coverage.get("status", "insufficient_data")
        if date == datetime.now().strftime("%Y-%m-%d"):
            status = "in_progress" if status == "verified" else status
        return {
            "ok": True,
            "surface": "daily_agent_evidence",
            "date": date,
            "timezone": str(datetime.now().astimezone().tzinfo),
            "data_status": status,
            "evidence": context,
        }

    def daily(self, days: int) -> dict:
        return {
            "range": self.db.date_range(),
            "daily": self.db.daily_keystrokes(days),
        }

    @staticmethod
    def metric_registry() -> dict:
        from interaction_rhythm.metrics import registry_payload

        return registry_payload()

    @staticmethod
    def settings() -> dict:
        from settings import get_settings

        payload = get_settings()
        # 数据目录是只读本机事实，随设置一并返回供设置页展示；
        # save_settings 只接受白名单键，该字段不会回写。
        import sys
        from pathlib import Path

        from .runtime_paths import resolve_data_dir

        app_root = (
            Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
        )
        payload["data_root"] = str(resolve_data_dir(app_root))
        return {"ok": True, "settings": payload}

    @staticmethod
    def save_settings(body: dict) -> dict:
        from settings import save_settings

        return {"ok": True, "settings": save_settings(body)}

    @staticmethod
    def update_check() -> dict:
        from update_manager import check_update

        return check_update()

    @staticmethod
    def update_status() -> dict:
        from update_manager import update_status

        return update_status()

    def update_install(self, manifest: dict) -> dict:
        from update_manager import install_update_async

        return install_update_async(manifest, self.shutdown_callback)

    def compact_database(self) -> dict:
        """VACUUM the SQLite database to reclaim space freed by cleanup."""
        before = self.db.file_size()
        self.db.compact()
        return {
            "ok": True,
            "before_bytes": before,
            "after_bytes": self.db.file_size(),
        }

    def delete_historical_window_titles(self) -> dict:
        deleted = self.db.delete_historical_window_titles()
        return {"ok": True, "deleted_rows": deleted}

    def _data_backup_manager(self) -> "DataBackupManager":
        from config import APP_VERSION
        from interaction_rhythm.data_backup import DataBackupManager

        def _pause():
            try:
                self.db.checkpoint()
            except Exception:
                pass

        return DataBackupManager(
            app_version=APP_VERSION,
            pause_callback=_pause,
        )

    def list_data_backups(self) -> dict:
        manager = self._data_backup_manager()
        return {
            "ok": True,
            "backups": [b.as_dict() for b in manager.list_backups()],
            "backup_root": str(manager.backup_root),
        }

    def create_data_backup(self, body: dict | None = None) -> dict:
        name = ""
        if isinstance(body, dict):
            name = str(body.get("name") or "").strip()
        manager = self._data_backup_manager()
        info = manager.create_backup(name=name, source="manual", is_auto=False)
        return {"ok": True, "backup": info.as_dict()}

    def restore_data_backup(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("缺少请求体")
        backup_id = str(body.get("backup_id") or "").strip()
        if not backup_id:
            raise ValueError("缺少 backup_id")
        manager = self._data_backup_manager()
        info = manager.restore_backup(backup_id)
        return {"ok": True, "backup": info.as_dict()}

    def delete_data_backup(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("缺少请求体")
        backup_id = str(body.get("backup_id") or "").strip()
        if not backup_id:
            raise ValueError("缺少 backup_id")
        manager = self._data_backup_manager()
        return manager.delete_backup(backup_id)

    def _feedback_store(self) -> "FeedbackStore":
        from config import APP_VERSION
        from interaction_rhythm.feedback import FeedbackStore

        return FeedbackStore(app_version=APP_VERSION)

    def save_feedback(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("缺少反馈内容")
        entry = self._feedback_store().add(
            category=str(body.get("category") or "other").strip(),
            subject=str(body.get("subject") or "").strip(),
            message=str(body.get("message") or "").strip(),
            contact=str(body.get("contact") or "").strip(),
        )
        return {"ok": True, "feedback": entry}

    def list_feedback(self) -> dict:
        return {
            "ok": True,
            "feedback": self._feedback_store().list_entries(),
            "stored_locally": True,
        }

    def export_feedback(self) -> dict:
        return self._feedback_store().export_bundle()

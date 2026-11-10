"""SQLite 数据库层"""
import statistics
import sqlite3
import threading
import random
import json
from collections import Counter
from pathlib import Path
from datetime import datetime, time, timedelta

from config import APP_NAME, COLLECTOR_STATES_RETENTION_DAYS
from interaction_rhythm.metrics import (
    METRIC_REGISTRY,
    METRIC_VERSION,
    coverage_result,
    personal_reference_summary,
)
from interaction_rhythm.observations import COLLECTOR_VERSION, InteractionBucket, local_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS keystrokes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS mouse_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS app_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    start TEXT NOT NULL,
    end TEXT
);
CREATE TABLE IF NOT EXISTS app_paths (
    app TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    updated TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS app_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    app TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    keyboard INTEGER NOT NULL DEFAULT 0,
    mouse INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS daily_totals (
    date TEXT PRIMARY KEY,
    keyboard INTEGER NOT NULL DEFAULT 0,
    mouse INTEGER NOT NULL DEFAULT 0,
    responses INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS insight_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    responses INTEGER NOT NULL DEFAULT 0,
    active_seconds INTEGER NOT NULL DEFAULT 0,
    top_app TEXT NOT NULL DEFAULT '',
    peak_hour INTEGER,
    summary TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS legacy_imports (
    source TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS foreground_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_name TEXT NOT NULL,
    executable_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(process_name, executable_path)
);
CREATE TABLE IF NOT EXISTS foreground_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER,
    process_name TEXT NOT NULL,
    executable_path TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    collector_version TEXT NOT NULL,
    health_state TEXT NOT NULL DEFAULT 'running',
    FOREIGN KEY(entity_id) REFERENCES foreground_entities(id)
);
CREATE TABLE IF NOT EXISTS interaction_buckets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    process_name TEXT NOT NULL,
    executable_path TEXT NOT NULL DEFAULT '',
    keyboard_down_count INTEGER NOT NULL DEFAULT 0,
    mouse_button_count INTEGER NOT NULL DEFAULT 0,
    scroll_event_count INTEGER NOT NULL DEFAULT 0,
    collector_version TEXT NOT NULL,
    health_state TEXT NOT NULL DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS observation_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    collector_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collector_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    state TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    collector_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_annotations (
    id TEXT PRIMARY KEY,
    interval_start TEXT NOT NULL,
    interval_end TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    local_only INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL,
    end_date TEXT NOT NULL,
    purpose_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    original_text TEXT NOT NULL DEFAULT '',
    corrected_text TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    evidence_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_labels (
    app TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_cache (
    cache_key TEXT PRIMARY KEY,
    metric_id TEXT NOT NULL,
    metric_version TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_key_ts ON keystrokes(ts);
CREATE INDEX IF NOT EXISTS idx_mouse_ts ON mouse_events(ts);
CREATE INDEX IF NOT EXISTS idx_app_start ON app_usage(start);
CREATE INDEX IF NOT EXISTS idx_app_name ON app_usage(app);
CREATE INDEX IF NOT EXISTS idx_interact_ts ON app_interactions(ts);
CREATE INDEX IF NOT EXISTS idx_interact_app ON app_interactions(app);
CREATE INDEX IF NOT EXISTS idx_insight_generated ON insight_history(generated_at);
CREATE INDEX IF NOT EXISTS idx_bucket_started ON interaction_buckets(started_at);
CREATE INDEX IF NOT EXISTS idx_bucket_process ON interaction_buckets(process_name);
CREATE INDEX IF NOT EXISTS idx_foreground_started ON foreground_intervals(started_at);
CREATE INDEX IF NOT EXISTS idx_observation_started ON observation_segments(started_at);
CREATE INDEX IF NOT EXISTS idx_collector_observed ON collector_states(observed_at);
CREATE INDEX IF NOT EXISTS idx_agent_corrections_lookup ON agent_corrections(end_date, purpose_id);
"""


class DB:
    def __init__(self, path: Path):
        self._path = path
        self._local = threading.local()
        self._connections = set()
        self._connections_lock = threading.Lock()
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.executescript(SCHEMA)
        self._migrate(conn)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        self._local.conn = conn
        self._connections.add(conn)

    def _migrate(self, conn: sqlite3.Connection):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(app_usage)").fetchall()}
        if "path" not in cols:
            conn.execute("ALTER TABLE app_usage ADD COLUMN path TEXT NOT NULL DEFAULT ''")
        path_cols = {row[1] for row in conn.execute("PRAGMA table_info(app_paths)").fetchall()}
        if "updated" not in path_cols:
            conn.execute("ALTER TABLE app_paths ADD COLUMN updated TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS legacy_imports ("
            "source TEXT PRIMARY KEY, "
            "fingerprint TEXT NOT NULL DEFAULT '', "
            "imported_at TEXT NOT NULL, "
            "summary TEXT NOT NULL DEFAULT '')"
        )
        now = datetime.now().isoformat(timespec="seconds")
        for key, value in (
            ("schema_version", "2"),
            ("collector_version", COLLECTOR_VERSION),
            ("metric_version", METRIC_VERSION),
            ("agent_corrections_schema_version", "1"),
        ):
            conn.execute(
                "INSERT INTO schema_meta(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now),
            )

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return bool(row)

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        if not self._table_exists(conn, table):
            return set()
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _source_fingerprint(self, path: Path) -> str:
        stat = path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def _legacy_already_imported(self, source: str, fingerprint: str) -> bool:
        row = self._conn.execute(
            "SELECT fingerprint FROM legacy_imports WHERE source=?",
            (source,),
        ).fetchone()
        return bool(row and row[0] == fingerprint)

    def _mark_legacy_imported(self, source: str, fingerprint: str, summary: dict):
        self._conn.execute(
            "INSERT OR REPLACE INTO legacy_imports(source,fingerprint,imported_at,summary) VALUES(?,?,?,?)",
            (
                source,
                fingerprint,
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _row_exists(self, table: str, fields: list[str], values: list) -> bool:
        clause = " AND ".join([f"{field} IS ?" for field in fields])
        row = self._conn.execute(
            f"SELECT 1 FROM {table} WHERE {clause} LIMIT 1",
            values,
        ).fetchone()
        return bool(row)

    def _insert_missing(self, table: str, row: dict, fields: list[str], keys: list[str]) -> bool:
        values = [row.get(field) for field in fields]
        key_values = [row.get(field) for field in keys]
        if self._row_exists(table, keys, key_values):
            return False
        placeholders = ",".join(["?"] * len(fields))
        self._conn.execute(
            f"INSERT INTO {table}({','.join(fields)}) VALUES({placeholders})",
            values,
        )
        return True

    def _import_rows(self, src: sqlite3.Connection, table: str, fields: list[str], keys: list[str]) -> int:
        columns = self._table_columns(src, table)
        if not columns:
            return 0
        available = [field for field in fields if field in columns]
        if not available:
            return 0
        inserted = 0
        for raw in src.execute(f"SELECT {','.join(available)} FROM {table}"):
            row = {field: raw[field] for field in available}
            for field in fields:
                row.setdefault(field, "")
            if self._insert_missing(table, row, fields, keys):
                inserted += 1
        return inserted

    def _import_app_paths(self, src: sqlite3.Connection) -> int:
        columns = self._table_columns(src, "app_paths")
        if not {"app", "path"}.issubset(columns):
            return 0
        select_fields = ["app", "path"] + (["updated"] if "updated" in columns else [])
        inserted_or_updated = 0
        for raw in src.execute(f"SELECT {','.join(select_fields)} FROM app_paths"):
            app = (raw["app"] or "").strip()
            path = (raw["path"] or "").strip()
            updated = raw["updated"] if "updated" in raw.keys() else ""
            if not app or not path:
                continue
            current = self._conn.execute(
                "SELECT path, updated FROM app_paths WHERE app=?",
                (app,),
            ).fetchone()
            if not current or not current[0] or (updated and updated > (current[1] or "")):
                self._conn.execute(
                    "INSERT OR REPLACE INTO app_paths(app,path,updated) VALUES(?,?,?)",
                    (app, path, updated or datetime.now().isoformat(timespec="seconds")),
                )
                inserted_or_updated += 1
        return inserted_or_updated

    def import_legacy_database(self, source_path: Path) -> dict:
        source_path = Path(source_path)
        source = str(source_path.resolve())
        current = str(self._path.resolve())
        if source == current or not source_path.exists() or source_path.stat().st_size <= 0:
            return {"source": source, "skipped": True, "reason": "same_or_missing"}

        fingerprint = self._source_fingerprint(source_path)
        if self._legacy_already_imported(source, fingerprint):
            return {"source": source, "skipped": True, "reason": "already_imported"}

        summary = {
            "source": source,
            "keystrokes": 0,
            "mouse_events": 0,
            "app_usage": 0,
            "app_paths": 0,
            "app_interactions": 0,
            "insight_history": 0,
        }
        try:
            src = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
            src.row_factory = sqlite3.Row
            try:
                summary["keystrokes"] = self._import_rows(
                    src,
                    "keystrokes",
                    ["ts", "count"],
                    ["ts", "count"],
                )
                summary["mouse_events"] = self._import_rows(
                    src,
                    "mouse_events",
                    ["ts", "count"],
                    ["ts", "count"],
                )
                summary["app_usage"] = self._import_rows(
                    src,
                    "app_usage",
                    ["app", "title", "path", "start", "end"],
                    ["app", "title", "path", "start", "end"],
                )
                summary["app_paths"] = self._import_app_paths(src)
                summary["app_interactions"] = self._import_rows(
                    src,
                    "app_interactions",
                    ["ts", "app", "path", "keyboard", "mouse"],
                    ["ts", "app", "path", "keyboard", "mouse"],
                )
                summary["insight_history"] = self._import_rows(
                    src,
                    "insight_history",
                    ["generated_at", "responses", "active_seconds", "top_app", "peak_hour", "summary"],
                    ["generated_at", "summary"],
                )
            finally:
                src.close()
        except sqlite3.DatabaseError as exc:
            summary["error"] = str(exc)
        self._mark_legacy_imported(source, fingerprint, summary)
        self._conn.commit()
        return summary

    def import_legacy_databases(self, sources: list[Path]) -> list[dict]:
        results = []
        seen = set()
        for source in sources:
            try:
                resolved = str(Path(source).resolve())
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            results.append(self.import_legacy_database(Path(resolved)))
        return results

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            if self._closed:
                raise RuntimeError("database is closed")
            c = sqlite3.connect(str(self._path), check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
            with self._connections_lock:
                if self._closed:
                    c.close()
                    del self._local.conn
                    raise RuntimeError("database is closed")
                self._connections.add(c)
        return self._local.conn

    def close(self):
        with self._connections_lock:
            self._closed = True
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        if hasattr(self._local, "conn"):
            del self._local.conn

    def close_thread_connection(self):
        connection = getattr(self._local, "conn", None)
        if connection is None:
            return
        with self._connections_lock:
            self._connections.discard(connection)
        try:
            connection.close()
        except sqlite3.Error:
            pass
        del self._local.conn

    def checkpoint(self):
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.commit()

    def _remember_app_path(self, app: str, path: str):
        if not app or not path:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO app_paths(app,path,updated) VALUES(?,?,?)",
            (app, path, datetime.now().isoformat(timespec="seconds")),
        )

    def _add_app_interaction(self, ts: str, app: str, path: str = "", keyboard: int = 0, mouse: int = 0):
        app = (app or "").strip()
        if not app or app == "unknown" or (keyboard <= 0 and mouse <= 0):
            return
        self._conn.execute(
            "INSERT INTO app_interactions(ts,app,path,keyboard,mouse) VALUES(?,?,?,?,?)",
            (ts, app, path or "", max(0, int(keyboard or 0)), max(0, int(mouse or 0))),
        )
        self._remember_app_path(app, path or "")

    @staticmethod
    def _iso(value, timespec: str = "seconds") -> str:
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value or "")
            if not raw:
                return ""
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                return raw
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.isoformat(timespec=timespec)

    @classmethod
    def _foreground_iso(cls, value) -> str:
        return cls._iso(value, timespec="microseconds")

    def _insert_interaction_bucket(self, bucket: InteractionBucket):
        started = self._iso(bucket.started_at)
        ended = self._iso(bucket.ended_at)
        app = (bucket.app or "unknown").strip() or "unknown"
        path = bucket.path or ""
        keyboard = max(0, int(bucket.keyboard_down_count or 0))
        buttons = max(0, int(bucket.mouse_button_count or 0))
        scroll = max(0, int(bucket.scroll_event_count or 0))
        self._conn.execute(
            "INSERT INTO interaction_buckets("
            "started_at,ended_at,process_name,executable_path,"
            "keyboard_down_count,mouse_button_count,scroll_event_count,"
            "collector_version,health_state) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                started,
                ended,
                app,
                path,
                keyboard,
                buttons,
                scroll,
                bucket.collector_version or COLLECTOR_VERSION,
                bucket.health_state or "unknown",
            ),
        )
        if keyboard:
            self._conn.execute(
                "INSERT INTO keystrokes(ts,count) VALUES(?,?)",
                (started, keyboard),
            )
        legacy_mouse = buttons + scroll
        if legacy_mouse:
            self._conn.execute(
                "INSERT INTO mouse_events(ts,count) VALUES(?,?)",
                (started, legacy_mouse),
            )
        self._add_app_interaction(
            started,
            app,
            path,
            keyboard=keyboard,
            mouse=legacy_mouse,
        )

    def add_interaction_bucket(self, bucket: InteractionBucket):
        try:
            self._insert_interaction_bucket(bucket)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def add_interaction_buckets(self, buckets: list[InteractionBucket]):
        rows = list(buckets or [])
        if not rows:
            return
        try:
            for bucket in rows:
                self._insert_interaction_bucket(bucket)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def add_keystrokes(self, count: int, app: str = "", path: str = ""):
        started = local_now().replace(microsecond=0)
        self.add_interaction_bucket(InteractionBucket(
            started_at=started,
            ended_at=started + timedelta(seconds=1),
            app=app or "unknown",
            path=path,
            keyboard_down_count=count,
        ))

    def add_mouse_events(self, count: int, app: str = "", path: str = ""):
        started = local_now().replace(microsecond=0)
        self.add_interaction_bucket(InteractionBucket(
            started_at=started,
            ended_at=started + timedelta(seconds=1),
            app=app or "unknown",
            path=path,
            mouse_button_count=count,
        ))

    def add_session(
        self,
        app: str,
        title: str,
        start: str,
        end: str,
        path: str = "",
        health_state: str = "running",
    ):
        self._conn.execute(
            "INSERT INTO app_usage(app,title,start,end,path) VALUES(?,?,?,?,?)",
            (app, "", start, end, path or ""),
        )
        self._add_foreground_interval(app, path, start, end, health_state)
        if app and path:
            self._remember_app_path(app, path)
        self._conn.commit()

    def _foreground_entity_id(self, app: str, path: str, now: str) -> int:
        app = (app or "unknown").strip() or "unknown"
        path = path or ""
        self._conn.execute(
            "INSERT INTO foreground_entities(process_name,executable_path,created_at,updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(process_name,executable_path) "
            "DO UPDATE SET updated_at=excluded.updated_at",
            (app, path, now, now),
        )
        row = self._conn.execute(
            "SELECT id FROM foreground_entities WHERE process_name=? AND executable_path=?",
            (app, path),
        ).fetchone()
        return int(row[0])

    def _add_foreground_interval(
        self,
        app: str,
        path: str,
        start,
        end,
        health_state: str = "running",
    ) -> None:
        start_iso = self._foreground_iso(start)
        end_iso = self._foreground_iso(end)
        if not start_iso or not end_iso or end_iso <= start_iso:
            return
        app = (app or "unknown").strip() or "unknown"
        path = path or ""
        entity_id = self._foreground_entity_id(app, path, end_iso)
        self._conn.execute(
            "INSERT INTO foreground_intervals("
            "entity_id,process_name,executable_path,started_at,ended_at,collector_version,health_state"
            ") VALUES(?,?,?,?,?,?,?)",
            (entity_id, app, path, start_iso, end_iso, COLLECTOR_VERSION, health_state),
        )

    def add_foreground_interval(
        self,
        app: str,
        path: str,
        start,
        end,
        health_state: str = "running",
    ) -> None:
        self._add_foreground_interval(app, path, start, end, health_state)
        if app and path:
            self._remember_app_path(app, path)
        self._conn.commit()

    def add_observation_segment(
        self,
        component: str,
        state: str,
        start,
        end,
        detail: str = "",
    ) -> None:
        start_iso = self._iso(start)
        end_iso = self._iso(end)
        if not start_iso or not end_iso or end_iso <= start_iso:
            return
        self._conn.execute(
            "INSERT INTO observation_segments("
            "component,state,started_at,ended_at,detail,collector_version"
            ") VALUES(?,?,?,?,?,?)",
            (component or "collector", state or "unknown", start_iso, end_iso, detail or "", COLLECTOR_VERSION),
        )
        self._conn.commit()

    def record_collector_state(
        self,
        component: str,
        state: str,
        observed_at=None,
        detail: str = "",
    ) -> None:
        observed = self._iso(observed_at or local_now())
        self._conn.execute(
            "INSERT INTO collector_states(component,state,observed_at,detail,collector_version) "
            "VALUES(?,?,?,?,?)",
            (component or "collector", state or "unknown", observed, detail or "", COLLECTOR_VERSION),
        )
        self._conn.commit()

    def collector_status_summary(self) -> dict:
        """供同机新实例判断「已有实例是否仍在采集」。

        返回最近一次采集器状态与最近活动时间（前台区间/键鼠/交互桶的最新
        时间戳）。旧版实例没有 /api/internal/status 端点时，新实例可读自己
        数据目录的该摘要做回退判断。
        """
        result = {
            "collector_state": "unknown",
            "state_at": None,
            "last_activity": None,
            "now": None,
        }
        try:
            row = self._conn.execute(
                "SELECT state, observed_at FROM collector_states "
                "WHERE component='system' ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                result["collector_state"] = row[0]
                result["state_at"] = row[1]
        except Exception:
            pass
        try:
            row = self._conn.execute(
                "SELECT MAX(t) FROM ("
                "  SELECT started_at AS t FROM foreground_intervals "
                "  UNION ALL SELECT ts FROM keystrokes "
                "  UNION ALL SELECT ts FROM mouse_events "
                "  UNION ALL SELECT started_at FROM interaction_buckets"
                ")"
            ).fetchone()
            if row and row[0]:
                result["last_activity"] = row[0]
        except Exception:
            pass
        try:
            result["now"] = datetime.now().isoformat(timespec="seconds")
        except Exception:
            pass
        return result

    def remember_app_paths(self, paths: dict[str, str]):
        rows = [(app, path) for app, path in (paths or {}).items() if app and path]
        if not rows:
            return
        updated = datetime.now().isoformat(timespec="seconds")
        self._conn.executemany(
            "INSERT OR REPLACE INTO app_paths(app,path,updated) VALUES(?,?,?)",
            [(app, path, updated) for app, path in rows],
        )
        self._conn.commit()

    def delete_historical_window_titles(self) -> int:
        cursor = self._conn.execute(
            "UPDATE app_usage SET title='' WHERE title<>''"
        )
        self._conn.commit()
        return max(0, int(cursor.rowcount or 0))

    def backfill_hourly_counts(self):
        """预留：如需从原始记录回填逐小时汇总，可在此实现。"""
        return None

    def _sum_table_today(self, table: str, today: str) -> int:
        row = self._conn.execute(
            f"SELECT COALESCE(SUM(count),0) FROM {table} WHERE ts>=?",
            (today,),
        ).fetchone()
        return int(row[0] or 0)

    def _clipped_seconds(
        self,
        table: str,
        start_column: str,
        end_column: str,
        start_iso: str,
        end_iso: str,
        where: str = "",
        params: tuple = (),
    ) -> float:
        clause = f" AND ({where})" if where else ""
        row = self._conn.execute(
            f"SELECT COALESCE(SUM(MAX(0, ("
            f"MIN(julianday({end_column}), julianday(?)) "
            f"- MAX(julianday({start_column}), julianday(?))"
            f") * 86400)), 0) FROM {table} "
            f"WHERE {start_column} < ? AND {end_column} > ?{clause}",
            (end_iso, start_iso, end_iso, start_iso, *params),
        ).fetchone()
        return float(row[0] or 0)

    def day_metrics(self, date: str) -> dict:
        date = date or datetime.now().strftime("%Y-%m-%d")
        start_dt = datetime.fromisoformat(date).replace(tzinfo=local_now().tzinfo)
        end_dt = start_dt + timedelta(days=1)
        start_iso = start_dt.isoformat(timespec="seconds")
        end_iso = end_dt.isoformat(timespec="seconds")

        event_row = self._conn.execute(
            "SELECT COALESCE(SUM(keyboard_down_count),0), "
            "COALESCE(SUM(mouse_button_count),0), "
            "COALESCE(SUM(scroll_event_count),0) "
            "FROM interaction_buckets WHERE started_at>=? AND started_at<?",
            (start_iso, end_iso),
        ).fetchone()
        keyboard, buttons, scroll = (int(value or 0) for value in event_row)
        bucket_count = self._conn.execute(
            "SELECT COUNT(*) FROM interaction_buckets WHERE started_at>=? AND started_at<?",
            (start_iso, end_iso),
        ).fetchone()[0]
        foreground_seconds = self._clipped_seconds(
            "foreground_intervals",
            "started_at",
            "ended_at",
            start_iso,
            end_iso,
            "health_state='running'",
        )
        foreground_count = self._conn.execute(
            "SELECT COUNT(*) FROM foreground_intervals "
            "WHERE julianday(started_at)<julianday(?) "
            "AND julianday(ended_at)>julianday(?)",
            (end_iso, start_iso),
        ).fetchone()[0]
        expected_seconds = self._clipped_seconds(
            "observation_segments",
            "started_at",
            "ended_at",
            start_iso,
            end_iso,
            # Unknown gaps are responsibility intervals: they belong in the
            # expected denominator and are measured separately by IR-HLT-004.
            "component='collector' AND state IN ('running','degraded','unknown')",
        )
        healthy_seconds = self._clipped_seconds(
            "observation_segments",
            "started_at",
            "ended_at",
            start_iso,
            end_iso,
            "component='collector' AND state='running'",
        )
        unknown_seconds = self._clipped_seconds(
            "observation_segments",
            "started_at",
            "ended_at",
            start_iso,
            end_iso,
            "component='collector' AND state IN ('degraded','unknown')",
        )
        observed = self._conn.execute(
            "SELECT MIN(started_at), MAX(ended_at) FROM observation_segments "
            "WHERE component='collector' AND started_at<? AND ended_at>?",
            (end_iso, start_iso),
        ).fetchone()
        coverage = coverage_result(expected_seconds, healthy_seconds, unknown_seconds)
        coverage.update({
            "first_observed_at": observed[0] if observed else None,
            "last_observed_at": observed[1] if observed else None,
            "metric_ids": ["IR-HLT-001", "IR-HLT-002", "IR-HLT-003", "IR-HLT-004"],
        })
        return {
            "date": date,
            "metric_version": METRIC_VERSION,
            "collector_version": COLLECTOR_VERSION,
            "events": {
                "keyboard_down_count": keyboard,
                "mouse_button_count": buttons,
                "scroll_event_count": scroll,
                "input_event_count": keyboard + buttons + scroll,
                "split_available": bool(bucket_count),
                "metric_ids": ["IR-EVT-001", "IR-EVT-002", "IR-EVT-003", "IR-EVT-004"],
            },
            "foreground_seconds": round(foreground_seconds),
            "foreground_available": bool(foreground_count),
            "foreground_metric_id": "IR-FG-001",
            "coverage": coverage,
            "registry": {
                metric_id: METRIC_REGISTRY[metric_id].as_dict()
                for metric_id in (
                    "IR-EVT-001", "IR-EVT-002", "IR-EVT-003", "IR-EVT-004",
                    "IR-FG-001", "IR-HLT-001", "IR-HLT-002", "IR-HLT-003", "IR-HLT-004",
                    "IR-DER-001", "IR-DER-002", "IR-DER-003", "IR-DER-004",
                    "IR-SES-001", "IR-SES-002", "IR-SES-003",
                    "IR-BAS-001", "IR-BAS-002", "IR-BAS-003",
                )
            },
        }

    def save_agent_correction(
        self,
        *,
        observation_id: str,
        end_date: str,
        purpose_id: str,
        kind: str,
        original_text: str = "",
        corrected_text: str = "",
        label: str = "",
        evidence_hash: str = "",
    ) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        cursor = self._conn.execute(
            "INSERT INTO agent_corrections("
            "observation_id,end_date,purpose_id,kind,original_text,"
            "corrected_text,label,evidence_hash,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                str(observation_id or "").strip(),
                str(end_date or "").strip(),
                str(purpose_id or "").strip(),
                str(kind or "").strip(),
                str(original_text or "").strip(),
                str(corrected_text or "").strip(),
                str(label or "").strip(),
                str(evidence_hash or "").strip(),
                now,
                now,
            ),
        )
        self._conn.commit()
        return {
            "id": int(cursor.lastrowid),
            "observation_id": str(observation_id or "").strip(),
            "end_date": str(end_date or "").strip(),
            "purpose_id": str(purpose_id or "").strip(),
            "kind": str(kind or "").strip(),
            "original_text": str(original_text or "").strip(),
            "corrected_text": str(corrected_text or "").strip(),
            "label": str(label or "").strip(),
            "evidence_hash": str(evidence_hash or "").strip(),
            "created_at": now,
        }

    def list_agent_corrections(self, end_date: str, purpose_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT observation_id,end_date,purpose_id,kind,original_text,"
            "corrected_text,label,evidence_hash,created_at "
            "FROM agent_corrections "
            "WHERE end_date=? AND purpose_id=? "
            "ORDER BY created_at ASC",
            (str(end_date or "").strip(), str(purpose_id or "").strip()),
        ).fetchall()
        return [
            {
                "observation_id": row[0],
                "end_date": row[1],
                "purpose_id": row[2],
                "kind": row[3],
                "original_text": row[4],
                "corrected_text": row[5],
                "label": row[6],
                "evidence_hash": row[7],
                "created_at": row[8],
            }
            for row in rows
        ]

    def app_label_map(self) -> dict[str, str]:
        """Return app -> user label mapping (labels survive app-name changes)."""
        rows = self._conn.execute(
            "SELECT app, label FROM app_labels WHERE label<>''"
        ).fetchall()
        return {app: label for app, label in rows}

    def save_app_label(self, app: str, label: str) -> dict:
        """Upsert a user label for an app; an empty label removes it."""
        app = (app or "").strip()
        label = (label or "").strip()[:80]
        now = datetime.now().isoformat(timespec="seconds")
        if not app:
            raise ValueError("缺少应用名")
        if label:
            self._conn.execute(
                "INSERT INTO app_labels(app,label,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(app) DO UPDATE SET "
                "label=excluded.label, updated_at=excluded.updated_at",
                (app, label, now),
            )
        else:
            self._conn.execute("DELETE FROM app_labels WHERE app=?", (app,))
        self._conn.commit()
        return {"app": app, "label": label, "updated_at": now}

    def personal_reference(
        self,
        metric_id: str,
        date: str,
        minimum_coverage_ratio: float = 0.8,
    ) -> dict:
        extractors = {
            "IR-EVT-001": lambda metrics: metrics["events"]["keyboard_down_count"],
            "IR-EVT-002": lambda metrics: metrics["events"]["mouse_button_count"],
            "IR-EVT-003": lambda metrics: metrics["events"]["scroll_event_count"],
            "IR-EVT-004": lambda metrics: metrics["events"]["input_event_count"],
            "IR-FG-001": lambda metrics: metrics["foreground_seconds"],
        }
        if metric_id not in extractors:
            raise ValueError("unsupported personal reference metric")
        gate = max(0.0, min(float(minimum_coverage_ratio), 1.0))
        target = datetime.fromisoformat(date or local_now().strftime("%Y-%m-%d"))
        stratum = "weekday" if target.weekday() < 5 else "weekend"
        valid_days = []
        for offset in range(28, 0, -1):
            candidate = target - timedelta(days=offset)
            candidate_stratum = "weekday" if candidate.weekday() < 5 else "weekend"
            if candidate_stratum != stratum:
                continue
            local_date = candidate.strftime("%Y-%m-%d")
            metrics = self.day_metrics(local_date)
            ratio = metrics["coverage"].get("ratio")
            if ratio is None or ratio < gate:
                continue
            valid_days.append({
                "date": local_date,
                "value": extractors[metric_id](metrics),
                "coverage_ratio": ratio,
            })

        current = self.day_metrics(target.strftime("%Y-%m-%d"))
        current_ratio = current["coverage"].get("ratio")
        current_value = (
            extractors[metric_id](current)
            if current_ratio is not None and current_ratio >= gate
            else None
        )
        result = personal_reference_summary(
            [row["value"] for row in valid_days],
            current_value=current_value,
        )
        result.update({
            "source_metric_id": metric_id,
            "window_days": 28,
            "stratum": stratum,
            "reference_end_exclusive": target.strftime("%Y-%m-%d"),
            "valid_days": valid_days,
            "current": {
                "date": target.strftime("%Y-%m-%d"),
                "value": current_value,
                "coverage_ratio": current_ratio,
            },
            "coverage_gate": {
                "minimum_ratio": gate,
                "status": "provisional",
                "kind": "visible_product_parameter",
            },
        })
        return result

    def today_stats(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.day_stats(today)

    def day_stats(self, date: str) -> dict:
        date = date or datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().isoformat(timespec="seconds")
        total_keys = self._conn.execute(
            "SELECT COALESCE(SUM(count),0) FROM keystrokes WHERE substr(ts,1,10)=?",
            (date,),
        ).fetchone()[0]
        total_mouse = self._conn.execute(
            "SELECT COALESCE(SUM(count),0) FROM mouse_events WHERE substr(ts,1,10)=?",
            (date,),
        ).fetchone()[0]

        rows = self._conn.execute(
            "SELECT au.app, "
            "  SUM((julianday(MIN(COALESCE(end, ?), ? || 'T23:59:59')) "
            "     - julianday(MAX(start, ?))) * 86400) AS dur_s, "
            "  MAX(title) AS title, "
            "  COALESCE(NULLIF(MAX(au.path), ''), MAX(ap.path), '') AS path "
            "FROM app_usage au LEFT JOIN app_paths ap ON ap.app=au.app "
            "WHERE start < ? || 'T23:59:59' AND COALESCE(end, ?) >= ? "
            "GROUP BY au.app ORDER BY dur_s DESC",
            (now, date, date, date, now, date),
        ).fetchall()

        apps = []
        total_time = 0.0
        for app, seconds, title, path in rows:
            seconds = seconds if seconds and seconds > 0 else 0
            total_time += seconds
            apps.append({
                "app": app,
                "seconds": round(seconds),
                "title": title or "",
                "path": path or "",
            })

        hourly = self.hourly_chart(date)
        responses = int(total_keys or 0) + int(total_mouse or 0)
        peak_hour = max(range(24), key=lambda h: hourly[h]) if responses else None
        peak_value = hourly[peak_hour] if peak_hour is not None else 0
        v2 = self.day_metrics(date)
        foreground_source = "v2_foreground_intervals"
        displayed_foreground_seconds = (
            v2["foreground_seconds"]
            if v2.get("foreground_available")
            else round(total_time)
        )
        if not v2.get("foreground_available"):
            foreground_source = "legacy_app_usage_fallback"

        return {
            "date": date,
            "is_today": date == datetime.now().strftime("%Y-%m-%d"),
            "keystrokes": int(total_keys or 0),
            "mouse_events": int(total_mouse or 0),
            "responses": responses,
            "total_seconds": displayed_foreground_seconds,
            "foreground_seconds": displayed_foreground_seconds,
            "foreground_source": foreground_source,
            "mouse_button_events": v2["events"]["mouse_button_count"],
            "scroll_events": v2["events"]["scroll_event_count"],
            "mouse_split_available": v2["events"]["split_available"],
            "input_events": v2["events"]["input_event_count"],
            "coverage": v2["coverage"],
            "metric_version": v2["metric_version"],
            "peak_hour": peak_hour,
            "peak_value": peak_value,
            "apps": apps[:20],
        }

    def _hourly_table(self, table: str, date: str) -> list[int]:
        rows = self._conn.execute(
            f"SELECT substr(ts,12,2) as h, SUM(count) "
            f"FROM {table} WHERE substr(ts,1,10)=? "
            "GROUP BY h ORDER BY h",
            (date,),
        ).fetchall()
        data = [0] * 24
        for h, c in rows:
            data[int(h)] = int(c or 0)
        return data

    def hourly_chart(self, date: str) -> list[int]:
        k = self._hourly_table("keystrokes", date)
        m = self._hourly_table("mouse_events", date)
        return [k[i] + m[i] for i in range(24)]

    def app_chart(self, date: str) -> list:
        now = datetime.now().isoformat(timespec="seconds")
        labels = self.app_label_map()
        rows = self._conn.execute(
            "SELECT au.app, "
            "  SUM((julianday(MIN(COALESCE(end, ?), ? || 'T23:59:59')) "
            "     - julianday(MAX(start, ?))) * 86400) AS dur_s, "
            "  COALESCE(NULLIF(MAX(au.path), ''), MAX(ap.path), '') AS path "
            "FROM app_usage au LEFT JOIN app_paths ap ON ap.app=au.app "
            "WHERE start < ? || 'T23:59:59' AND COALESCE(end, ?) >= ? "
            "GROUP BY au.app ORDER BY dur_s DESC LIMIT 10",
            (now, date, date, date, now, date),
        ).fetchall()
        interactions = self._conn.execute(
            "SELECT ai.app, SUM(ai.keyboard), SUM(ai.mouse), "
            "  COALESCE(NULLIF(MAX(ai.path), ''), MAX(ap.path), '') AS path "
            "FROM app_interactions ai LEFT JOIN app_paths ap ON ap.app=ai.app "
            "WHERE substr(ai.ts,1,10)=? "
            "GROUP BY ai.app",
            (date,),
        ).fetchall()
        interaction_map = {}
        for app, keyboard, mouse, path in interactions:
            keyboard = int(keyboard or 0)
            mouse = int(mouse or 0)
            interaction_map[app] = {
                "keyboard": keyboard,
                "mouse": mouse,
                "responses": keyboard + mouse,
                "path": path or "",
            }

        apps = {}
        for app, seconds, path in rows:
            apps[app] = {
                "app": app,
                "seconds": round(max(seconds or 0, 0)),
                "path": path or "",
            }
        for app, interaction in interaction_map.items():
            apps.setdefault(app, {"app": app, "seconds": 0, "path": interaction.get("path", "")})
            if interaction.get("path") and not apps[app].get("path"):
                apps[app]["path"] = interaction["path"]

        total_seconds = sum(row.get("seconds", 0) for row in apps.values())
        total_responses = sum(row.get("responses", 0) for row in interaction_map.values())
        result = []
        for app, row in apps.items():
            interaction = interaction_map.get(app, {})
            keyboard = int(interaction.get("keyboard") or 0)
            mouse = int(interaction.get("mouse") or 0)
            responses = keyboard + mouse
            seconds = int(row.get("seconds") or 0)
            result.append({
                "app": app,
                "label": labels.get(app, ""),
                "seconds": seconds,
                "path": row.get("path") or "",
                "keyboard": keyboard,
                "mouse": mouse,
                "responses": responses,
                "duration_share": round(seconds / total_seconds, 4) if total_seconds else 0,
                "response_share": round(responses / total_responses, 4) if total_responses else 0,
                "response_density": round(responses / max(1, seconds / 60), 2) if seconds else 0,
            })
        result.sort(key=lambda item: (item["responses"], item["seconds"]), reverse=True)
        return result[:10]

    def hour_detail(self, date: str, hour: int) -> dict:
        date = date or datetime.now().strftime("%Y-%m-%d")
        hour = max(0, min(23, int(hour or 0)))
        prefix = f"{date}T{hour:02d}:"
        keyboard = self._conn.execute(
            "SELECT COALESCE(SUM(count),0) FROM keystrokes WHERE ts LIKE ?",
            (prefix + "%",),
        ).fetchone()[0]
        mouse = self._conn.execute(
            "SELECT COALESCE(SUM(count),0) FROM mouse_events WHERE ts LIKE ?",
            (prefix + "%",),
        ).fetchone()[0]
        rows = self._conn.execute(
            "SELECT ai.app, SUM(ai.keyboard), SUM(ai.mouse), "
            "  COALESCE(NULLIF(MAX(ai.path), ''), MAX(ap.path), '') AS path "
            "FROM app_interactions ai LEFT JOIN app_paths ap ON ap.app=ai.app "
            "WHERE ai.ts LIKE ? "
            "GROUP BY ai.app",
            (prefix + "%",),
        ).fetchall()
        apps = []
        total = 0
        for app, keys, clicks, path in rows:
            keys = int(keys or 0)
            clicks = int(clicks or 0)
            responses = keys + clicks
            total += responses
            apps.append({
                "app": app,
                "path": path or "",
                "keyboard": keys,
                "mouse": clicks,
                "responses": responses,
            })
        apps.sort(key=lambda item: item["responses"], reverse=True)
        for app in apps:
            app["share"] = round(app["responses"] / total, 4) if total else 0
        hour_start = datetime.fromisoformat(
            f"{date}T{hour:02d}:00:00"
        ).replace(tzinfo=local_now().tzinfo)
        hour_end = hour_start + timedelta(hours=1)
        start_iso = hour_start.isoformat(timespec="seconds")
        end_iso = hour_end.isoformat(timespec="seconds")
        foreground_seconds = self._clipped_seconds(
            "foreground_intervals",
            "started_at",
            "ended_at",
            start_iso,
            end_iso,
            "health_state='running'",
        )
        expected_seconds = self._clipped_seconds(
            "observation_segments",
            "started_at",
            "ended_at",
            start_iso,
            end_iso,
            "component='collector' AND state IN ('running','degraded','unknown')",
        )
        healthy_seconds = self._clipped_seconds(
            "observation_segments",
            "started_at",
            "ended_at",
            start_iso,
            end_iso,
            "component='collector' AND state='running'",
        )
        return {
            "date": date,
            "hour": hour,
            "keyboard": int(keyboard or 0),
            "mouse": int(mouse or 0),
            "responses": int(keyboard or 0) + int(mouse or 0),
            "foreground_seconds": round(foreground_seconds),
            "coverage": coverage_result(expected_seconds, healthy_seconds, 0),
            "apps": apps[:8],
        }

    def daily_summary(self, date: str) -> dict:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(count),0) FROM keystrokes WHERE substr(ts,1,10)=?",
            (date,),
        ).fetchone()
        return {"date": date, "keystrokes": row[0]}

    def app_detail(self, app: str, date: str) -> dict:
        rows = self._conn.execute(
            "SELECT start, end, title FROM app_usage "
            "WHERE app=? AND substr(start,1,10)=? ORDER BY start",
            (app, date),
        ).fetchall()
        sessions = []
        for s, e, t in rows:
            start_dt = datetime.fromisoformat(s)
            end_dt = datetime.fromisoformat(e) if e else datetime.now()
            dur = (end_dt - start_dt).total_seconds()
            sessions.append({
                "start": s[11:19],
                "end": (e or datetime.now().isoformat())[11:19],
                "duration": round(dur),
                "title": t or "",
            })
        total = sum(s["duration"] for s in sessions)
        labels = self.app_label_map()
        return {
            "app": app,
            "label": labels.get(app, ""),
            "date": date,
            "sessions": sessions,
            "total_seconds": total,
        }

    def _dt(self, value: str | None, fallback: datetime) -> datetime:
        if not value:
            return fallback
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                return parsed.replace(tzinfo=None)
            return parsed
        except ValueError:
            return fallback

    def _range(self, days: int) -> tuple[datetime, datetime, list[str]]:
        days = max(1, min(int(days or 1), 365))
        now = datetime.now()
        start_day = (now - timedelta(days=days - 1)).date()
        start = datetime.combine(start_day, time.min)
        dates = [(start_day + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
        return start, now, dates

    def app_analysis(self, app: str, days: int = 7) -> dict:
        app = (app or "").strip()
        start, end, dates = self._range(days)
        now_iso = end.isoformat(timespec="seconds")
        start_iso = start.isoformat(timespec="seconds")
        end_iso = end.isoformat(timespec="seconds")
        path_row = self._conn.execute("SELECT path FROM app_paths WHERE app=?", (app,)).fetchone()
        known_path = path_row[0] if path_row else ""

        rows = self._conn.execute(
            "SELECT start, COALESCE(end, ?), title, path "
            "FROM app_usage "
            "WHERE app=? AND start < ? AND COALESCE(end, ?) > ? "
            "ORDER BY start",
            (now_iso, app, end_iso, now_iso, start_iso),
        ).fetchall()

        daily = {d: 0.0 for d in dates}
        heatmap = {d: [0.0] * 24 for d in dates}
        hourly = [0.0] * 24
        sessions = []
        titles: dict[str, float] = {}
        path = known_path

        for raw_start, raw_end, title, row_path in rows:
            row_start = self._dt(raw_start, start)
            row_end = self._dt(raw_end, end)
            clipped_start = max(row_start, start)
            clipped_end = min(row_end, end)
            if clipped_end <= clipped_start:
                continue
            if row_path:
                path = row_path

            duration = (clipped_end - clipped_start).total_seconds()
            sessions.append({
                "date": clipped_start.strftime("%Y-%m-%d"),
                "start": clipped_start.isoformat(timespec="seconds"),
                "end": clipped_end.isoformat(timespec="seconds"),
                "duration": round(duration),
                "title": title or "",
                "path": row_path or known_path or "",
            })
            if title:
                titles[title] = titles.get(title, 0.0) + duration

            cursor = clipped_start
            while cursor < clipped_end:
                next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                segment_end = min(next_hour, clipped_end)
                seconds = (segment_end - cursor).total_seconds()
                d = cursor.strftime("%Y-%m-%d")
                h = cursor.hour
                if d in daily:
                    daily[d] += seconds
                    heatmap[d][h] += seconds
                    hourly[h] += seconds
                cursor = segment_end

        total_seconds = round(sum(daily.values()))
        session_count = len(sessions)
        longest = max((s["duration"] for s in sessions), default=0)
        peak_hour = max(range(24), key=lambda h: hourly[h]) if total_seconds else None
        top_titles = [
            {"title": title, "seconds": round(seconds)}
            for title, seconds in sorted(titles.items(), key=lambda item: item[1], reverse=True)[:5]
        ]
        interaction_rows = self._conn.execute(
            "SELECT substr(ts,1,10) AS d, CAST(substr(ts,12,2) AS INTEGER) AS h, "
            "  SUM(keyboard), SUM(mouse) "
            "FROM app_interactions "
            "WHERE app=? AND ts>=? AND ts<=? "
            "GROUP BY d, h ORDER BY d, h",
            (app, start_iso, end_iso),
        ).fetchall()
        interaction_daily = {d: {"keyboard": 0, "mouse": 0} for d in dates}
        interaction_heatmap = {d: [{"keyboard": 0, "mouse": 0} for _ in range(24)] for d in dates}
        interaction_hourly = [{"keyboard": 0, "mouse": 0} for _ in range(24)]
        keyboard_total = 0
        mouse_total = 0
        for d, h, keys, clicks in interaction_rows:
            if d not in interaction_daily or h is None or h < 0 or h > 23:
                continue
            keys = int(keys or 0)
            clicks = int(clicks or 0)
            keyboard_total += keys
            mouse_total += clicks
            interaction_daily[d]["keyboard"] += keys
            interaction_daily[d]["mouse"] += clicks
            interaction_heatmap[d][h]["keyboard"] += keys
            interaction_heatmap[d][h]["mouse"] += clicks
            interaction_hourly[h]["keyboard"] += keys
            interaction_hourly[h]["mouse"] += clicks
        response_total = keyboard_total + mouse_total

        return {
            "app": app,
            "label": self.app_label_map().get(app, ""),
            "path": path or "",
            "days": len(dates),
            "date_start": dates[0] if dates else "",
            "date_end": dates[-1] if dates else "",
            "total_seconds": total_seconds,
            "today_seconds": round(daily.get(datetime.now().strftime("%Y-%m-%d"), 0)),
            "session_count": session_count,
            "average_session_seconds": round(total_seconds / session_count) if session_count else 0,
            "longest_session_seconds": longest,
            "active_hours": sum(1 for value in hourly if value > 0),
            "peak_hour": peak_hour,
            "keyboard": keyboard_total,
            "mouse": mouse_total,
            "responses": response_total,
            "response_density": round(response_total / max(1, total_seconds / 60), 2) if total_seconds else 0,
            "interaction_source": "direct" if response_total else "duration_only",
            "first_seen": sessions[0]["start"] if sessions else "",
            "last_seen": sessions[-1]["end"] if sessions else "",
            "daily": [{"date": d, "seconds": round(daily[d])} for d in dates],
            "hourly": [round(v) for v in hourly],
            "heatmap": [{"date": d, "hourly": [round(v) for v in heatmap[d]]} for d in dates],
            "interaction_daily": [
                {
                    "date": d,
                    "keyboard": interaction_daily[d]["keyboard"],
                    "mouse": interaction_daily[d]["mouse"],
                    "responses": interaction_daily[d]["keyboard"] + interaction_daily[d]["mouse"],
                }
                for d in dates
            ],
            "interaction_hourly": [
                {
                    "hour": h,
                    "keyboard": row["keyboard"],
                    "mouse": row["mouse"],
                    "responses": row["keyboard"] + row["mouse"],
                }
                for h, row in enumerate(interaction_hourly)
            ],
            "interaction_heatmap": [
                {
                    "date": d,
                    "hourly": [
                        {
                            "keyboard": cell["keyboard"],
                            "mouse": cell["mouse"],
                            "responses": cell["keyboard"] + cell["mouse"],
                        }
                        for cell in interaction_heatmap[d]
                    ],
                }
                for d in dates
            ],
            "sessions": list(reversed(sessions[-30:])),
            "top_titles": top_titles,
        }

    def export_data(self, days: int = 30) -> dict:
        start, end, dates = self._range(days)
        now_iso = end.isoformat(timespec="seconds")
        start_iso = start.isoformat(timespec="seconds")
        end_iso = end.isoformat(timespec="seconds")
        heatmap = self.heatmap_data(len(dates), False)
        daily = []
        for row in heatmap:
            keyboard = sum(row.get("keyboard_hourly", []))
            mouse = sum(row.get("mouse_hourly", []))
            daily.append({
                "date": row["date"],
                "keyboard": keyboard,
                "mouse": mouse,
                "responses": keyboard + mouse,
            })

        app_rows = self._conn.execute(
            "SELECT au.app, "
            "  SUM((julianday(MIN(COALESCE(end, ?), ?)) - julianday(MAX(start, ?))) * 86400) AS dur_s, "
            "  COALESCE(NULLIF(MAX(au.path), ''), MAX(ap.path), '') AS path "
            "FROM app_usage au LEFT JOIN app_paths ap ON ap.app=au.app "
            "WHERE start < ? AND COALESCE(end, ?) > ? "
            "GROUP BY au.app ORDER BY dur_s DESC",
            (now_iso, end_iso, start_iso, end_iso, now_iso, start_iso),
        ).fetchall()
        applications = [
            {"app": app, "seconds": round(max(seconds or 0, 0)), "path": path or ""}
            for app, seconds, path in app_rows
        ]

        session_rows = self._conn.execute(
            "SELECT app, title, path, start, COALESCE(end, ?) "
            "FROM app_usage "
            "WHERE start < ? AND COALESCE(end, ?) > ? "
            "ORDER BY start DESC",
            (now_iso, end_iso, now_iso, start_iso),
        ).fetchall()
        sessions = []
        for app, title, path, raw_start, raw_end in session_rows:
            row_start = self._dt(raw_start, start)
            row_end = self._dt(raw_end, end)
            clipped_start = max(row_start, start)
            clipped_end = min(row_end, end)
            if clipped_end <= clipped_start:
                continue
            sessions.append({
                "app": app,
                "title": title or "",
                "path": path or "",
                "start": clipped_start.isoformat(timespec="seconds"),
                "end": clipped_end.isoformat(timespec="seconds"),
                "seconds": round((clipped_end - clipped_start).total_seconds()),
            })

        interaction_rows = self._conn.execute(
            "SELECT app, path, ts, keyboard, mouse "
            "FROM app_interactions "
            "WHERE ts>=? AND ts<=? "
            "ORDER BY ts DESC",
            (start_iso, end_iso),
        ).fetchall()
        interactions = [
            {
                "app": app,
                "path": path or "",
                "ts": ts,
                "keyboard": int(keyboard or 0),
                "mouse": int(mouse or 0),
                "responses": int(keyboard or 0) + int(mouse or 0),
            }
            for app, path, ts, keyboard, mouse in interaction_rows
        ]

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "range": {"start": dates[0], "end": dates[-1], "days": len(dates)},
            "today": self.today_stats(),
            "daily": daily,
            "heatmap": heatmap,
            "applications": applications,
            "sessions": sessions,
            "interactions": interactions,
        }

    def _previous_insight(self) -> dict | None:
        row = self._conn.execute(
            "SELECT generated_at, responses, active_seconds, top_app, peak_hour, summary "
            "FROM insight_history ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        generated_at, responses, active_seconds, top_app, peak_hour, summary = row
        return {
            "generated_at": generated_at,
            "responses": responses,
            "active_seconds": active_seconds,
            "top_app": top_app,
            "peak_hour": peak_hour,
            "summary": summary,
        }

    def _save_insight(self, result: dict):
        summary = "；".join((result.get("findings") or result.get("judgments") or [])[:2])
        top_app = ((result.get("summary") or {}).get("top_app") or {}).get("app", "")
        self._conn.execute(
            "INSERT INTO insight_history(generated_at,responses,active_seconds,top_app,peak_hour,summary) "
            "VALUES(?,?,?,?,?,?)",
            (
                result.get("generated_at", datetime.now().isoformat(timespec="seconds")),
                int((result.get("summary") or {}).get("responses") or 0),
                int((result.get("summary") or {}).get("average_daily_seconds") or 0),
                top_app or "",
                (result.get("summary") or {}).get("peak_hour"),
                summary,
            ),
        )
        self._conn.execute(
            "DELETE FROM insight_history WHERE id NOT IN "
            "(SELECT id FROM insight_history ORDER BY generated_at DESC LIMIT 2)"
        )
        self._conn.commit()

    def _comparison_text(self, previous: dict | None, responses: int, avg_seconds: int, top_app: dict, peak_hour):
        if not previous:
            return "这是第一次生成分析，还没有上次结果可以比较。"
        prev_responses = int(previous.get("responses") or 0)
        prev_seconds = int(previous.get("active_seconds") or 0)
        resp_delta = responses - prev_responses
        sec_delta = avg_seconds - prev_seconds
        parts = []
        if abs(resp_delta) < max(50, prev_responses * 0.05):
            parts.append("手感响应变化不大")
        elif resp_delta > 0:
            parts.append(f"手感响应多了约 {resp_delta} 次")
        else:
            parts.append(f"手感响应少了约 {abs(resp_delta)} 次")
        if abs(sec_delta) < 10 * 60:
            parts.append("日均前台时长基本持平")
        elif sec_delta > 0:
            parts.append(f"日均前台时长增加约 {round(sec_delta / 60)} 分钟")
        else:
            parts.append(f"日均前台时长减少约 {round(abs(sec_delta) / 60)} 分钟")
        prev_app = previous.get("top_app") or ""
        current_app = top_app.get("app") if top_app else ""
        if prev_app and current_app and prev_app != current_app:
            parts.append(f"主场应用从 {self._app_display_name(prev_app)} 变成 {self._app_display_name(current_app)}")
        elif current_app:
            parts.append(f"主场应用仍然是 {self._app_display_name(current_app)}")
        return "跟上次相比，" + "，".join(parts) + "。"

    def _app_display_name(self, app: str) -> str:
        name = (app or "").strip()
        stem = name[:-4] if name.lower().endswith(".exe") else name
        lower = stem.lower()
        if lower in {"interactionrhythm", "typetracker"} or name in {"交互节律", APP_NAME}:
            return APP_NAME
        aliases = {
            "code": "VS Code",
            "codex": "Codex",
            "chrome": "Chrome",
            "msedge": "Edge",
            "explorer": "文件管理器",
            "wechat": "微信",
            "obsidian": "Obsidian",
            "python": "Python",
            "pythonw": "Python",
        }
        return aliases.get(lower, stem or name)

    def _hour_range_text(self, hour: int) -> str:
        return f"{hour:02d}:00-{(hour + 1) % 24:02d}:00"

    def _duration_text(self, seconds: int) -> str:
        seconds = max(0, int(seconds or 0))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours and minutes:
            return f"{hours} 小时 {minutes} 分钟"
        if hours:
            return f"{hours} 小时"
        if minutes:
            return f"{minutes} 分钟"
        return f"{seconds} 秒"

    def media_snapshot(self, date: str | None = None) -> dict:
        try:
            date = datetime.fromisoformat(date or "").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            date = datetime.now().strftime("%Y-%m-%d")

        replay = self.day_replay(date)
        summary = replay.get("summary") or {}
        keyboard = int(summary.get("keyboard") or 0)
        mouse = int(summary.get("mouse") or 0)
        responses = int(summary.get("responses") or keyboard + mouse)
        active_seconds = int(summary.get("active_seconds") or 0)
        peak_hour = summary.get("peak_hour")
        apps = replay.get("app_strength") or []
        top_apps = []
        for app in apps[:5]:
            responses_for_app = int(app.get("responses") or 0)
            seconds_for_app = int(app.get("seconds") or 0)
            top_apps.append({
                "name": self._app_display_name(app.get("app", "")),
                "responses": responses_for_app,
                "seconds": seconds_for_app,
                "density": app.get("response_density") or 0,
            })

        if keyboard > mouse * 1.5:
            rhythm_type = "键盘偏强"
        elif mouse > keyboard * 1.5:
            rhythm_type = "鼠标偏强"
        elif responses:
            rhythm_type = "混合手感"
        else:
            rhythm_type = "安静日"

        lines = [
            f"# {APP_NAME}数字手感快照｜{date}",
            "",
            f"- 手感类型：{rhythm_type}",
            f"- 手感响应：{responses:,} 次（键盘 {keyboard:,} / 鼠标 {mouse:,}）",
            f"- 前台时长：{self._duration_text(active_seconds)}",
        ]
        if peak_hour is not None:
            lines.append(f"- 峰值时段：{self._hour_range_text(int(peak_hour))}")
        else:
            lines.append("- 峰值时段：暂无明确峰值")
        if top_apps:
            app_text = "、".join(
                f"{app['name']} {app['responses']:,} 次" if app["responses"] else app["name"]
                for app in top_apps[:3]
                if app["name"]
            )
            if app_text:
                lines.append(f"- 主场应用：{app_text}")
        lines.extend([
            "",
            "一句话回放：",
            replay.get("conclusion") or "这一天还没有留下清晰手感。",
            "",
            "可讲线索：",
        ])
        for finding in (replay.get("findings") or [])[:3]:
            lines.append(f"- {finding}")
        if not replay.get("findings"):
            lines.append("- 暂时没有足够聚合记录生成稳定线索。")
        lines.extend([
            "",
            f"隐私边界：{APP_NAME}不记录输入内容、具体按键、鼠标坐标、截图或窗口正文；这份快照只来自本机聚合统计。",
        ])

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scope": "media_snapshot",
            "date": date,
            "summary": {
                "rhythm_type": rhythm_type,
                "keyboard": keyboard,
                "mouse": mouse,
                "responses": responses,
                "active_seconds": active_seconds,
                "foreground_seconds": active_seconds,
                "peak_hour": peak_hour,
                "top_apps": top_apps,
            },
            "headline": f"{date} 的数字手感：{rhythm_type}，{responses:,} 次响应",
            "draft": "\n".join(lines),
            "privacy_note": "只使用本机聚合统计，不包含输入内容、具体按键、鼠标坐标、截图或窗口正文。",
            "source": "local_aggregate",
        }

    def _apps_during_hour(self, sessions: list[dict], date: str, hour: int, limit: int = 2) -> list[str]:
        start = datetime.fromisoformat(f"{date}T{hour:02d}:00:00")
        end = start + timedelta(hours=1)
        app_seconds: dict[str, float] = {}
        for session in sessions:
            try:
                raw_start = datetime.fromisoformat(session["start"])
                raw_end = datetime.fromisoformat(session["end"])
            except (KeyError, ValueError, TypeError):
                continue
            overlap = (min(end, raw_end) - max(start, raw_start)).total_seconds()
            if overlap <= 0:
                continue
            app = self._app_display_name(session.get("app") or "")
            if app:
                app_seconds[app] = app_seconds.get(app, 0) + overlap
        return [
            app for app, _seconds in sorted(app_seconds.items(), key=lambda item: item[1], reverse=True)[:limit]
        ]

    def _recall_lines(self, heatmap: list[dict], sessions: list[dict], limit: int = 3) -> list[str]:
        slots = []
        for day in heatmap:
            date = day.get("date", "")
            keyboard = day.get("keyboard_hourly", [])
            mouse = day.get("mouse_hourly", [])
            for hour in range(24):
                k = int(keyboard[hour]) if hour < len(keyboard) else 0
                m = int(mouse[hour]) if hour < len(mouse) else 0
                total = k + m
                if total > 0:
                    slots.append((total, date, hour, k, m))
        slots.sort(reverse=True)
        lines = []
        for total, date, hour, keyboard, mouse in slots[:limit]:
            apps = self._apps_during_hour(sessions, date, hour)
            app_text = "，主要应用：" + "、".join(apps) if apps else ""
            if keyboard > mouse * 1.5:
                action = "更像连续键盘响应。"
            elif mouse > keyboard * 1.5:
                action = "更像浏览、筛选或整理。"
            else:
                action = "更像边输入边浏览。"
            lines.append(
                f"{date} {self._hour_range_text(hour)} 手感集中{app_text}，{action}"
            )
        return lines

    def _fresh_suggestions(self, base: list[str], limit: int = 3) -> list[str]:
        pool = list(dict.fromkeys(base))
        random.shuffle(pool)
        return pool[:limit]

    def _ranked_suggestions(self, base: list[str], limit: int = 3) -> list[str]:
        result = []
        for text in base:
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    def _interaction_totals(self, start_iso: str, end_iso: str) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT ai.app, SUM(ai.keyboard), SUM(ai.mouse), "
            "  COALESCE(NULLIF(MAX(ai.path), ''), MAX(ap.path), '') AS path "
            "FROM app_interactions ai LEFT JOIN app_paths ap ON ap.app=ai.app "
            "WHERE ai.ts>=? AND ai.ts<=? "
            "GROUP BY ai.app",
            (start_iso, end_iso),
        ).fetchall()
        totals = {}
        for app, keyboard, mouse, path in rows:
            keyboard = int(keyboard or 0)
            mouse = int(mouse or 0)
            totals[app] = {
                "app": app,
                "path": path or "",
                "keyboard": keyboard,
                "mouse": mouse,
                "responses": keyboard + mouse,
            }
        return totals

    def _app_strength_findings(
        self,
        apps: list[dict],
        sessions: list[dict],
        interactions: dict[str, dict],
        total_seconds: int,
    ) -> tuple[list[str], list[str], list[dict]]:
        session_counts = Counter(session.get("app") or "" for session in sessions)
        total_responses = sum(row.get("responses", 0) for row in interactions.values())
        rows = []
        known_apps = {app.get("app") for app in apps if app.get("app")}
        ordered_names = [app.get("app") for app in apps if app.get("app")]
        for app in sorted(set(interactions) - known_apps):
            ordered_names.append(app)

        for name in ordered_names:
            display_name = self._app_display_name(name)
            if display_name == APP_NAME or display_name.lower() in {"unknown", "未知", ""}:
                continue
            duration = next((int(app.get("seconds") or 0) for app in apps if app.get("app") == name), 0)
            row = interactions.get(name, {})
            keyboard = int(row.get("keyboard") or 0)
            mouse = int(row.get("mouse") or 0)
            responses = keyboard + mouse
            minutes = max(1, duration / 60) if duration else 1
            duration_share = duration / total_seconds if total_seconds else 0
            response_share = responses / total_responses if total_responses else 0
            density = responses / minutes if duration else 0
            rows.append({
                "app": name,
                "name": display_name,
                "seconds": duration,
                "sessions": int(session_counts.get(name, 0)),
                "keyboard": keyboard,
                "mouse": mouse,
                "responses": responses,
                "duration_share": round(duration_share, 4),
                "response_share": round(response_share, 4),
                "response_density": round(density, 2),
            })

        candidates = []
        advice_candidates = []
        for item in rows:
            name = item["name"]
            seconds = item["seconds"]
            sessions_count = item["sessions"]
            keyboard = item["keyboard"]
            mouse = item["mouse"]
            responses = item["responses"]
            duration_share = item["duration_share"]
            response_share = item["response_share"]
            density = item["response_density"]

            if responses and response_share - duration_share >= 0.12:
                candidates.append((
                    90 + int((response_share - duration_share) * 100),
                    f"{name} 的手感占比高于时长占比，是今天键盘感更强的应用。",
                ))
                advice_candidates.append((
                    90 + int((response_share - duration_share) * 100),
                    "active_task",
                    f"把 {name} 标成今日高强度应用，回看时先问：它是在推进任务、检索资料，还是在做切换中枢。",
                ))
            if seconds >= 20 * 60 and duration_share - response_share >= 0.15:
                candidates.append((
                    82 + int((duration_share - response_share) * 100),
                    f"{name} 停留较长，但键鼠响应不高，更像安静驻留、阅读、观看或等待。",
                ))
                advice_candidates.append((
                    82 + int((duration_share - response_share) * 100),
                    "low_response_stay",
                    f"给 {name} 加一个“安静驻留”标签，它更像阅读、等待或陪伴，不必和高键盘响应应用放在同一种强度里解释。",
                ))
            if sessions_count >= 6 and seconds < 90 * 60 and (responses >= 80 or seconds >= 5 * 60):
                candidates.append((
                    66 + min(12, sessions_count),
                    f"{name} 回返次数偏多，是最近比较明显的碎片回返应用。",
                ))
                advice_candidates.append((
                    66 + min(12, sessions_count),
                    "return_frequency",
                    f"把 {name} 放进“碎片回返”组：它不是主场，但可能解释了今天为什么有很多短切换。",
                ))
            if keyboard >= max(300, mouse * 1.6):
                candidates.append((
                    75 + min(20, keyboard // 300),
                    f"{name} 的键盘响应更突出，更像输入、搜索、整理或开发现场。",
                ))
                advice_candidates.append((
                    75 + min(20, keyboard // 300),
                    "keyboard_break",
                    f"把 {name} 的这段记成“键盘冲刺”，它比单纯时长更能代表今天的数字手感。",
                ))
            if mouse >= max(300, keyboard * 1.6):
                candidates.append((
                    75 + min(20, mouse // 300),
                    f"{name} 的鼠标响应更突出，更像浏览、筛选、调整或文件整理。",
                ))
                advice_candidates.append((
                    75 + min(20, mouse // 300),
                    "mouse_break",
                    f"把 {name} 的这段记成“浏览筛选”，和键盘响应型应用分开看会更有意思。",
                ))
            if responses >= 600 and density >= 80:
                candidates.append((
                    72 + min(20, int(density // 20)),
                    f"{name} 单位时间手感密度偏高，说明这段使用不只是挂着，而是在密集操作。",
                ))
                advice_candidates.append((
                    72 + min(20, int(density // 20)),
                    "high_density",
                    f"把 {name} 记成一次爆发时刻，之后做周报或排位会很有辨识度。",
                ))

        if rows and not candidates:
            lead = max(rows, key=lambda item: (item["responses"], item["seconds"]))
            if lead["responses"]:
                candidates.append((60, f"{lead['name']} 是目前手感最明显的应用，可以继续观察它在不同时间段的变化。"))
            elif lead["seconds"]:
                candidates.append((55, f"{lead['name']} 是目前停留最明显的应用，{APP_NAME} 会继续积累它的键鼠手感。"))

        candidates.sort(key=lambda item: item[0], reverse=True)
        findings = []
        for _score, text in candidates:
            if text not in findings:
                findings.append(text)
            if len(findings) >= 3:
                break

        rows.sort(key=lambda item: (item["responses"], item["seconds"]), reverse=True)
        advice = []
        seen_categories = set()
        for _score, category, text in sorted(advice_candidates, key=lambda item: item[0], reverse=True):
            if category in seen_categories or text in advice:
                continue
            seen_categories.add(category)
            advice.append(text)
            if len(advice) >= 3:
                break
        return findings, advice, rows[:6]

    def day_replay(self, date: str | None = None) -> dict:
        try:
            date = datetime.fromisoformat(date or "").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            date = datetime.now().strftime("%Y-%m-%d")

        stats = self.day_stats(date)
        apps = self.app_chart(date)
        hourly = self.hourly_chart(date)
        keyboard = int(stats.get("keystrokes") or 0)
        mouse = int(stats.get("mouse_events") or 0)
        responses = keyboard + mouse
        total_seconds = int(stats.get("total_seconds") or 0)
        peak_hour = max(range(24), key=lambda h: hourly[h]) if responses else None
        peak_value = hourly[peak_hour] if peak_hour is not None else 0
        top_app = apps[0] if apps else {"app": "", "seconds": 0, "responses": 0}
        top_app_name = self._app_display_name(top_app.get("app", "")) if top_app.get("app") else ""

        if keyboard > mouse * 1.5:
            rhythm_type = "键盘响应型"
        elif mouse > keyboard * 1.5:
            rhythm_type = "鼠标浏览型"
        else:
            rhythm_type = "混合手感型"

        if responses == 0 and total_seconds == 0:
            conclusion = f"这一天还没有响起来。{APP_NAME} 暂时只能确认记录通道还在等信号。"
        elif responses < 80 or total_seconds < 15 * 60:
            conclusion = "这一天只有一点轻响，更适合先看记录是否连续，不急着判断稳定手感。"
        else:
            app_phrase = f"主场是 {top_app_name}" if top_app_name else "主场应用还不明显"
            hour_phrase = f"高峰在 {peak_hour:02d}:00 左右" if peak_hour is not None else "高峰还不明显"
            conclusion = f"{date} 更像{rhythm_type}手感，{app_phrase}，{hour_phrase}。"

        findings = []
        if peak_hour is not None:
            findings.append(
                f"{self._hour_range_text(peak_hour)} 是这一天最深的小时格，共记录到 {peak_value} 次手感响应。"
            )
        if top_app_name and int(top_app.get("responses") or 0) > 0:
            share = int(round((top_app.get("response_share") or 0) * 100))
            density = top_app.get("response_density") or 0
            findings.append(
                f"{top_app_name} 是这一天最响的应用，约占 {share}% 手感响应，密度约 {density} 次/分。"
            )
        elif top_app_name and int(top_app.get("seconds") or 0) > 0:
            findings.append(
                f"{top_app_name} 停留最明显，更像这一天的安静驻留或主场背景。"
            )
        if responses:
            if keyboard > mouse * 1.5:
                findings.append("键盘响应明显多于鼠标响应，这一天更像输入、搜索、整理或开发现场。")
            elif mouse > keyboard * 1.5:
                findings.append("鼠标响应明显多于键盘响应，这一天更像浏览、筛选、调整或文件整理。")
            else:
                findings.append("键盘和鼠标响应比较接近，这一天更像边输入边浏览的混合节奏。")
        if not findings:
            findings = [
                "还没有足够的键盘、鼠标或应用记录来选出稳定线索。",
                "先看这一天有没有连续记录，比解释原因更重要。",
            ]

        suggestions = []
        if peak_hour is not None:
            suggestions.append(
                f"可以点开 {peak_hour:02d}:00 的小时格，看看那一段的应用构成。"
            )
        if top_app_name and int(top_app.get("responses") or 0) > 0:
            suggestions.append(
                f"可以把 {top_app_name} 这段先记成“主场推进”或“密集操作”，以后做周回放会更容易认出来。"
            )
        elif top_app_name:
            suggestions.append(
                f"可以把 {top_app_name} 暂时看作“安静驻留”，先不把它和高键盘响应应用放在一起解释。"
            )
        if responses == 0:
            suggestions.append("如果这一天本来就很安静，就把它当作空白日留着。")
        elif keyboard > mouse * 1.5:
            suggestions.append("也许值得回看键盘最密的一段：那通常比总时长更能说明这一天的手感。")
        elif mouse > keyboard * 1.5:
            suggestions.append("也许值得回看鼠标最密的一段：它可能对应浏览、筛选或整理。")
        else:
            suggestions.append("也许值得先看前两个应用之间有没有来回切换，而不是只看单个主场。")
        suggestions = self._ranked_suggestions(suggestions + [
            "这一天如果数据偏少，可以先确认记录是否连续，等更完整的一天再解释形状。",
            "回放只适合帮你回忆桌面现场，不需要把它当成效率分数。",
        ])

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "assistant": "手感回放",
            "model": "本地规则分析",
            "model_note": "手感回放当前使用本地规则分析，没有调用外部 AI 模型，记录不会离开本机。",
            "interface": "GET /api/day-replay?date=YYYY-MM-DD",
            "scope": "day",
            "date": date,
            "days": 1,
            "prompt_design": [
                "先回看选中日期，再给线索和下一步。",
                "只使用键盘响应、鼠标响应、小时高峰、应用构成和前台应用时长。",
                "不评价人格，不给效率打分，不假装知道用户真实意图。",
            ],
            "conclusion": conclusion,
            "summary": {
                "keyboard": keyboard,
                "mouse": mouse,
                "responses": responses,
                "active_seconds": total_seconds,
                "foreground_seconds": total_seconds,
                "metric_ids": ["IR-EVT-001", "IR-EVT-004", "IR-FG-001"],
                "peak_hour": peak_hour,
                "top_app": top_app,
            },
            "findings": findings[:3],
            "judgments": findings[:3],
            "suggestions": suggestions[:3],
            "provenance": {
                "metric_version": METRIC_VERSION,
                "metric_ids": ["IR-EVT-001", "IR-EVT-004", "IR-FG-001"],
                "claim_id": "Q01",
                "boundary": "descriptive_personal_recall_only",
            },
            "app_strength": apps[:6],
            "charts": {
                "hourly": hourly,
                "apps": apps[:5],
            },
        }

    def insights(self, days: int = 7) -> dict:
        data = self.export_data(days)
        daily = data["daily"]
        apps = data["applications"]
        heatmap = data["heatmap"]
        sessions = data["sessions"]
        keyboard = sum(row["keyboard"] for row in daily)
        mouse = sum(row["mouse"] for row in daily)
        responses = keyboard + mouse
        total_seconds = sum(app["seconds"] for app in apps)
        avg_responses = round(responses / max(1, len(daily)))
        avg_seconds = round(total_seconds / max(1, len(daily)))
        hourly = [0] * 24
        for day in heatmap:
            for i, value in enumerate(day.get("hourly", [])):
                hourly[i] += value
        peak_hour = max(range(24), key=lambda h: hourly[h]) if responses else None
        peak_day = max(daily, key=lambda row: row["responses"]) if daily else {"date": "", "responses": 0}
        top_app = apps[0] if apps else {"app": "", "seconds": 0}
        top_share = (top_app["seconds"] / total_seconds) if total_seconds else 0
        previous = self._previous_insight()
        recall = self._recall_lines(heatmap, sessions)
        start, end, _dates = self._range(days)
        interactions = self._interaction_totals(start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))
        app_findings, app_suggestions, app_strength = self._app_strength_findings(
            apps, sessions, interactions, total_seconds
        )

        if responses == 0 and total_seconds == 0:
            conclusion = f"现在还没有足够的记录。手感回放只能确认 {APP_NAME} 的记录通道已经准备好。"
            findings = [
                "最近还没有记录到键盘、鼠标或应用使用数据。",
                "先正常使用一段时间，再回来查看数字手感线索。",
            ]
            suggestions = self._ranked_suggestions([
                "先正常使用半天，让手感热力图积累出时间块。",
                "如果一直在使用电脑却没有记录，检查后台是否仍在运行。",
                "第一次观察先看记录是否连续，不急着给自己下结论。",
                "等应用强度榜出现稳定名称后，再看具体活动线索。",
                "如果开机自启已打开，明天的数据会更完整。",
            ])
        elif responses < 500 or total_seconds < 20 * 60:
            conclusion = f"当前数据偏少，更适合作为 {APP_NAME} 通道检查，不适合直接判断稳定手感。"
            findings = [
                "当前数据只能看出大概方向，还不能当成稳定的应用画像。",
                "等记录覆盖几个完整时段后，高峰时间和应用主场会更准。",
            ]
            if peak_hour is not None:
                findings.append(f"目前少量记录里，手感响应主要出现在 {peak_hour:02d}:00 左右。")
            for text in app_findings:
                if len(findings) >= 3:
                    break
                if text not in findings:
                    findings.append(text)
            suggestions = self._ranked_suggestions([
                "先积累半天到一天的数据，再看趋势会更稳。",
                "现在可以先确认记录是否连续、应用名称是否正确。",
                "如果某个小时突然变深，可以回忆那一段像不像一次爆发时刻。",
                "数据还少时，先看形状，不急着解释原因。",
                "等有了完整一天，再生成一次更像样的数字手感回放。",
            ])
        else:
            if keyboard > mouse * 1.5:
                rhythm_type = "键盘响应型"
            elif mouse > keyboard * 1.5:
                rhythm_type = "鼠标浏览型"
            else:
                rhythm_type = "混合手感型"
            top_app_name = self._app_display_name(top_app["app"])
            if top_app["app"] and top_share >= 0.5:
                app_phrase = f"主场应用是 {top_app_name}"
            elif top_app["app"] and top_share >= 0.25:
                app_phrase = f"有一个较明显的主场应用：{top_app_name}"
            else:
                app_phrase = "应用分布比较分散"
            hour_phrase = f"高峰多在 {peak_hour:02d}:00 左右" if peak_hour is not None else "高峰时间还不明显"
            conclusion = f"最近 {data['range']['days']} 天呈现{rhythm_type}数字手感，{app_phrase}，{hour_phrase}。"
            context_findings = []
            if avg_seconds >= 8 * 3600:
                context_findings.append("最近每天桌面驻留很长，更像深潜日而不是轻量使用。")
            elif avg_seconds >= 5 * 3600:
                context_findings.append("最近每天桌面活动时间不低，适合看主场应用和爆发时刻。")
            else:
                context_findings.append("最近总时长不算重，重点看有没有集中在某一两个小时里。")

            if peak_hour is not None:
                if peak_hour >= 22 or peak_hour <= 5:
                    context_findings.append(f"高峰在 {peak_hour:02d}:00 左右，今天有一点夜航味道。")
                else:
                    context_findings.append(f"高峰在 {peak_hour:02d}:00 左右，这是今天最明显的键盘时段。")
            findings = []
            for text in app_findings:
                if len(findings) >= 3:
                    break
                if text not in findings:
                    findings.append(text)
            for text in context_findings:
                if len(findings) >= 3:
                    break
                if text not in findings:
                    findings.append(text)

            suggestion_pool = []
            if top_app["app"]:
                suggestion_pool.append(f"可以先看应用强度榜里的 {top_app_name}：它是今天的主场、工具台，还是只是长驻留背景。")
            if peak_hour is not None:
                suggestion_pool.append(f"回看 {peak_hour:02d}:00 左右那一小时：如果热力格连续变深，可以把它标成今天的爆发时刻。")
            if top_share >= 0.45 and top_app["app"]:
                suggestion_pool.append(f"给 {top_app_name} 这段使用起一个名字，例如“主场推进”或“长驻留”，之后做周报会更容易复盘。")
            elif top_share < 0.25:
                suggestion_pool.append("今天应用分布比较散，可以先命名为“碎片风”，重点看哪些应用反复回返。")
            if keyboard > mouse * 1.5:
                suggestion_pool.append("今天更偏键盘响应型，适合从创作、搜索、整理或开发冲刺的角度回看。")
            elif mouse > keyboard * 1.5:
                suggestion_pool.append("今天更偏鼠标浏览型，适合从浏览、筛选、调整或文件整理的角度回看。")
            else:
                suggestion_pool.append("今天是混合手感型，可以先看应用强度榜前两名之间有没有来回切换。")
            suggestion_pool.extend([
                "如果想做分享卡，优先拿“主场应用 + 最深小时 + 一句话标签”这三个元素。",
                "下次打开时可以先看应用强度榜第一名，再回忆它为什么成为今天的主角。",
            ])
            if avg_seconds >= 5 * 3600:
                suggestion_pool.append("如果今天是深潜日，可以在回放里单独保留一个长驻留标签。")
            if peak_hour is not None and (peak_hour >= 22 or peak_hour <= 5):
                suggestion_pool.append("夜间高峰可以先不评价，直接标成“夜航时段”会更准确。")
            if mouse > keyboard * 1.5 and mouse > 1000:
                suggestion_pool.append("鼠标用得多的一天，可以生成“浏览筛选型”应用关系画像。")
            suggestions = self._ranked_suggestions(suggestion_pool + app_suggestions, 3)

        comparison = self._comparison_text(previous, responses, avg_seconds, top_app, peak_hour)
        if previous and comparison and responses >= 500 and total_seconds >= 20 * 60:
            ordered = []
            if findings:
                ordered.append(findings[0])
            ordered.append(comparison)
            ordered.extend(findings[1:])
            findings = list(dict.fromkeys(ordered))[:3]
        result = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "assistant": "手感回放",
            "model": "本地规则分析",
            "model_note": "手感回放当前使用本地规则分析，没有调用外部 AI 模型，记录不会离开本机。",
            "interface": "GET /api/insights?days=7",
            "prompt_design": [
                "先给数字手感画像，再列线索和下一步。",
                "优先具体绑定应用、时间段和可命名标签，少给泛泛提醒。",
                "用键盘响应、鼠标响应、小时高峰、应用主场和前台应用时间段帮助用户回忆也许发生了什么。",
                "不评价人格，不给效率打分，不假装知道用户真实意图。",
                "使用保守表达，保持好玩、克制和可校正。",
            ],
            "days": data["range"]["days"],
            "conclusion": conclusion,
            "summary": {
                "keyboard": keyboard,
                "mouse": mouse,
                "responses": responses,
                "average_daily_responses": avg_responses,
                "average_daily_seconds": avg_seconds,
                "peak_day": peak_day,
                "peak_hour": peak_hour,
                "top_app": top_app,
            },
            "recall": recall,
            "findings": findings[:3],
            "judgments": findings[:3],
            "comparison": comparison,
            "suggestions": suggestions[:3],
            "app_strength": app_strength,
            "charts": {
                "daily": daily,
                "hourly": hourly,
                "apps": apps[:5],
            },
        }
        self._save_insight(result)
        return result

    def date_range(self) -> tuple:
        r1 = self._conn.execute(
            "SELECT MIN(d) FROM ("
            "  SELECT MIN(substr(ts,1,10)) AS d FROM keystrokes "
            "  UNION ALL SELECT MIN(date) FROM daily_totals"
            ")"
        ).fetchone()
        r2 = self._conn.execute(
            "SELECT MAX(d) FROM ("
            "  SELECT MAX(substr(ts,1,10)) AS d FROM keystrokes "
            "  UNION ALL SELECT MAX(date) FROM daily_totals"
            ")"
        ).fetchone()
        return (r1[0], r2[0]) if r1 and r2 else (None, None)

    def daily_keystrokes(self, days: int = 7) -> list:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = self._conn.execute(
            "SELECT d, SUM(c) FROM ("
            "  SELECT substr(ts,1,10) AS d, SUM(count) AS c "
            "  FROM keystrokes WHERE substr(ts,1,10) >= ? GROUP BY d "
            "  UNION ALL "
            "  SELECT date AS d, keyboard AS c FROM daily_totals "
            "  WHERE date >= ? AND NOT EXISTS ("
            "    SELECT 1 FROM keystrokes k WHERE substr(k.ts,1,10)=daily_totals.date"
            "  )"
            ") GROUP BY d ORDER BY d",
            (cutoff, cutoff),
        ).fetchall()
        return [{"date": d, "count": c} for d, c in rows]

    def heatmap_data(self, days: int = 7, tomorrow: bool = False, end_date: str | None = None) -> list:
        actual_days = max(1, days)
        today_dt = datetime.now()
        try:
            end_dt = datetime.fromisoformat(end_date) if end_date else today_dt
        except (TypeError, ValueError):
            end_dt = today_dt
        if not end_date and tomorrow:
            end_dt = today_dt + timedelta(days=1)
        if end_dt.date() > today_dt.date():
            end_dt = today_dt
        cutoff = (end_dt - timedelta(days=actual_days - 1)).strftime("%Y-%m-%d")
        end = end_dt.strftime("%Y-%m-%d")

        def _by_day(table):
            rows = self._conn.execute(
                f"SELECT substr(ts,1,10) as d, CAST(substr(ts,12,2) AS INTEGER) as h, SUM(count) "
                f"FROM {table} WHERE substr(ts,1,10) >= ? AND substr(ts,1,10) <= ? "
                "GROUP BY d, h ORDER BY d, h",
                (cutoff, end),
            ).fetchall()
            out = {}
            for d, h, c in rows:
                out.setdefault(d, [0] * 24)[h] = int(c or 0)
            return out

        keyboard_by_day = _by_day("keystrokes")
        mouse_by_day = _by_day("mouse_events")
        today = datetime.now().strftime("%Y-%m-%d")
        result = []
        for i in range(actual_days):
            d = (end_dt - timedelta(days=actual_days - 1 - i)).strftime("%Y-%m-%d")
            keyboard_hourly = keyboard_by_day.get(d, [0] * 24)
            mouse_hourly = mouse_by_day.get(d, [0] * 24)
            hourly = [keyboard_hourly[h] + mouse_hourly[h] for h in range(24)]
            result.append({
                "date": d,
                "hourly": hourly,
                "keyboard_hourly": keyboard_hourly,
                "mouse_hourly": mouse_hourly,
                "isToday": d == today,
            })
        return result

    def month_heatmap(self, month: str | None = None) -> dict:
        current = datetime.now()
        try:
            base = datetime.strptime((month or current.strftime("%Y-%m")) + "-01", "%Y-%m-%d")
        except (TypeError, ValueError):
            base = current.replace(day=1)
        next_month = (base.replace(day=28) + timedelta(days=4)).replace(day=1)
        start = base.strftime("%Y-%m-%d")
        end = (next_month - timedelta(days=1)).strftime("%Y-%m-%d")

        def _daily(table):
            detail_column = "keyboard" if table == "keystrokes" else "mouse"
            rows = self._conn.execute(
                "SELECT d, SUM(c) FROM ("
                f"  SELECT substr(ts,1,10) AS d, SUM(count) AS c FROM {table} "
                "  WHERE substr(ts,1,10)>=? AND substr(ts,1,10)<=? GROUP BY d "
                "  UNION ALL "
                "  SELECT date AS d, "
                f"{detail_column} AS c FROM daily_totals "
                "  WHERE date>=? AND date<=? AND NOT EXISTS ("
                f"    SELECT 1 FROM {table} t WHERE substr(t.ts,1,10)=daily_totals.date"
                "  )"
                ") GROUP BY d",
                (start, end, start, end),
            ).fetchall()
            return {d: int(c or 0) for d, c in rows}

        keyboard = _daily("keystrokes")
        mouse = _daily("mouse_events")
        days = []
        cursor = base
        while cursor < next_month:
            d = cursor.strftime("%Y-%m-%d")
            keys = keyboard.get(d, 0)
            clicks = mouse.get(d, 0)
            days.append({
                "date": d,
                "keyboard": keys,
                "mouse": clicks,
                "responses": keys + clicks,
            })
            cursor += timedelta(days=1)
        return {"month": base.strftime("%Y-%m"), "days": days}

    def year_heatmap(self, year: str | None = None) -> dict:
        current = datetime.now()
        try:
            year_value = int(year or current.strftime("%Y"))
            if year_value < 2000 or year_value > current.year:
                year_value = current.year
        except (TypeError, ValueError):
            year_value = current.year

        months = []
        for month in range(1, 13):
            months.append(self.month_heatmap(f"{year_value}-{month:02d}"))
        return {"year": str(year_value), "months": months}

    def week_summary(self, end_date: str | None = None, days: int = 7) -> dict:
        """Aggregate one bounded review window for the weekly Agent card.

        Model-independent and bounded by the same window used by
        build_weekly_evidence_pack. Peak hours and top apps are derived from
        per-interaction detail, so they are omitted once that detail retention
        has expired; the daily totals keep the card readable regardless.
        """
        day_count = max(1, min(int(days), 31))
        try:
            end_dt = datetime.fromisoformat(str(end_date or "")).replace(tzinfo=local_now().tzinfo)
        except (TypeError, ValueError):
            end_dt = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=day_count - 1)
        start_iso = start_dt.isoformat(timespec="seconds")
        end_iso = (end_dt + timedelta(days=1)).isoformat(timespec="seconds")

        event_row = self._conn.execute(
            "SELECT "
            "COALESCE(SUM(keyboard_down_count),0), "
            "COALESCE(SUM(mouse_button_count),0), "
            "COALESCE(SUM(scroll_event_count),0), "
            "COALESCE(SUM(keyboard_down_count),0)+COALESCE(SUM(mouse_button_count),0)+"
            "COALESCE(SUM(scroll_event_count),0) "
            "FROM interaction_buckets WHERE started_at>=? AND started_at<?",
            (start_iso, end_iso),
        ).fetchone()
        keyboard, buttons, scroll, total = (int(value or 0) for value in event_row)

        peak_rows = self._conn.execute(
            "SELECT CAST(substr(started_at,12,2) AS INTEGER) AS h, "
            "COALESCE(SUM(keyboard_down_count),0)+COALESCE(SUM(mouse_button_count),0)+"
            "COALESCE(SUM(scroll_event_count),0) AS c "
            "FROM interaction_buckets WHERE started_at>=? AND started_at<? "
            "GROUP BY h ORDER BY c DESC LIMIT 3",
            (start_iso, end_iso),
        ).fetchall()
        peak_hours = [
            {"hour": int(h), "count": int(c)}
            for h, c in peak_rows
            if h is not None and int(c or 0) > 0
        ]

        foreground_row = self._conn.execute(
            "SELECT COALESCE(SUM(MAX(0, "
            "(MIN(julianday(ended_at), julianday(?)) "
            "- MAX(julianday(started_at), julianday(?))) * 86400"
            ")), 0) FROM foreground_intervals "
            "WHERE health_state='running' AND started_at<? AND ended_at>?",
            (end_iso, start_iso, end_iso, start_iso),
        ).fetchone()
        foreground_seconds = float(foreground_row[0] or 0)

        app_rows = self._conn.execute(
            "SELECT au.app, COALESCE(SUM(MAX(0, "
            "(MIN(julianday(COALESCE(au.end, ?)), julianday(?)) "
            "- MAX(julianday(au.start), julianday(?))) * 86400"
            ")), 0) AS secs FROM app_usage au "
            "WHERE au.start<? AND COALESCE(au.end, ?)>? "
            "GROUP BY au.app ORDER BY secs DESC LIMIT 5",
            (end_iso, end_iso, start_iso, end_iso, end_iso, start_iso),
        ).fetchall()
        top_apps = [
            {"name": app, "seconds": int(round(secs))}
            for app, secs in app_rows
            if secs is not None and float(secs) > 0
        ]
        return {
            "start": start_dt.strftime("%Y-%m-%d"),
            "end": end_dt.strftime("%Y-%m-%d"),
            "days": day_count,
            "keyboard_events": keyboard,
            "mouse_events": buttons,
            "scroll_events": scroll,
            "input_events": total,
            "foreground_seconds": int(round(foreground_seconds)),
            "peak_hours": peak_hours,
            "top_apps": top_apps,
        }

    def week_session_stats(self, end_date: str | None = None, days: int = 7) -> dict:
        """Count foreground sessions fully inside the window and their median dwell.

        Only intervals that start and end inside the review window are counted,
        so durations are not distorted by clipping at the window edges. The
        median is reported instead of the mean because dwell times are heavily
        right-skewed (many short foreground slices), per the robust-statistics
        guidance in the derived-indicator research.
        """
        day_count = max(1, min(int(days), 31))
        try:
            end_dt = datetime.fromisoformat(str(end_date or "")).replace(tzinfo=local_now().tzinfo)
        except (TypeError, ValueError):
            end_dt = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=day_count - 1)
        start_iso = start_dt.isoformat(timespec="seconds")
        end_iso = (end_dt + timedelta(days=1)).isoformat(timespec="seconds")
        rows = self._conn.execute(
            "SELECT started_at, ended_at FROM foreground_intervals "
            "WHERE health_state='running' AND started_at >= ? AND ended_at <= ?",
            (start_iso, end_iso),
        ).fetchall()
        durs = []
        for started, ended in rows:
            try:
                t0 = datetime.fromisoformat(started)
                t1 = datetime.fromisoformat(ended)
            except (TypeError, ValueError):
                continue
            seconds = (t1 - t0).total_seconds()
            if seconds > 0:
                durs.append(seconds)
        if not durs:
            return {"session_count": 0, "median_dwell_seconds": None}
        durs.sort()
        middle = len(durs) // 2
        if len(durs) % 2:
            median = durs[middle]
        else:
            median = (durs[middle - 1] + durs[middle]) / 2
        return {
            "session_count": len(durs),
            "median_dwell_seconds": int(round(median)),
        }

    def corpus_profile(self) -> dict:
        """Corpus-size profile used by the weekly report's evidence-tier logic.

        Sources mirror ``trend_summary``: ``interaction_buckets`` are canonical
        while retained, and ``daily_totals`` backfill dates whose raw buckets
        have rolled off the 180-day retention window. The profile answers "how
        much history / how many events" without promising per-day detail beyond
        the retention windows.
        """
        first_last = self._conn.execute(
            "SELECT MIN(d), MAX(d) FROM ("
            "  SELECT MIN(substr(started_at,1,10)) AS d FROM interaction_buckets "
            "  UNION ALL SELECT MIN(date) AS d FROM daily_totals "
            "  UNION ALL SELECT MIN(substr(started_at,1,10)) AS d FROM foreground_intervals"
            ")"
        ).fetchone()
        first_date, last_date = (first_last or (None, None))
        history_days = 0
        if first_date and last_date:
            try:
                first_day = datetime.fromisoformat(str(first_date)).date()
                last_day = datetime.fromisoformat(str(last_date)).date()
                history_days = (last_day - first_day).days + 1
            except (TypeError, ValueError):
                history_days = 0
        total_row = self._conn.execute(
            "SELECT COALESCE(SUM(v), 0) FROM ("
            "  SELECT COALESCE(keyboard_down_count,0) + COALESCE(mouse_button_count,0)"
            "       + COALESCE(scroll_event_count,0) AS v FROM interaction_buckets "
            "  UNION ALL "
            "  SELECT COALESCE(keyboard,0) + COALESCE(mouse,0) AS v FROM daily_totals d "
            "  WHERE NOT EXISTS ("
            "    SELECT 1 FROM interaction_buckets b WHERE substr(b.started_at,1,10) = d.date"
            "  )"
            ")"
        ).fetchone()
        total_input = int(total_row[0] or 0) if total_row else 0
        try:
            db_size = self._path.stat().st_size
        except OSError:
            db_size = 0
        return {
            "first_date": first_date,
            "last_date": last_date,
            "history_days": int(history_days),
            "total_input_events": total_input,
            "db_size_mb": round(db_size / 1024 / 1024, 1),
        }

    def trend_summary(self, weeks: int = 12) -> dict:
        """Weekly input/foreground trend plus the personal daily baseline band.

        The trend horizon is Monday-aligned and bounded to the same review
        window the weekly card uses. Daily totals keep the trend readable
        after raw detail retention expires; scroll events are only present
        while interaction-bucket detail is retained.
        """
        weeks = max(4, min(int(weeks), 26))
        today = local_now().date()
        monday = today - timedelta(days=today.weekday())
        first_monday = monday - timedelta(weeks=weeks - 1)
        start_iso = datetime.combine(first_monday, time.min).isoformat(timespec="seconds")
        end_exclusive = datetime.combine(monday + timedelta(days=7), time.min)
        end_iso = end_exclusive.isoformat(timespec="seconds")
        end_date_exclusive = end_exclusive.strftime("%Y-%m-%d")

        bucket_rows = self._conn.execute(
            "SELECT substr(started_at,1,10) AS d, "
            "COALESCE(SUM(keyboard_down_count),0), "
            "COALESCE(SUM(mouse_button_count),0), "
            "COALESCE(SUM(scroll_event_count),0) "
            "FROM interaction_buckets "
            "WHERE started_at>=? AND started_at<? "
            "GROUP BY d",
            (start_iso, end_iso),
        ).fetchall()
        daily: dict[str, list] = {
            d: [int(k or 0), int(m or 0), int(s or 0)]
            for d, k, m, s in bucket_rows
        }
        fallback_rows = self._conn.execute(
            "SELECT date, keyboard, mouse FROM daily_totals "
            "WHERE date>=? AND date<? "
            "AND NOT EXISTS (SELECT 1 FROM interaction_buckets b "
            "WHERE substr(b.started_at,1,10)=daily_totals.date)",
            (first_monday.strftime("%Y-%m-%d"), end_date_exclusive),
        ).fetchall()
        for d, k, m in fallback_rows:
            daily.setdefault(d, [int(k or 0), int(m or 0), None])

        weeks_out = []
        for offset in range(weeks):
            week_start = first_monday + timedelta(days=7 * offset)
            week_end = week_start + timedelta(days=7)
            ws = datetime.combine(week_start, time.min).isoformat(timespec="seconds")
            we = datetime.combine(week_end, time.min).isoformat(timespec="seconds")
            fg_row = self._conn.execute(
                "SELECT COALESCE(SUM(MAX(0, "
                "(MIN(julianday(ended_at), julianday(?)) "
                "- MAX(julianday(started_at), julianday(?))) * 86400"
                ")), 0) FROM foreground_intervals "
            "WHERE health_state='running' "
            "AND julianday(started_at)<julianday(?) "
            "AND julianday(ended_at)>julianday(?)",
            (we, ws, we, ws),
            ).fetchone()
            keys = mouse = scroll = active_days = 0
            for i in range(7):
                d = (week_start + timedelta(days=i)).strftime("%Y-%m-%d")
                row = daily.get(d)
                if not row:
                    continue
                if row[0] or row[1] or row[2]:
                    active_days += 1
                keys += int(row[0] or 0)
                mouse += int(row[1] or 0)
                scroll += int(row[2] or 0)
            input_events = keys + mouse + scroll
            weeks_out.append({
                "week_start": week_start.strftime("%Y-%m-%d"),
                "week_end": (week_end - timedelta(days=1)).strftime("%Y-%m-%d"),
                "input_events": input_events,
                "foreground_seconds": int(round(float(fg_row[0] or 0))),
                "active_days": active_days,
                "avg_daily_input": round(input_events / 7, 1) if input_events else 0,
            })

        baseline = {}
        for metric_id, key in (
            ("IR-EVT-004", "input"),
            ("IR-FG-001", "foreground_seconds"),
        ):
            try:
                ref = self.personal_reference(metric_id, today.isoformat())
            except ValueError:
                ref = {}
            baseline[key] = {
                "median": ref.get("median"),
                "p25": ref.get("p25"),
                "p75": ref.get("p75"),
                "stage": ref.get("stage", "facts_only"),
                "n_valid_days": ref.get("n_valid_days", 0),
            }
        return {
            "weeks": weeks_out,
            "baseline": baseline,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def return_interval_summary(self, days: int = 7) -> dict:
        """Descriptive return-interval and app-switch statistics.

        A return pair is two foreground intervals of the same process with a
        real gap between them. Pairs are skipped when an unknown/lock/sleep
        segment overlaps the gap (the return is not observable, per
        IR-SES-003). Switch count counts adjacent foreground changes where
        the process differs.
        """
        days = max(1, min(int(days or 7), 90))
        start, end, _ = self._range(days)
        start_iso = start.isoformat(timespec="seconds")
        end_iso = end.isoformat(timespec="seconds")

        rows = self._conn.execute(
            "SELECT process_name, started_at, ended_at FROM foreground_intervals "
            "WHERE health_state='running' AND started_at>=? AND started_at<? "
            "ORDER BY process_name, started_at",
            (start_iso, end_iso),
        ).fetchall()
        gap_rows = self._conn.execute(
            "SELECT started_at, ended_at FROM observation_segments "
            "WHERE component='collector' AND state IN "
            "('unknown','sleeping','locked','paused') "
            "AND started_at<? AND ended_at>?",
            (end_iso, start_iso),
        ).fetchall()
        gap_ranges = []
        for s, e in gap_rows:
            try:
                gap_start = datetime.fromisoformat(s)
                gap_end = datetime.fromisoformat(e)
            except (TypeError, ValueError):
                continue
            if gap_end > gap_start:
                gap_ranges.append((gap_start, gap_end))

        def _overlaps_gap(lo: datetime, hi: datetime) -> bool:
            return any(g_start < hi and g_end > lo for g_start, g_end in gap_ranges)

        by_app: dict[str, list[tuple[datetime, datetime]]] = {}
        intervals: list[tuple[datetime, str]] = []
        for process, started, ended in rows:
            try:
                start_dt = datetime.fromisoformat(started)
                end_dt = datetime.fromisoformat(ended)
            except (TypeError, ValueError):
                continue
            if end_dt <= start_dt:
                continue
            by_app.setdefault(process, []).append((start_dt, end_dt))
            intervals.append((start_dt, process))

        app_stats = []
        for process, seq in by_app.items():
            returns = []
            for prev, cur in zip(seq, seq[1:]):
                gap_start, gap_end = prev[1], cur[0]
                if gap_end <= gap_start:
                    continue
                if _overlaps_gap(gap_start, gap_end):
                    continue
                interval = (gap_end - gap_start).total_seconds()
                if 0 < interval <= 86400 * 2:
                    returns.append(interval)
            if not returns:
                continue
            app_stats.append({
                "name": process,
                "returns": len(returns),
                "median_seconds": round(float(statistics.median(returns))),
            })
        app_stats.sort(key=lambda item: item["returns"], reverse=True)

        intervals.sort(key=lambda item: item[0])
        switch_count = sum(
            1 for prev, cur in zip(intervals, intervals[1:])
            if cur[1] != prev[1]
        )
        active_days = len({item[0].strftime("%Y-%m-%d") for item in intervals})
        return {
            "days": days,
            "active_days": active_days,
            "switch_count": switch_count,
            "switches_per_day": round(switch_count / max(1, active_days), 1),
            "apps": app_stats[:5],
        }

    def build_daily_totals(self) -> int:
        """Materialize day-level keyboard/mouse totals from raw detail rows.

        Idempotent UPSERT; called before raw detail retention expires so the
        month/year views and daily lists stay complete after detail cleanup.
        """
        rows = self._conn.execute(
            "SELECT d, COALESCE(SUM(keyboard),0), COALESCE(SUM(mouse),0) "
            "FROM ("
            "  SELECT substr(ts,1,10) AS d, SUM(count) AS keyboard, 0 AS mouse "
            "  FROM keystrokes GROUP BY d "
            "  UNION ALL "
            "  SELECT substr(ts,1,10) AS d, 0 AS keyboard, SUM(count) AS mouse "
            "  FROM mouse_events GROUP BY d "
            ") GROUP BY d"
        ).fetchall()
        now = datetime.now().isoformat(timespec="seconds")
        for date, keyboard, mouse in rows:
            keyboard = int(keyboard or 0)
            mouse = int(mouse or 0)
            self._conn.execute(
                "INSERT INTO daily_totals(date,keyboard,mouse,responses,updated_at) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "keyboard=excluded.keyboard, mouse=excluded.mouse, "
                "responses=excluded.keyboard+excluded.mouse, updated_at=excluded.updated_at",
                (date, keyboard, mouse, keyboard + mouse, now),
            )
        self._conn.commit()
        return len(rows)

    def cleanup(self, days: int, hourly_days: int | None = None) -> int:
        """Retain raw detail for hourly_days and aggregates for days.

        Daily totals are materialized first so month/year views and daily
        lists stay complete after raw detail expires. Returns total deleted rows.
        """
        detail_days = max(1, min(int(hourly_days or days), int(days)))
        self.build_daily_totals()
        detail_cutoff = (
            datetime.now() - timedelta(days=detail_days)
        ).isoformat(timespec="seconds")
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        state_cutoff = (
            datetime.now() - timedelta(days=COLLECTOR_STATES_RETENTION_DAYS)
        ).isoformat(timespec="seconds")
        deleted = 0
        for table, column, threshold in (
            ("keystrokes", "ts", detail_cutoff),
            ("mouse_events", "ts", detail_cutoff),
            ("app_interactions", "ts", detail_cutoff),
            ("interaction_buckets", "started_at", detail_cutoff),
            ("app_usage", "start", cutoff),
            ("foreground_intervals", "ended_at", cutoff),
            ("observation_segments", "ended_at", cutoff),
            ("collector_states", "observed_at", state_cutoff),
        ):
            cursor = self._conn.execute(
                f"DELETE FROM {table} WHERE {column} < ?", (threshold,)
            )
            deleted += max(0, int(cursor.rowcount or 0))
        self._conn.commit()
        return deleted

    def file_size(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def compact(self) -> None:
        """VACUUM the database to reclaim pages freed by cleanup.

        Blocks writes briefly; callers run it on a background thread.
        """
        self._conn.execute("VACUUM")
        self._conn.commit()

"""不包含内容数据的采集事实与一秒缓冲。"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta


COLLECTOR_VERSION = "0.10.1.1"
VALID_HEALTH_STATES = {
    "running",
    "paused",
    "locked",
    "sleeping",
    "unknown",
    "degraded",
}


def local_now() -> datetime:
    return datetime.now().astimezone()


@dataclass(frozen=True)
class ForegroundSnapshot:
    app: str = "unknown"
    path: str = ""

    def __post_init__(self):
        object.__setattr__(self, "app", (self.app or "unknown").strip() or "unknown")
        object.__setattr__(self, "path", self.path or "")


@dataclass(frozen=True)
class InteractionBucket:
    started_at: datetime
    ended_at: datetime
    app: str
    path: str = ""
    keyboard_down_count: int = 0
    mouse_button_count: int = 0
    scroll_event_count: int = 0
    collector_version: str = COLLECTOR_VERSION
    health_state: str = "running"

    @property
    def input_event_count(self) -> int:
        return (
            max(0, int(self.keyboard_down_count))
            + max(0, int(self.mouse_button_count))
            + max(0, int(self.scroll_event_count))
        )


class KeyboardPressState:
    """只计物理按下一次；同一按键的系统自动重复不重复计数。"""

    def __init__(self):
        self._pressed: set[tuple[int, int]] = set()
        self._lock = threading.Lock()

    def on_key_down(self, vk_code: int, scan_code: int) -> bool:
        key = (int(vk_code), int(scan_code))
        with self._lock:
            if key in self._pressed:
                return False
            self._pressed.add(key)
            return True

    def on_key_up(self, vk_code: int, scan_code: int) -> None:
        with self._lock:
            self._pressed.discard((int(vk_code), int(scan_code)))

    def reset(self) -> None:
        with self._lock:
            self._pressed.clear()


class ObservationBuffer:
    """按事件发生秒、前台进程和健康状态聚合输入事实。"""

    def __init__(self, collector_version: str = COLLECTOR_VERSION):
        self.collector_version = collector_version
        self._buckets: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _second(value: datetime) -> datetime:
        return value.replace(microsecond=0)

    def _record(
        self,
        kind: str,
        occurred_at: datetime,
        foreground: ForegroundSnapshot,
        health_state: str,
    ) -> None:
        state = health_state if health_state in VALID_HEALTH_STATES else "unknown"
        started_at = self._second(occurred_at)
        key = (started_at, foreground.app, foreground.path, state)
        with self._lock:
            row = self._buckets.setdefault(key, {
                "keyboard_down_count": 0,
                "mouse_button_count": 0,
                "scroll_event_count": 0,
            })
            row[kind] += 1

    def record_keyboard(
        self,
        occurred_at: datetime,
        foreground: ForegroundSnapshot,
        health_state: str = "running",
    ) -> None:
        self._record("keyboard_down_count", occurred_at, foreground, health_state)

    def record_mouse_button(
        self,
        occurred_at: datetime,
        foreground: ForegroundSnapshot,
        health_state: str = "running",
    ) -> None:
        self._record("mouse_button_count", occurred_at, foreground, health_state)

    def record_scroll(
        self,
        occurred_at: datetime,
        foreground: ForegroundSnapshot,
        health_state: str = "running",
    ) -> None:
        self._record("scroll_event_count", occurred_at, foreground, health_state)

    def drain(self, before: datetime | None = None, force: bool = False) -> list[InteractionBucket]:
        cutoff = self._second(before or local_now())
        selected = []
        with self._lock:
            for key in list(self._buckets):
                started_at, app, path, state = key
                ended_at = started_at + timedelta(seconds=1)
                if not force and ended_at > cutoff:
                    continue
                counts = self._buckets.pop(key)
                selected.append(InteractionBucket(
                    started_at=started_at,
                    ended_at=ended_at,
                    app=app,
                    path=path,
                    collector_version=self.collector_version,
                    health_state=state,
                    **counts,
                ))
        return sorted(selected, key=lambda item: (item.started_at, item.app, item.path))

    def requeue(self, bucket: InteractionBucket) -> None:
        key = (
            self._second(bucket.started_at),
            bucket.app,
            bucket.path,
            bucket.health_state,
        )
        with self._lock:
            row = self._buckets.setdefault(key, {
                "keyboard_down_count": 0,
                "mouse_button_count": 0,
                "scroll_event_count": 0,
            })
            row["keyboard_down_count"] += max(0, int(bucket.keyboard_down_count))
            row["mouse_button_count"] += max(0, int(bucket.mouse_button_count))
            row["scroll_event_count"] += max(0, int(bucket.scroll_event_count))

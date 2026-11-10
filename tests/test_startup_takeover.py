# -*- coding: utf-8 -*-
"""startup_guard：已有实例「是否仍在采集」判定 + /api/internal/status 查询。"""
import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from startup_guard import (
    NON_COLLECTING_STATES,
    STALE_ACTIVITY_SECONDS,
    TAKEOVER_COOLDOWN_SECONDS,
    existing_instance_is_stale,
    parse_iso,
    query_status,
    takeover_in_cooldown,
)


class _FakeHandler(BaseHTTPRequestHandler):
    status = 200
    body = b"{}"

    def do_GET(self):
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class _Server:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else b"{}"

        class H(_FakeHandler):
            pass

        H.status = self.status
        H.body = self.body
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def now():
    return datetime(2026, 8, 5, 12, 0, 0).astimezone()


def test_query_status_ok():
    payload = {"ok": True, "collector_state": "running"}
    srv = _Server(status=200, body=json.dumps(payload).encode())
    try:
        reachable, status = query_status(srv.port, timeout=1.0)
        assert reachable is True
        assert status["collector_state"] == "running"
    finally:
        srv.close()


def test_query_status_missing_endpoint_old_build():
    srv = _Server(status=404, body=b"")
    try:
        reachable, status = query_status(srv.port, timeout=1.0)
        assert reachable is True
        assert status is None
    finally:
        srv.close()


def test_query_status_unreachable():
    # 找一个未占用端口：先起一个服务器拿到端口再关闭。
    probe = _Server(status=200)
    port = probe.port
    probe.close()
    reachable, status = query_status(port, timeout=0.3)
    assert reachable is False
    assert status is None


def test_parse_iso_invalid():
    assert parse_iso(None) is None
    assert parse_iso("") is None
    assert parse_iso("not-a-date") is None


def test_stale_when_server_unreachable(now):
    assert existing_instance_is_stale(False, None, None, now=now) is True


@pytest.mark.parametrize("state", sorted(NON_COLLECTING_STATES))
def test_stale_when_non_collecting_state(now, state):
    status = {"collector_state": state, "last_activity": now.isoformat()}
    assert existing_instance_is_stale(True, status, None, now=now) is True


def test_healthy_when_running_and_recent(now):
    status = {
        "collector_state": "running",
        "last_activity": (now - timedelta(seconds=30)).isoformat(),
    }
    assert existing_instance_is_stale(True, status, None, now=now) is False


def test_stale_when_running_but_no_recent_activity(now):
    status = {
        "collector_state": "running",
        "last_activity": (now - timedelta(seconds=STALE_ACTIVITY_SECONDS + 60)).isoformat(),
    }
    assert existing_instance_is_stale(True, status, None, now=now) is True


def test_not_stale_when_running_without_activity(now):
    # 刚启动：状态 running 但还没有任何活动记录 → 不视为 stale。
    status = {"collector_state": "running", "last_activity": None}
    assert existing_instance_is_stale(True, status, None, now=now) is False


def test_fallback_local_activity_recent(now):
    assert existing_instance_is_stale(
        True, None, now - timedelta(minutes=1), now=now
    ) is False


def test_fallback_local_activity_old(now):
    assert existing_instance_is_stale(
        True, None, now - timedelta(hours=2), now=now
    ) is True


def test_fallback_unknown_conservative(now):
    # 旧版实例且本地库无法判定 → 保守返回 False，避免误杀健康实例。
    assert existing_instance_is_stale(True, None, None, now=now) is False


def test_naive_local_activity_handled(now):
    # 本地库时间可能是 naive（无时区），不应抛异常。
    naive_old = (now - timedelta(hours=3)).replace(tzinfo=None)
    assert existing_instance_is_stale(True, None, naive_old, now=now) is True
def test_takeover_in_cooldown_no_marker(now):
    assert takeover_in_cooldown(None, now=now) is False
    assert takeover_in_cooldown("", now=now) is False


def test_takeover_in_cooldown_fresh(now):
    marker = (now - timedelta(minutes=5)).isoformat()
    assert takeover_in_cooldown(marker, now=now) is True


def test_takeover_in_cooldown_expired(now):
    marker = (now - timedelta(minutes=16)).isoformat()
    assert takeover_in_cooldown(marker, now=now) is False


def test_takeover_in_cooldown_invalid(now):
    assert takeover_in_cooldown("not-a-date", now=now) is False


def test_takeover_in_cooldown_boundary(now):
    marker = (now - timedelta(seconds=TAKEOVER_COOLDOWN_SECONDS)).isoformat()
    assert takeover_in_cooldown(marker, now=now) is False


def test_takeover_in_cooldown_custom_window(now):
    marker = (now - timedelta(seconds=30)).isoformat()
    assert takeover_in_cooldown(marker, now=now, cooldown_seconds=10) is False

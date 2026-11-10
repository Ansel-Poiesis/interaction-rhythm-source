import base64
import json
import os
import re
import smtplib
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch
import urllib.error
import urllib.request

import pytest

from db import DB
from interaction_rhythm.email_reports import (
    EmailReports, build_report, email_address, latest_period, smtp_send,
)
from interaction_rhythm.observations import InteractionBucket
from interaction_rhythm.protected_store import protect, unprotect
from stats import StatsServer


class Clock:
    def __init__(self, value="2026-10-26T10:00:00"):
        self.set(value)

    def set(self, value):
        self.value = datetime.fromisoformat(value).timestamp()

    def __call__(self):
        return self.value


class Mailbox:
    def __init__(self):
        self.messages = []
        self.fail = False

    def __call__(self, config, recipient, subject, text, body, message_id):
        if self.fail:
            raise OSError("SECRET-SERVER-RESPONSE")
        self.messages.append(dict(recipient=recipient, subject=subject, text=text, html=body, id=message_id))

    def code(self):
        return re.search(r"验证码：(\d{6})", self.messages[-1]["text"])[1]


@pytest.fixture
def env(tmp_path):
    db = DB(tmp_path / "tracker.db")
    mailbox, clock = Mailbox(), Clock()
    # Unit fixture codec only. Production never uses this reversible test codec.
    seal = lambda value: base64.b64encode(value.encode()).decode()
    unseal = lambda value: base64.b64decode(value).decode()
    manager = EmailReports(db, send=mailbox, clock=clock, seal=seal, unseal=unseal)
    yield db, mailbox, clock, manager
    manager.stop()
    db.close()


def configure(manager, **patches):
    return manager.configure(dict(host="smtp.example.com", port=465, security="ssl",
                                  sender="sender@example.com", password="test", **patches))


def login(manager, mailbox, email="reader@example.com"):
    manager.request_code({"email": email})
    return manager.verify({"email": email, "code": mailbox.code()})


def test_windows_protection_roundtrip_and_no_plaintext(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows DPAPI integration")
    value = "授权码-PRIVATE-123-🪟"
    protected = protect(value)
    assert value not in protected
    assert unprotect(protected) == value
    db = DB(tmp_path / "tracker.db")
    try:
        manager = EmailReports(db)
        configure(manager)
        saved = manager.path.read_text(encoding="utf-8")
        assert "SECRET-APP-PASSWORD" not in saved
        assert "sender@example.com" not in saved
        assert EmailReports(db).status()["configured"]
        assert "password" not in manager.status()["smtp"]
    finally:
        db.close()


@pytest.mark.parametrize("address", ["a@example.com\r\nBcc:other@example.com", "name <a@example.com>", "x", "x@localhost"])
def test_reject_header_injection_and_invalid_addresses(address):
    with pytest.raises(ValueError):
        email_address(address)


def test_login_gates_codes_expiry_reuse_and_logout(env):
    db, mailbox, clock, manager = env
    with pytest.raises(ValueError, match="配置"):
        manager.request_code({"email": "reader@example.com"})
    configure(manager)
    for command, body in [(manager.preview, {}), (manager.subscribe, {"weekly": True, "monthly": False}),
                          (manager.send_latest, {"consent": True})]:
        with pytest.raises(ValueError, match="登录"):
            command(body)
    manager.request_code({"email": "reader@example.com"})
    code = mailbox.code()
    with pytest.raises(ValueError):
        manager.verify({"email": "other@example.com", "code": code})
    assert not manager.status()["logged_in"]
    clock.value += 601
    with pytest.raises(ValueError, match="失效"):
        manager.verify({"email": "reader@example.com", "code": code})
    status = login(manager, mailbox)
    assert status["email"] == "reader@example.com"
    with pytest.raises(ValueError):
        manager.verify({"email": "reader@example.com", "code": mailbox.code()})
    manager.subscribe({"weekly": True, "monthly": False, "consent": True})
    manager.logout({})
    assert not manager.status()["logged_in"]
    assert manager.state["subscriptions"] == {}
    before = len(mailbox.messages)
    clock.set("2026-11-10T10:00:00")
    manager.process_due()
    assert len(mailbox.messages) == before


def test_wrong_codes_are_bounded_and_resend_quota_survives_restart(env):
    db, mailbox, clock, manager = env
    configure(manager)
    manager.request_code({"email": "reader@example.com"})
    code = mailbox.code()
    for _ in range(5):
        with pytest.raises(ValueError):
            manager.verify({"email": "reader@example.com", "code": "wrong"})
    with pytest.raises(ValueError, match="失效"):
        manager.verify({"email": "reader@example.com", "code": code})
    with pytest.raises(ValueError, match="60 秒"):
        manager.request_code({"email": "reader@example.com"})
    restarted = EmailReports(db, send=mailbox, clock=clock, seal=manager.seal, unseal=manager.unseal)
    with pytest.raises(ValueError, match="60 秒"):
        restarted.request_code({"email": "reader@example.com"})
    for _ in range(9):
        clock.value += 61
        restarted.request_code({"email": "reader@example.com"})
    clock.value += 61
    with pytest.raises(ValueError, match="10 次"):
        restarted.request_code({"email": "reader@example.com"})


def test_subscription_consent_and_independent_choices(env):
    _, mailbox, clock, manager = env
    configure(manager)
    login(manager, mailbox)
    with pytest.raises(ValueError, match="确认"):
        manager.subscribe({"weekly": True, "monthly": True})
    with pytest.raises(ValueError, match="开关"):
        manager.subscribe({"weekly": "false", "monthly": False, "consent": True})
    manager.subscribe({"weekly": True, "monthly": True, "consent": True})
    status = manager.subscribe({"weekly": False, "monthly": True, "consent": True})
    assert not status["subscriptions"]["weekly"]["enabled"]
    assert status["subscriptions"]["monthly"]["enabled"]
    manager.subscribe({"weekly": False, "monthly": False})
    count = len(mailbox.messages)
    clock.set("2026-11-09T10:00:00")
    manager.process_due()
    assert len(mailbox.messages) == count


@pytest.mark.parametrize("kind,today,expected", [
    ("weekly", date(2026, 11, 1), (date(2026, 10, 19), date(2026, 10, 25))),
    ("weekly", date(2026, 11, 2), (date(2026, 10, 26), date(2026, 11, 1))),
    ("monthly", date(2024, 3, 1), (date(2024, 2, 1), date(2024, 2, 29))),
    ("monthly", date(2026, 1, 1), (date(2025, 12, 1), date(2025, 12, 31))),
])
def test_complete_calendar_periods(kind, today, expected):
    assert latest_period(kind, today) == expected


def test_report_aggregate_contract_privacy_and_retained_daily_fallback(env):
    db, _, _, _ = env
    start = datetime.fromisoformat("2026-10-26T10:00:00").astimezone()
    db.add_interaction_buckets([InteractionBucket(start, start + timedelta(seconds=1),
        "PRIVATE-APP.exe", r"C:\PRIVATE-PATH\app.exe", 10, 3, 2)])
    db.add_session("PRIVATE-APP.exe", "PRIVATE-WINDOW-TITLE", start.isoformat(),
                   (start + timedelta(hours=1)).isoformat(), r"C:\PRIVATE-PATH\app.exe")
    db.add_observation_segment("collector", "running", start, start + timedelta(hours=1))
    db._conn.execute("INSERT INTO daily_totals VALUES(?,?,?,?,?)", ("2026-10-27", 70, 30, 100, "now"))
    db._conn.commit()
    report = build_report(db, "weekly", date(2026, 10, 26), date(2026, 11, 1))
    first, second = report["daily"][:2]
    assert (first["keyboard"], first["clicks"], first["scroll"]) == (10, 3, 2)
    assert first["healthy_seconds"] == 3600
    assert second["keyboard"] == 70 and second["mouse"] == 30
    assert second["clicks"] is None and second["scroll"] is None
    assert second["coverage_status"] == "insufficient_data"
    serialized = json.dumps(report)
    assert "PRIVATE" not in serialized
    assert "80" in report["text"]
    assert len(build_report(db, "monthly", date(2026, 10, 1), date(2026, 10, 31))["daily"]) == 31


def test_schedule_due_at_nine_catchup_dedup_and_restart(env):
    db, mailbox, clock, manager = env
    configure(manager)
    login(manager, mailbox)
    manager.subscribe({"weekly": True, "monthly": True, "consent": True})
    count = len(mailbox.messages)
    clock.set("2026-11-01T08:59:59")
    manager.process_due()
    assert len(mailbox.messages) == count
    clock.set("2026-11-02T09:00:00")
    manager.process_due()
    assert len(mailbox.messages) == count + 2
    manager.process_due()
    assert len(mailbox.messages) == count + 2
    restarted = EmailReports(db, send=mailbox, clock=clock, seal=manager.seal, unseal=manager.unseal)
    restarted.process_due()
    assert len(mailbox.messages) == count + 2
    assert all(m["recipient"] == "reader@example.com" for m in mailbox.messages)
    clock.set("2026-11-16T10:00:00")
    restarted.process_due()
    assert len(mailbox.messages) == count + 4


def test_uncertain_delivery_is_never_auto_retried(env):
    db, mailbox, clock, manager = env
    configure(manager)
    login(manager, mailbox)
    manager.subscribe({"weekly": True, "monthly": False, "consent": True})
    clock.set("2026-11-02T10:00:00")
    mailbox.fail = True
    manager.process_due()
    history = manager.status()["deliveries"]
    assert len(history) == 1 and history[0]["status"] == "uncertain"
    assert "SECRET-SERVER" not in json.dumps(history)
    mailbox.fail = False
    count = len(mailbox.messages)
    manager.process_due()
    assert len(mailbox.messages) == count
    with pytest.raises(ValueError, match="确认"):
        manager.retry({"id": history[0]["id"]})
    manager.retry({"id": history[0]["id"], "confirm_possible_duplicate": True})
    assert len(mailbox.messages) == count + 1
    preview = manager.preview({"kind": "weekly"})
    manager.send_latest({"preview_token": preview["preview_token"], "consent": True})
    assert len(mailbox.messages) == count + 1


def test_send_is_bound_to_the_exact_preview_even_across_period_boundary(env):
    _, mailbox, clock, manager = env
    configure(manager)
    login(manager, mailbox)
    clock.set("2026-11-01T23:59:00")
    preview = manager.preview({"kind": "weekly"})
    clock.set("2026-11-02T00:01:00")
    manager.send_latest({"preview_token": preview["preview_token"], "consent": True})
    assert mailbox.messages[-1]["html"] == preview["report"]["html"]
    assert "2026-10-25" in mailbox.messages[-1]["subject"]
    clock.value += 601
    with pytest.raises(ValueError, match="预览已失效"):
        manager.send_latest({"preview_token": preview["preview_token"], "consent": True})


def test_expired_login_and_account_switch_cannot_send_old_subscription(env):
    _, mailbox, clock, manager = env
    configure(manager)
    login(manager, mailbox)
    manager.subscribe({"weekly": True, "monthly": True, "consent": True})
    count = len(mailbox.messages)
    clock.value += 91 * 86400
    manager.process_due()
    assert len(mailbox.messages) == count
    login(manager, mailbox, "other@example.com")
    assert manager.status()["subscriptions"] == {}
    assert manager.status()["deliveries"] == []


def test_smtp_requires_encryption_and_does_not_log_credentials():
    config = dict(host="smtp.example.com", port=587, security="starttls", sender="sender@example.com",
                  username="sender@example.com", password="secret")
    client = MagicMock()
    client.send_message.return_value = {}
    with patch("smtplib.SMTP", return_value=client):
        smtp_send(config, "reader@example.com", "报告", "text", "<p>text</p>", "<id@example.com>")
    assert client.starttls.called
    calls = [c[0] for c in client.mock_calls]
    assert calls.index("starttls") < calls.index("login") < calls.index("send_message")
    assert client.send_message.call_args.kwargs["to_addrs"] == ["reader@example.com"]
    assert client.close.called
    client.reset_mock()
    client.starttls.side_effect = smtplib.SMTPNotSupportedError("No TLS")
    with patch("smtplib.SMTP", return_value=client), pytest.raises(smtplib.SMTPNotSupportedError):
        smtp_send(config, "reader@example.com", "报告", "text", None, "<id@example.com>")
    client.login.assert_not_called()


def test_email_commands_require_existing_loopback_session(env):
    db, _, _, _ = env
    server = StatsServer(db, port=0)
    server.start()
    try:
        from stats import Handler
        url = f"http://127.0.0.1:{server.port}/api/email-reports"
        body = json.dumps({"action": "status"}).encode()
        def post(headers):
            return urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=5)
        with pytest.raises(urllib.error.HTTPError) as denied:
            post({"Content-Type": "application/json"})
        assert denied.value.code == 403
        cookie = Handler.guard.cookie_header().split(";")[0]
        with pytest.raises(urllib.error.HTTPError) as denied:
            post({"Cookie": cookie, "Origin": "https://evil.example"})
        assert denied.value.code == 403
        with post({"Cookie": cookie, "Content-Type": "application/json"}) as response:
            result = json.load(response)
            assert result["ok"] and "password" not in result["smtp"]
    finally:
        server.stop()


def test_week_anchor_default_and_adjustment(env):
    _, _, clock, manager = env
    configure(manager)
    assert manager.status()["preferences"]["week_start"] == 1
    # Default Monday anchor: previous complete Mon-Sun week even midweek.
    assert latest_period("weekly", date(2026, 11, 4)) == (date(2026, 10, 26), date(2026, 11, 1))
    # Wednesday anchor: weeks run Wed-Tue.
    assert latest_period("weekly", date(2026, 11, 4), 3) == (date(2026, 10, 28), date(2026, 11, 3))
    with pytest.raises(ValueError, match="周起始日"):
        latest_period("weekly", date(2026, 11, 4), 8)
    mailbox = env[1]
    login(manager, mailbox)
    clock.set("2026-10-26T10:00:00")  # Monday
    manager.subscribe({"weekly": True, "monthly": False, "consent": True, "week_start": 3})
    assert manager.state["preferences"]["week_start"] == 3
    assert manager.state["subscriptions"]["weekly"]["since"] == "2026-10-21"
    status = manager.set_preferences({"week_start": 5})
    assert status["preferences"]["week_start"] == 5
    assert manager.state["subscriptions"]["weekly"]["since"] == "2026-10-23"
    with pytest.raises(ValueError, match="周起始日"):
        manager.set_preferences({"week_start": 0})


def test_week_anchor_controls_send_day(env):
    _, mailbox, clock, manager = env
    configure(manager)
    login(manager, mailbox)
    clock.set("2026-10-26T10:00:00")
    manager.subscribe({"weekly": True, "monthly": False, "consent": True, "week_start": 3})  # Wednesday
    before = len(mailbox.messages)
    clock.set("2026-10-28T08:59:00")  # Wednesday, before nine o'clock
    manager.process_due()
    assert len(mailbox.messages) == before
    clock.set("2026-10-28T09:00:00")
    manager.process_due()
    assert len(mailbox.messages) == before + 1
    assert mailbox.messages[-1]["subject"].startswith("扣舷")
    assert (mailbox.messages[-1]["text"].splitlines()[0].endswith("2026-10-27")
            or "2026-10-21" in mailbox.messages[-1]["text"])

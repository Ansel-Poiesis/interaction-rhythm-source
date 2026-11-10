import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from db import DB
from interaction_rhythm import agently_transport as agent
from interaction_rhythm.email_reports import EmailReports, send_email


SENDER = "reporter@agent.qq.com"


def identity(sender=SENDER, primary=True, scopes=None):
    return {"ok": True, "data": {"aliases": [{"email": sender, "is_primary": primary}],
                                 "scopes": scopes if scopes is not None else ["mail:send"]}}


def test_run_keeps_default_account_and_environment_token_out_of_child(monkeypatch):
    monkeypatch.setenv("AGENTLY_ACCESS_TOKEN", "PRIVATE-TOKEN")
    monkeypatch.setenv("AGENTLY_WORKSPACE", "unrelated-default")
    monkeypatch.setenv("AGENTLY_CLI_CONFIG_DIR", "unrelated-config")
    result = subprocess.CompletedProcess([], 0, json.dumps(identity()), "")
    with patch.object(agent, "cli_path", return_value=Path("C:/fixture/agently-cli.exe")), patch.object(agent.subprocess, "run", return_value=result) as run:
        assert agent._run(["+me"]) == identity()
    child = run.call_args.kwargs
    assert "AGENTLY_ACCESS_TOKEN" not in child["env"]
    assert "AGENTLY_CLI_CONFIG_DIR" not in child["env"]
    assert child["env"]["AGENTLY_WORKSPACE"] == "digitalrhythm"
    assert child["env"]["AGENTLY_DISABLE_TELEMETRY"] == "1"
    assert "shell" not in child
    assert child["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert os.environ["AGENTLY_WORKSPACE"] == "unrelated-default"
    assert os.environ["AGENTLY_ACCESS_TOKEN"] == "PRIVATE-TOKEN"


@pytest.mark.parametrize("payload", [identity("other@agent.qq.com"), identity(primary=False)])
def test_wrong_sender_or_non_primary_alias_cannot_send(payload):
    with patch.object(agent, "_run", return_value=payload) as run, pytest.raises(ValueError, match="不匹配"):
        agent.send({"sender": SENDER}, "recipient@example.com", "subject", "text", None, "id")
    assert run.call_count == 1 and run.call_args.args[0] == ["+me"]


def test_send_scope_required():
    with patch.object(agent, "_run", return_value=identity(scopes=["mail:read"])), pytest.raises(ValueError, match="权限"):
        agent.check_connection(SENDER)


def test_explicit_application_send_uses_verified_primary_and_requires_queue_ack():
    with patch.object(agent, "_run", side_effect=[identity(), {"ok": True, "queued": True}]) as run:
        agent.send({"sender": SENDER}, "recipient@example.com", "subject", "fixture code", None, "id")
    args = run.call_args.args[0]
    assert args[:2] == ["message", "+send"]
    assert "--confirmed" in args and "--confirmation-token" not in args
    assert args[args.index("--to") + 1] == "recipient@example.com"
    assert "--body-file" not in args
    with patch.object(agent, "_run", side_effect=[identity(), {"ok": True, "data": {"confirmation_required": True}}]), pytest.raises(ValueError, match="未确认"):
        agent.send({"sender": SENDER}, "recipient@example.com", "subject", "fixture", None, "id")


def test_monthly_html_uses_relative_file_and_cleans_it_after_send():
    folders = []
    body = "<html>报告" + "a" * 40000 + "</html>"
    def run(args, *, cwd=None):
        if args == ["+me"]:
            return identity()
        assert args[args.index("--body-file") + 1] == "./report.html"
        assert (cwd / "report.html").read_text(encoding="utf-8") == body
        folders.append(cwd)
        return {"ok": True, "data": {"queued": True}}
    with patch.object(agent, "_run", side_effect=run):
        agent.send({"sender": SENDER}, "recipient@example.com", "subject", "text", body, "id")
    assert folders and not folders[0].exists()


@pytest.mark.parametrize("exit_code", [1, 3, 7, 8])
def test_cli_errors_never_surface_raw_response_or_credentials(exit_code):
    result = subprocess.CompletedProcess([], exit_code, 'PRIVATE-TOKEN', 'PRIVATE-SERVER-DETAILS')
    with patch.object(agent, "cli_path", return_value=Path("C:/fixture/agently-cli.exe")), patch.object(agent.subprocess, "run", return_value=result), pytest.raises(ValueError) as error:
        agent._run(["+me"])
    assert "PRIVATE" not in str(error.value)


def test_agently_configuration_checks_identity_and_revokes_old_subscription(tmp_path):
    db = DB(tmp_path / "tracker.db")
    manager = EmailReports(db, seal=lambda s: s, unseal=lambda s: s)
    manager.state["smtp"] = {"sender": "old@example.com", "password": "test"}
    manager.state["subscriptions"] = {"weekly": {"enabled": True, "since": "2026-11-02"}}
    try:
        with patch.object(agent, "check_connection", side_effect=ValueError("wrong sender")), pytest.raises(ValueError):
            manager.configure({"transport": "agently", "sender": SENDER})
        assert manager.state["smtp"]["sender"] == "old@example.com"
        assert manager.state["subscriptions"]["weekly"]["enabled"]
        with patch.object(agent, "check_connection"):
            status = manager.configure({"transport": "agently", "sender": SENDER, "recipient_hint": "recipient@example.com"})
        assert status["configured"] and status["transport"] == "agently"
        assert status["recipient_hint"] == "recipient@example.com"
        assert not status["logged_in"] and status["subscriptions"] == {}
        assert "password" not in manager.state["smtp"]
        assert "workspace" not in status["smtp"]
        assert manager.state["subscriptions"] == {}
    finally:
        db.close()


def test_switching_back_to_smtp_revokes_agent_subscription(tmp_path):
    db = DB(tmp_path / "tracker.db")
    manager = EmailReports(db, seal=lambda s: s, unseal=lambda s: s)
    manager.state["smtp"] = {"sender": SENDER, "transport": "agently"}
    manager.state["subscriptions"] = {"monthly": {"enabled": True, "since": "2026-11-01"}}
    try:
        status = manager.configure({"host":"smtp.example.com", "sender":"ordinary@example.com", "password":"test"})
        assert status["transport"] == "smtp"
        assert manager.state["subscriptions"] == {}
    finally:
        db.close()


def test_transport_selection_never_falls_back_to_smtp():
    with patch.object(agent, "send", side_effect=ValueError("disconnected")), patch("interaction_rhythm.email_reports.smtp_send") as smtp, pytest.raises(ValueError):
        send_email({"sender": SENDER, "transport": "agently"}, "reader@example.com", "subject", "text", None, "id")
    smtp.assert_not_called()

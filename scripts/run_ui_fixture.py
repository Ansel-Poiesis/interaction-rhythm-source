"""Serve the dashboard against disposable synthetic data for visual checks."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db import DB
from interaction_rhythm.observations import InteractionBucket
from interaction_rhythm.service import ReplayJobManager
from mimo_client import _ai_context_payload, _compact_day_context, _factual_replay
from stats import StatsServer


AI_FIELDS = [
    "date",
    "metric_version",
    "keyboard",
    "mouse",
    "mouse_buttons",
    "scroll_events",
    "mouse_split_available",
    "responses",
    "input_type",
    "keyboard_ratio_percent",
    "foreground_minutes",
    "peak_hour",
    "peak_range",
    "coverage",
    "hourly_distribution",
    "application_aggregates",
    "foreground_segments",
    "personal_comparison",
    "facts",
]

EXCLUDED_FIELDS = [
    "key_values",
    "input_text",
    "mouse_coordinates",
    "screenshots",
    "window_titles",
    "browser_page_content",
]


def seed(db: DB) -> None:
    now = datetime.now().astimezone()
    buckets = []
    apps = [
        ("Editor.exe", r"C:\Fixture\Editor.exe"),
        ("Browser.exe", r"C:\Fixture\Browser.exe"),
        ("Terminal.exe", r"C:\Fixture\Terminal.exe"),
    ]
    for day_offset in range(35, -1, -1):
        day = (now - timedelta(days=day_offset)).replace(hour=8, minute=30, second=0, microsecond=0)
        scale = 1 + ((35 - day_offset) % 7)
        for index, hour in enumerate((9, 11, 14, 16)):
            started = day.replace(hour=hour)
            app, path = apps[index % len(apps)]
            buckets.append(InteractionBucket(
                started_at=started,
                ended_at=started + timedelta(seconds=1),
                app=app,
                path=path,
                keyboard_down_count=28 * scale + index * 9,
                mouse_button_count=11 * scale + index * 3,
                scroll_event_count=5 * scale + index * 2,
            ))
        sessions = [
            (apps[0], day, day + timedelta(hours=2, minutes=10)),
            (apps[1], day + timedelta(hours=2, minutes=20), day + timedelta(hours=5)),
            (apps[2], day + timedelta(hours=5, minutes=10), day + timedelta(hours=7, minutes=30)),
        ]
        for (app, path), started, ended in sessions:
            db.add_session(app, "", started.isoformat(), ended.isoformat(), path)
        db.add_observation_segment("collector", "running", day, day + timedelta(hours=7, minutes=40))
    db.add_interaction_buckets(buckets)


def fixture_settings() -> dict:
    return {
        "auto_start": False,
        "background_start": False,
        "silent_start": False,
        "auto_update_check": False,
        "theme": "light",
        "accent": "green",
        "language": "zh",
        "intro_seen": True,
        "ai_provider": "deepseek",
        "ai_cloud_enabled": True,
        "ai_base_url": "https://api.deepseek.com/v1",
        "ai_model": "deepseek-chat",
    }


def fixture_replay(db: DB, date: str) -> dict:
    time.sleep(0.65)
    result = _factual_replay(_compact_day_context(db, date), "ui_fixture")
    if date == datetime.now().strftime("%Y-%m-%d"):
        result.update({
            "fallback": False,
            "fallback_reason": "",
            "assistant": "DeepSeek手感回放",
            "model": "deepseek-chat",
            "model_note": "云端仅接收设置页列明的合成聚合字段。",
            "provider": "deepseek",
        })
    return result


def configure_service(server: StatsServer, db: DB, email_inbox: Path | None = None) -> None:
    handler = server._httpd.RequestHandlerClass
    service = handler.service
    def fixture_email_send(config, recipient, subject, text, body, message_id):
        # UI fixtures never contact a real SMTP service or send personal data.
        if email_inbox:
            email_inbox.parent.mkdir(parents=True, exist_ok=True)
            with email_inbox.open("a", encoding="utf-8") as output:
                output.write(json.dumps({"recipient": recipient, "subject": subject,
                                         "text": text, "html": body}, ensure_ascii=False) + "\n")
    service.email_report_manager().send = fixture_email_send
    from interaction_rhythm import agently_transport
    agently_transport.check_connection = lambda _sender: None
    state = fixture_settings()
    original_serve_static = handler._serve_static

    def serve_static(request) -> None:
        if urlparse(request.path).path == "/__fixture/mobile":
            body = (
                '<!doctype html><html><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<style>*{box-sizing:border-box}html,body{margin:0;width:390px;height:844px;overflow:hidden;background:#fff}'
                'iframe{display:block;width:390px;height:844px;border:0}</style></head>'
                '<body><iframe id="mobile-app" src="/?desktop=1"></iframe><script>'
                'const frame=document.getElementById("mobile-app");'
                'frame.addEventListener("load",()=>setTimeout(()=>{'
                'const state=new URLSearchParams(location.search).get("state")||"home";'
                'const doc=frame.contentDocument;'
                'const button=name=>[...doc.querySelectorAll("button")].find(x=>x.getAttribute("aria-label")===name||x.textContent.trim()===name);'
                'if(state==="settings"){button("设置").click();}'
                'if(state==="replay"){button("本周观察").click();}'
                '},850));'
                '</script></body></html>'
            ).encode("utf-8")
            request.send_response(200)
            request.send_header("Content-Type", "text/html; charset=utf-8")
            request.send_header("Content-Length", str(len(body)))
            request.end_headers()
            request.wfile.write(body)
            return
        if urlparse(request.path).path == "/__fixture/drive":
            # 通用视觉检查驱动页：把待执行的前端函数序列编码在 ?steps= 里，
            # 逐个在 iframe 内执行后等待，供 headless Chrome 截图任意界面状态。
            query = parse_qs(urlparse(request.path).query)
            steps = query.get("steps", [""])[0].split("|")
            wait_ms = int(query.get("wait", ["1400"])[0])
            calls = " ".join(
                f'try{{frame.contentWindow.{step}}}catch(e){{console.error("step failed: %s", e)}}' % step
                for step in steps
                if step
            )
            body = (
                '<!doctype html><html><head><meta charset="utf-8">'
                '<style>*{box-sizing:border-box}html,body{margin:0;background:#fff}</style></head>'
                '<body><iframe id="app" style="display:block;border:0;width:100vw;height:100vh"></iframe><script>'
                'const frame=document.getElementById("app");'
                'const q=new URLSearchParams(location.search);'
                'frame.src="/?desktop=1"+(q.get("extra")||"");'
                f'frame.addEventListener("load",()=>setTimeout(()=>{{{calls};'
                'try{const m=frame.contentDocument.getElementById("modal");'
                'document.title="DRIVE:"+((m&&!m.hidden)?"modal-open":"no-modal")+'
                '":"+(frame.contentDocument.querySelectorAll(".modal-card").length)}catch(e){document.title="DRIVE:err"}'
                f'}},2*{wait_ms})));'
                '</script></body></html>'
            ).encode("utf-8")
            request.send_response(200)
            request.send_header("Content-Type", "text/html; charset=utf-8")
            request.send_header("Content-Length", str(len(body)))
            request.end_headers()
            request.wfile.write(body)
            return
        original_serve_static(request)

    handler._serve_static = serve_static

    def save_settings(patch: dict) -> dict:
        if isinstance(patch, dict):
            state.update({key: value for key, value in patch.items() if key in state})
        return {"ok": True, "settings": dict(state)}

    def ai_preview(date: str) -> dict:
        payload = _ai_context_payload(_compact_day_context(db, date))
        return {
            "ok": True,
            "date": date,
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "cloud_enabled": True,
            "key_configured": True,
            "fields": list(AI_FIELDS),
            "excluded_fields": list(EXCLUDED_FIELDS),
            "payload": payload,
        }

    service.settings = lambda: {"ok": True, "settings": dict(state)}
    service.save_settings = save_settings

    # 备份端点改用内存桩：真实 DataBackupManager 会解析工程 data/ 目录，
    # 视觉检查期间误触 POST 会把快照写进真实数据目录（2026-09-29 事故）。
    fake_backups: dict = {}

    def fake_create_backup(body):
        backup_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-fixture"
        fake_backups[backup_id] = {
            "id": backup_id,
            "name": backup_id,
            "created_at_iso": datetime.now().isoformat(timespec="seconds"),
            "version": "0.11.5",
            "size": 204800,
            "source": "manual",
        }
        return {"ok": True, "backup": fake_backups[backup_id]}

    def fake_list_backups():
        return {
            "ok": True,
            "backup_root": "C:\\Fixture\\data\\backups",
            "backups": sorted(fake_backups.values(), key=lambda item: item["id"], reverse=True),
        }

    def fake_restore_backup(body):
        return {"ok": True, "restored": body.get("backup_id")}

    def fake_delete_backup(body):
        backup_id = body.get("backup_id") or body.get("id")
        if backup_id not in fake_backups:
            return {"ok": False, "error": "backup not found"}
        del fake_backups[backup_id]
        return {"ok": True, "deleted": backup_id}

    service.create_data_backup = fake_create_backup
    service.list_data_backups = fake_list_backups
    service.restore_data_backup = fake_restore_backup
    service.delete_data_backup = fake_delete_backup
    service.ai_config = lambda: {
        "ok": True,
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "cloud_enabled": True,
        "key_configured": True,
    }
    service.ai_preview = ai_preview
    service.replay_jobs = ReplayJobManager(lambda date: fixture_replay(db, date))
    service.update_status = lambda: {"ok": True, "status": "idle"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18931)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--email-inbox", type=Path, help="Local-only simulated email output for UI checks")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="interaction-rhythm-ui-fixture-") as temp_dir:
        db = DB(Path(temp_dir) / "fixture.db")
        server = StatsServer(db, port=args.port)
        try:
            seed(db)
            configure_service(server, db, args.email_inbox)
            server.start()
            args.ready_file.write_text(str(server.port), encoding="utf-8")
            while not args.stop_file.exists():
                time.sleep(0.1)
        finally:
            server.stop()
            db.close()
            args.ready_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

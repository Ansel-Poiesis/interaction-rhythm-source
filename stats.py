"""内建 HTTP 服务器 + REST API"""
import csv
import io
import json
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from config import PORT, APP_VERSION
from interaction_rhythm.service import ApplicationService, SessionGuard

if getattr(sys, 'frozen', False):
    STATIC_DIR = Path(sys._MEIPASS) / "static"
else:
    STATIC_DIR = Path(__file__).parent / "static"


def _safe_int(value: str, default: int, lo: int = 1, hi: int = 365) -> int:
    """安全地将查询参数转为整数，越界或非法值回退到 default。"""
    try:
        return max(lo, min(int(value), hi))
    except (ValueError, TypeError):
        return default


class Handler(SimpleHTTPRequestHandler):
    db = None
    service = None
    guard = None
    shutdown_callback = None
    shutdown_token = None

    def log_message(self, _format, *_args):
        return

    def finish(self):
        try:
            super().finish()
        finally:
            close_connection = getattr(Handler.db, "close_thread_connection", None)
            if close_connection:
                close_connection()

    def do_GET(self):
        if not self._valid_host():
            self._json({"ok": False, "error": "invalid host"}, status=403)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}

        if path == "/api/internal/status":
            self._json(self._internal_status())
            return
        if path == "/api/today":
            self._json(Handler.service.today())
        elif path == "/api/day":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            self._json(Handler.service.day(d))
        elif path == "/api/metrics":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            self._json(Handler.service.day_metrics(d))
        elif path == "/api/metric-registry":
            self._json(Handler.service.metric_registry())
        elif path == "/api/personal-reference":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            try:
                self._json(Handler.service.personal_reference(
                    params.get("metric_id", "IR-EVT-004"), d
                ))
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, status=400)
        elif path == "/api/heatmap":
            days = _safe_int(params.get("days", "7"), 7)
            tomorrow = params.get("tomorrow", "0") == "1"
            self._json(Handler.service.heatmap(days, tomorrow, params.get("end", "")))
        elif path == "/api/month-heatmap":
            self._json(Handler.service.month_heatmap(params.get("month", "")))
        elif path == "/api/year-heatmap":
            self._json(Handler.service.year_heatmap(params.get("year", "")))
        elif path == "/api/icon":
            from icons import icon_bytes
            raw_path = unquote(params.get("path", ""))
            # 安全检查：只允许绝对路径，拒绝路径穿越
            icon_path = Path(raw_path)
            if not raw_path or ".." in raw_path or len(raw_path) > 500 or not icon_path.is_absolute():
                self.send_error(400)
                return
            data = icon_bytes(raw_path)
            if not data:
                self.send_response(404)
                self.send_header("Cache-Control", "public, max-age=900")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/hourly":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            self._json({"date": d, "hourly": Handler.service.db.hourly_chart(d)})
        elif path == "/api/hour-detail":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            hour = _safe_int(params.get("hour", "0"), 0, 0, 23)
            self._json(Handler.service.hour_detail(d, hour))
        elif path == "/api/apps":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            self._json(Handler.service.applications(d))
        elif path == "/api/app-detail":
            from datetime import datetime
            d = params.get("date", datetime.now().strftime("%Y-%m-%d"))
            a = params.get("app", "")
            self._json(Handler.service.application_detail(a, d))
        elif path == "/api/app-analysis":
            days = _safe_int(params.get("days", "7"), 7)
            self._json(Handler.service.application_analysis(params.get("app", ""), days))
        elif path == "/api/export":
            if not self._authorize_command():
                return
            days = _safe_int(params.get("days", "30"), 30)
            fmt = params.get("format", "json").lower()
            data = Handler.service.export(days)
            if fmt == "csv":
                self._download_csv(data)
            else:
                self._download_json(data)
        elif path == "/api/day-replay":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            self._json(Handler.service.day_replay(d))
        elif path == "/api/ai/config":
            self._json(Handler.service.ai_config())
        elif path == "/api/ai/preview":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            self._json(Handler.service.ai_preview(d))
        elif path == "/api/media-snapshot":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            self._json(Handler.service.media_snapshot(d))
        elif path == "/api/mimo-replay":
            self._json(
                {"ok": False, "error": "AI replay is a protected asynchronous POST command"},
                status=405,
            )
        elif path == "/api/replay/jobs":
            if not self._authorize_command():
                return
            job = Handler.service.replay_status(params.get("id", ""))
            if job:
                self._json({"ok": True, **job})
            else:
                self._json({"ok": False, "error": "job not found"}, status=404)
        elif path == "/api/insights":
            days = _safe_int(params.get("days", "7"), 7)
            self._json(Handler.service.insights(days))
        elif path == "/api/agent/purposes":
            self._json(Handler.service.agent_purposes())
        elif path == "/api/agent/observations":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            try:
                self._json(Handler.service.weekly_observations(
                    d,
                    params.get("purpose", "understand_personal_rhythm"),
                ))
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, status=400)
        elif path == "/api/agent/day":
            d = params.get("date", "")
            from datetime import datetime
            try:
                d = datetime.fromisoformat(d).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "invalid date"}, status=400)
                return
            try:
                self._json(Handler.service.agent_day(d))
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, status=400)
        elif path == "/api/agent/corrections":
            d = params.get("date", "")
            from datetime import datetime
            d = d or datetime.now().strftime("%Y-%m-%d")
            self._json(Handler.service.agent_corrections(
                d,
                params.get("purpose", "understand_personal_rhythm"),
            ))
        elif path == "/api/app-labels":
            self._json(Handler.service.app_labels())
        elif path == "/api/trend":
            weeks = params.get("weeks", "")
            self._json(Handler.service.trend(int(weeks) if weeks.isdigit() else 12))
        elif path == "/api/return-intervals":
            days = params.get("days", "")
            self._json(Handler.service.return_intervals(int(days) if days.isdigit() else 7))
        elif path == "/api/agent/jobs":
            if not self._authorize_command():
                return
            job = Handler.service.agent_deep_read_status(params.get("id", ""))
            if job:
                self._json({"ok": True, **job})
            else:
                self._json({"ok": False, "error": "job not found"}, status=404)
        elif path == "/api/daily":
            days = _safe_int(params.get("days", "7"), 7)
            self._json(Handler.service.daily(days))
        elif path == "/api/update/check":
            self._json(
                {"ok": False, "error": "update check requires POST"},
                status=405,
            )
        elif path == "/api/update/status":
            self._handle_update_status()
        elif path == "/api/data/backups":
            if not self._authorize_command():
                return
            self._handle_data_backup_list()
        elif path == "/api/feedback":
            if not self._authorize_command():
                return
            self._handle_feedback_list()
        elif path == "/api/feedback/export":
            if not self._authorize_command():
                return
            self._handle_feedback_export()
        elif path == "/api/settings":
            self._handle_settings()
        else:
            self._serve_static()

    def do_POST(self):
        if not self._valid_host():
            self._json({"ok": False, "error": "invalid host"}, status=403)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/internal/shutdown":
            self._handle_internal_shutdown()
            return
        if not self._authorize_command():
            return
        if parsed.path == "/api/email-reports":
            try:
                body = self._read_json_body()
                if not isinstance(body, dict):
                    raise ValueError("请求格式无效")
                manager = Handler.service.email_report_manager()
                actions = {
                    "status": lambda _: manager.status(),
                    "configure": manager.configure,
                    "request_code": manager.request_code,
                    "verify": manager.verify,
                    "logout": manager.logout,
                    "subscribe": manager.subscribe,
                    "set_preferences": manager.set_preferences,
                    "preview": manager.preview,
                    "send_latest": manager.send_latest,
                    "retry": manager.retry,
                }
                action = actions.get(body.get("action"))
                if action is None:
                    raise ValueError("未知邮件操作")
                self._json(action(body))
            except (ValueError, TypeError, OSError):
                # Manager messages are locally generated; never return raw SMTP or DPAPI errors.
                import sys
                error = sys.exc_info()[1]
                message = str(error) if type(error) is ValueError else "邮件配置或本地存储操作失败"
                self._json({"ok": False, "error": message}, status=400)
        elif parsed.path == "/api/update/install":
            self._handle_update_install()
        elif parsed.path == "/api/update/check":
            self._handle_update_check()
        elif parsed.path == "/api/settings":
            self._handle_settings_save()
        elif parsed.path == "/api/replay/jobs":
            try:
                from datetime import datetime
                body = self._read_json_body()
                date = body.get("date", "") if isinstance(body, dict) else ""
                try:
                    date = datetime.fromisoformat(date).strftime("%Y-%m-%d")
                except (TypeError, ValueError):
                    date = datetime.now().strftime("%Y-%m-%d")
                self._json(
                    {"ok": True, **Handler.service.submit_replay(date)},
                    status=202,
                )
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=400)
        elif parsed.path == "/api/agent/jobs":
            try:
                from datetime import datetime
                body = self._read_json_body()
                if not isinstance(body, dict):
                    raise ValueError("缺少请求体")
                end_date = body.get("end_date") or body.get("date") or ""
                try:
                    end_date = datetime.fromisoformat(end_date).strftime("%Y-%m-%d")
                except (TypeError, ValueError):
                    end_date = datetime.now().strftime("%Y-%m-%d")
                purpose_id = str(
                    body.get("purpose") or body.get("purpose_id") or "understand_personal_rhythm"
                ).strip()
                thinking_enabled = bool(body.get("thinking_enabled"))
                scope = str(body.get("scope") or "week").strip().lower()
                self._json(
                    {
                        "ok": True,
                        **Handler.service.submit_agent_deep_read(
                            end_date,
                            purpose_id,
                            thinking_enabled,
                            scope,
                        ),
                    },
                    status=202,
                )
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=400)
        elif parsed.path == "/api/agent/corrections":
            try:
                body = self._read_json_body()
                self._json(Handler.service.save_agent_correction(body), status=201)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=400)
        elif parsed.path == "/api/app-labels":
            try:
                body = self._read_json_body()
                self._json(Handler.service.save_app_label(body), status=201)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=400)
        elif parsed.path == "/api/privacy/window-titles/delete":
            try:
                self._json(Handler.service.delete_historical_window_titles())
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=400)
        elif parsed.path == "/api/db/compact":
            try:
                self._json(Handler.service.compact_database())
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=500)
        elif parsed.path == "/api/data/backup":
            try:
                body = self._read_json_body()
                self._json(Handler.service.create_data_backup(body), status=201)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=500)
        elif parsed.path == "/api/data/restore":
            try:
                body = self._read_json_body()
                self._json(Handler.service.restore_data_backup(body))
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=500)
        elif parsed.path == "/api/data/delete":
            try:
                body = self._read_json_body()
                self._json(Handler.service.delete_data_backup(body))
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, status=500)
        elif parsed.path == "/api/feedback":
            self._handle_feedback_save()
        else:
            self._json({"ok": False, "error": "not found"}, status=404)

    def _internal_status(self):
        """本机健康状态：供新实例判断「已有实例是否仍在采集」。

        只允许本机 Host 访问（与 shutdown 端点一致）；不携带个人数据。
        """
        import os
        data = {}
        if Handler.db is not None:
            try:
                data = Handler.db.collector_status_summary()
            except Exception:
                data = {}
        data["ok"] = True
        data["pid"] = os.getpid()
        data["version"] = APP_VERSION
        return data

    def _handle_internal_shutdown(self):
        try:
            body = self._read_json_body()
            token = body.get("token") if isinstance(body, dict) else None
        except Exception:
            self._json({"ok": False, "error": "invalid body"}, status=400)
            return
        if not Handler.shutdown_token or token != Handler.shutdown_token:
            self._json({"ok": False, "error": "invalid token"}, status=403)
            return
        callback = Handler.shutdown_callback
        self._json({"ok": True})
        if callback:
            # 先完整返回响应，再延迟退出，避免请求方读不到 200 就收到连接重置。
            def _delayed_exit():
                time.sleep(0.3)
                callback()
            threading.Thread(target=_delayed_exit, daemon=True).start()

    def _json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _valid_host(self) -> bool:
        guard = Handler.guard
        return bool(guard and (self.headers.get("Host") or "").lower() in guard.allowed_hosts)

    def _authorize_command(self) -> bool:
        guard = Handler.guard
        if guard and guard.authorize(
            self.headers.get("Host", ""),
            self.headers.get("Origin"),
            self.headers.get("Cookie", ""),
        ):
            return True
        self._json({"ok": False, "error": "session capability required"}, status=403)
        return False

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 1024 * 1024:
            raise ValueError("请求体过大")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _handle_update_check(self):
        try:
            self._json(Handler.service.update_check())
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_data_backup_list(self):
        try:
            self._json(Handler.service.list_data_backups())
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, status=500)

    def _handle_feedback_list(self):
        try:
            self._json(Handler.service.list_feedback())
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, status=500)

    def _handle_feedback_save(self):
        try:
            body = self._read_json_body()
            self._json(Handler.service.save_feedback(body), status=201)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, status=400)

    def _handle_feedback_export(self):
        try:
            data = Handler.service.export_feedback()
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            exported_at = str(data.get("exported_at") or "").replace(":", "").replace("+", "")
            name = f"interaction-rhythm-feedback-{exported_at[:19] or 'export'}.json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, status=500)

    def _handle_update_status(self):
        try:
            self._json(Handler.service.update_status())
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_update_install(self):
        try:
            body = self._read_json_body()
            manifest = body.get("manifest") if isinstance(body, dict) else None
            if not isinstance(manifest, dict):
                raise ValueError("缺少更新清单")
            self._json(Handler.service.update_install(manifest))
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_settings(self):
        try:
            self._json(Handler.service.settings())
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_settings_save(self):
        try:
            body = self._read_json_body()
            self._json(Handler.service.save_settings(body))
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _download_json(self, data):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        name = f"interaction-rhythm-{data['range']['start']}-{data['range']['end']}.json"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _download_csv(self, data):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["section", "date", "hour", "app", "title", "path", "keyboard", "mouse", "responses", "seconds"])
        for row in data.get("daily", []):
            writer.writerow(["daily", row["date"], "", "", "", "", row["keyboard"], row["mouse"], row["responses"], ""])
        for day in data.get("heatmap", []):
            keyboard = day.get("keyboard_hourly", [])
            mouse = day.get("mouse_hourly", [])
            total = day.get("hourly", [])
            for hour in range(24):
                writer.writerow([
                    "hourly", day["date"], hour, "", "", "",
                    keyboard[hour] if hour < len(keyboard) else 0,
                    mouse[hour] if hour < len(mouse) else 0,
                    total[hour] if hour < len(total) else 0,
                    "",
                ])
        for app in data.get("applications", []):
            writer.writerow(["application", "", "", app["app"], "", app.get("path", ""), "", "", "", app["seconds"]])
        for item in data.get("interactions", []):
            ts = item.get("ts", "")
            writer.writerow([
                "interaction", ts[:10], ts[11:13], item.get("app", ""), "",
                item.get("path", ""), item.get("keyboard", 0), item.get("mouse", 0),
                item.get("responses", 0), "",
            ])
        for session in data.get("sessions", []):
            writer.writerow([
                "session", session["start"][:10], "", session["app"], session.get("title", ""),
                session.get("path", ""), "", "", "", session["seconds"],
            ])
        body = ("\ufeff" + out.getvalue()).encode("utf-8")
        name = f"interaction-rhythm-{data['range']['start']}-{data['range']['end']}.csv"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self):
        raw = urlparse(self.path).path.lstrip("/") or "index.html"
        if raw == "detail":
            raw = "detail.html"
        # favicon: 返回托盘图标 PNG，避免浏览器 404 噪音
        if raw == "favicon.ico":
            from tray import make_icon
            import io
            buf = io.BytesIO()
            make_icon(32).save(buf, "PNG")
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        fp = (STATIC_DIR / raw).resolve()
        if not str(fp).startswith(str(STATIC_DIR.resolve())):
            self.send_error(403)
            return
        if fp.is_file():
            # MIME 类型映射
            MIME_TYPES = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css",
                ".js": "application/javascript",
                ".json": "application/json",
                ".webmanifest": "application/manifest+json",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
                ".woff": "font/woff",
                ".woff2": "font/woff2",
                ".ttf": "font/ttf",
            }
            ct = MIME_TYPES.get(fp.suffix, "application/octet-stream")
            body = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if fp.suffix == ".html" and Handler.guard:
                self.send_header("Set-Cookie", Handler.guard.cookie_header())

            if fp.suffix in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf"):
                self.send_header("Cache-Control", "public, max-age=86400")

            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


class ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class StatsServer:
    def __init__(self, db, port: int = PORT, host: str = "127.0.0.1", shutdown_callback=None, shutdown_token: str = ""):
        Handler.db = db
        Handler.shutdown_callback = shutdown_callback
        Handler.shutdown_token = shutdown_token
        self.service = ApplicationService(db, shutdown_callback)
        Handler.service = self.service
        self._httpd = ReusableHTTPServer((host, port), Handler)
        self._port = int(self._httpd.server_address[1])
        Handler.guard = SessionGuard(host, self._port)

    @property
    def port(self) -> int:
        return self._port

    @property
    def threaded(self) -> bool:
        return isinstance(self._httpd, ThreadingHTTPServer)

    def start(self):
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def stop(self):
        try:
            if self.service.email_reports:
                self.service.email_reports.stop()
            self._httpd.shutdown()
        finally:
            self._httpd.server_close()

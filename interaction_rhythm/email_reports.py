"""Local email identity, explicit subscriptions and a durable SMTP delivery ledger.

This is a per-installation identity, not a hosted account or cloud sync service.
Only whitelisted daily aggregates leave the machine after explicit consent.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import secrets
import smtplib
import ssl
import threading
import time
from datetime import date, datetime, time as day_time, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from config import APP_NAME
from interaction_rhythm.metrics import METRIC_VERSION
from interaction_rhythm.protected_store import protect, unprotect


def email_address(value) -> str:
    value = str(value or "").strip()
    if len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value):
        raise ValueError("请输入有效邮箱地址")
    local, domain = value.rsplit("@", 1)
    return local + "@" + domain.lower()


def period_start(kind: str, day: date, anchor: int = 1) -> date:
    if kind == "weekly":
        anchor = int(anchor)
        if not 1 <= anchor <= 7:
            raise ValueError("周起始日须为周一至周日中的一天")
        return day - timedelta(days=(day.isoweekday() - anchor) % 7)
    if kind == "monthly":
        return day.replace(day=1)
    raise ValueError("报告周期须为 weekly 或 monthly")


def period_after(kind: str, start: date) -> date:
    if kind == "weekly":
        return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def latest_period(kind: str, today: date, anchor: int = 1) -> tuple[date, date]:
    end = period_start(kind, today, anchor) - timedelta(days=1)
    return period_start(kind, end, anchor), end


def build_report(db, kind: str, start: date, end: date) -> dict:
    """Keep the outbound schema independent of raw DB/API payloads and labels."""
    rows = []
    months = {}
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        metrics = db.day_metrics(key)
        month = key[:7]
        if month not in months:
            months[month] = {item["date"]: item for item in db.month_heatmap(month)["days"]}
        legacy = months[month][key]
        events = metrics["events"]
        split = bool(events["split_available"])
        coverage = metrics["coverage"]
        rows.append({
            "date": key,
            "keyboard": events["keyboard_down_count"] if split else int(legacy["keyboard"]),
            "mouse": (events["mouse_button_count"] + events["scroll_event_count"]) if split else int(legacy["mouse"]),
            "clicks": events["mouse_button_count"] if split else None,
            "scroll": events["scroll_event_count"] if split else None,
            "foreground_seconds": metrics["foreground_seconds"] if metrics["foreground_available"] else None,
            "coverage_status": coverage["status"],
            "expected_seconds": coverage["expected_seconds"],
            "healthy_seconds": coverage["healthy_seconds"],
            "unknown_seconds": coverage["unknown_seconds"],
            "first_observed_at": coverage["first_observed_at"],
            "last_observed_at": coverage["last_observed_at"],
        })
        cursor += timedelta(days=1)
    label = "周报" if kind == "weekly" else "月报"
    title = f"{APP_NAME} · {label} · {start} — {end}"
    total_keys = sum(row["keyboard"] for row in rows)
    total_mouse = sum(row["mouse"] for row in rows)
    observed = sum(row["expected_seconds"] > 0 for row in rows)
    lines = [title, f"指标版本：{METRIC_VERSION}；按本机日历统计。",
             f"记录到键盘事件 {total_keys:,} 次，鼠标事件 {total_mouse:,} 次。",
             f"{len(rows)} 个日历日中，{observed} 天保留可计算的采集覆盖窗口。",
             "覆盖仅针对软件可观察且应采集的窗口，不能代表全天完整记录。",
             "无覆盖记录不等于没有活动；旧数据无法拆分点击与滚轮时显示“—”。",
             "前台时长不等于注意、效率或工作时长。", ""]
    table = []
    headers = ["日期", "键盘事件", "鼠标事件", "点击", "滚轮", "前台时长", "健康/应采集窗口", "观察起止"]
    lines.append(" | ".join(headers))
    for row in rows:
        coverage_text = (f'{row["healthy_seconds"]:.0f}/{row["expected_seconds"]:.0f} 秒；未知 {row["unknown_seconds"]:.0f} 秒'
                         if row["expected_seconds"] else "暂无覆盖记录")
        window = (f'{row["first_observed_at"] or "—"} 至 {row["last_observed_at"] or "—"}')
        fg = "—" if row["foreground_seconds"] is None else f'{row["foreground_seconds"] / 60:.1f} 分钟'
        values = [row["date"], str(row["keyboard"]), str(row["mouse"]),
                  "—" if row["clicks"] is None else str(row["clicks"]),
                  "—" if row["scroll"] is None else str(row["scroll"]), fg, coverage_text, window]
        lines.append(" | ".join(values))
        table.append("<tr>" + "".join(f'<td style="overflow-wrap:anywhere">{html.escape(v)}</td>' for v in (values[0], values[1], values[2], values[5])) + "</tr>")
        detail = f"点击 {values[3]} / 滚轮 {values[4]}；覆盖 {coverage_text}；观察窗口 {window}"
        table.append(f'<tr><td colspan="4" style="font-size:12px;overflow-wrap:anywhere;color:#52645a">{html.escape(detail)}</td></tr>')
    body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font:14px/1.7 sans-serif;color:#24352b;margin:16px">'
    body += f"<h2>{html.escape(title)}</h2>" + "".join(f"<p>{html.escape(s)}</p>" for s in lines[1:7])
    body += '<table style="width:100%;border-collapse:collapse" border="1" cellpadding="6" cellspacing="0"><tr>' + "".join(f"<th>{s}</th>" for s in ("日期", "键盘事件", "鼠标事件", "前台时长")) + "</tr>" + "".join(table) + "</table>"
    body += "<p>邮件不包含应用名称、用户标签、本地路径、输入内容或原始事件。不调用云端 AI。</p></body></html>"
    lines += ["", "邮件不包含应用名称、用户标签、本地路径、输入内容或原始事件。不调用云端 AI。"]
    return {"kind": kind, "start": str(start), "end": str(end), "subject": title,
            "text": "\n".join(lines), "html": body, "daily": rows}


def smtp_send(config: dict, recipient: str, subject: str, text: str, body: str | None, message_id: str) -> None:
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = message_id
    message.set_content(text)
    if body:
        message.add_alternative(body, subtype="html")
    context = ssl.create_default_context()
    client = (smtplib.SMTP_SSL(config["host"], config["port"], timeout=20, context=context)
              if config["security"] == "ssl" else smtplib.SMTP(config["host"], config["port"], timeout=20))
    try:
        if config["security"] == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(config["username"], config["password"])
        if client.send_message(message, from_addr=config["sender"], to_addrs=[recipient]):
            raise ValueError("收件地址被拒绝")
    finally:
        # A QUIT failure after DATA acceptance must not turn success into a retry.
        client.close()


def send_email(config, recipient, subject, text, body, message_id):
    if config.get("transport", "smtp") == "agently":
        from interaction_rhythm.agently_transport import send
        return send(config, recipient, subject, text, body, message_id)
    return smtp_send(config, recipient, subject, text, body, message_id)


class EmailReports:
    def __init__(self, db, *, send=send_email, seal=protect, unseal=unprotect, clock=time.time):
        self.db, self.send, self.seal, self.unseal, self.clock = db, send, seal, unseal, clock
        self.path = db._path.parent / "email-reports.json"
        self.lock = threading.RLock()
        self.challenge = None
        self.previews = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.worker_error = ""
        self.state = {"smtp": {}, "account": {}, "subscriptions": {}, "deliveries": {}, "code_requests": [],
                      "preferences": {"week_start": 1}}
        self.storage_error = ""
        try:
            if self.path.exists():
                self.state.update(json.loads(unseal(json.loads(self.path.read_text(encoding="utf-8"))["protected"])))
        except (ValueError, KeyError, OSError):
            self.storage_error = "邮箱配置无法解密或已损坏，自动发送已停用。请重新保存发信配置并验证邮箱。"
        try:
            self.state["preferences"] = {"week_start": int(self.state.get("preferences", {}).get("week_start", 1))}
            period_start("weekly", date.today(), self.state["preferences"]["week_start"])
        except (TypeError, ValueError):
            self.state["preferences"] = {"week_start": 1}

    def _save(self):
        data = {"schema": 1, "protected": self.seal(json.dumps(self.state, ensure_ascii=False))}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(self.path)

    def _account(self):
        account = self.state["account"]
        return account if account.get("expires", 0) > self.clock() else {}

    def _require_account(self):
        account = self._account()
        if not account:
            raise ValueError("请先通过邮箱验证码登录")
        return account["email"]

    def _week_anchor(self) -> int:
        return int(self.state["preferences"].get("week_start", 1))

    def _apply_week_start(self, value):
        try:
            anchor = int(value)
        except (TypeError, ValueError):
            raise ValueError("周起始日须为周一至周日中的一天") from None
        if not 1 <= anchor <= 7:
            raise ValueError("周起始日须为周一至周日中的一天")
        if anchor == self._week_anchor():
            return
        self.state["preferences"]["week_start"] = anchor
        weekly = self.state["subscriptions"].get("weekly", {})
        if weekly.get("enabled"):
            # Re-anchor from the current week instead of back-filling overlapping windows.
            weekly["since"] = str(period_start("weekly", datetime.fromtimestamp(self.clock()).date(), anchor))

    def status(self):
        with self.lock:
            smtp = self.state["smtp"]
            email = self._account().get("email", "")
            history = [dict(value, id=key) for key, value in self.state["deliveries"].items()
                       if value["recipient"] == email]
            for item in history:
                if item["status"] == "sending":
                    item["status"] = "uncertain"
            transport = smtp.get("transport", "smtp")
            return {"ok": True, "logged_in": bool(email), "email": email,
                    "smtp": {key: smtp.get(key, "") for key in ("host", "port", "security", "sender", "username")},
                    "transport": transport,
                    "recipient_hint": smtp.get("recipient_hint", ""),
                    "configured": bool(smtp.get("sender")) if transport == "agently" else bool(smtp.get("password")),
                    "subscriptions": self.state["subscriptions"] if email else {},
                    "preferences": dict(self.state["preferences"]),
                    "deliveries": sorted(history, key=lambda x: x["at"], reverse=True)[:30],
                    "error": self.storage_error or self.worker_error,
                    "identity_scope": "local_windows_user"}

    def configure(self, body):
        with self.lock:
            transport = body.get("transport", "smtp")
            if transport not in ("smtp", "agently"):
                raise ValueError("请选择支持的发信方式")
            recipient_hint = email_address(body["recipient_hint"]) if body.get("recipient_hint") else ""
            if transport == "agently":
                from interaction_rhythm.agently_transport import check_connection
                sender = email_address(body.get("sender"))
                if not sender.lower().endswith("@agent.qq.com"):
                    raise ValueError("请输入 QQ Agent 邮箱地址")
                check_connection(sender)
                previous = self.state["smtp"]
                if previous.get("transport", "smtp") != transport or previous.get("sender") != sender:
                    self.state["subscriptions"] = {}
                self.state["smtp"] = {"transport": "agently", "sender": sender, "recipient_hint": recipient_hint}
                self.challenge = None
                self.previews.clear()
                self._save()
                self.storage_error = ""
                return self.status()
            host = str(body.get("host", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
                raise ValueError("请输入 SMTP 主机名，不含协议或路径")
            port = int(body.get("port", 465))
            security = body.get("security", "ssl")
            if not 1 <= port <= 65535 or security not in ("ssl", "starttls"):
                raise ValueError("请选择 SSL/TLS 或 STARTTLS 及有效端口")
            sender = email_address(body.get("sender"))
            username = str(body.get("username") or sender).strip()
            if len(username) > 254 or any(ord(c) < 32 for c in username):
                raise ValueError("SMTP 用户名无效")
            previous = self.state["smtp"]
            config = dict(host=host, port=port, security=security, sender=sender, username=username)
            password = str(body.get("password") or "")
            if not password and all(config[k] == previous.get(k) for k in config):
                password = previous.get("password", "")
            if not password or len(password) > 4096:
                raise ValueError("请输入邮箱服务提供的 SMTP 授权码或应用密码")
            if previous.get("transport", "smtp") != transport or previous.get("sender") != sender:
                self.state["subscriptions"] = {}
            self.state["smtp"] = dict(config, password=password, recipient_hint=recipient_hint)
            # Sender changes invalidate an outstanding code, never authorize a new recipient.
            self.challenge = None
            self.previews.clear()
            self._save()
            self.storage_error = ""
            return self.status()

    def request_code(self, body):
        with self.lock:
            if not self.state["smtp"]:
                raise ValueError("请先配置发信服务")
            email = email_address(body.get("email"))
            now = self.clock()
            recent = [t for t in self.state["code_requests"] if t > now - 86400]
            if recent and now - recent[-1] < 60:
                raise ValueError("请等待 60 秒后再获取验证码")
            if len(recent) >= 10:
                raise ValueError("本机 24 小时内验证码发送已达 10 次，请稍后再试")
            self.state["code_requests"] = recent + [now]
            self._save()
            code = f"{secrets.randbelow(1000000):06d}"
            self.challenge = None
            try:
                self.send(self.state["smtp"], email, f"{APP_NAME} · 邮箱登录验证码",
                          f"验证码：{code}\n10 分钟内有效，仅用于本机邮箱登录。若非本人操作，请忽略。", None, make_msgid())
            except (OSError, ValueError):
                raise ValueError("验证码发送失败，请检查所选发信连接、网络和邮箱授权") from None
            self.challenge = {"email": email, "hash": hashlib.sha256(code.encode()).digest(),
                              "expires": now + 600, "attempts": 0}
            return {"ok": True, "message": "验证码已提交邮箱服务，请检查收件箱及垃圾邮件", "retry_after": 60}

    def verify(self, body):
        with self.lock:
            c = self.challenge
            if not c or c["expires"] <= self.clock() or c["attempts"] >= 5:
                self.challenge = None
                raise ValueError("验证码已失效，请重新获取")
            c["attempts"] += 1
            code = str(body.get("code", ""))
            if email_address(body.get("email")) != c["email"] or not hmac.compare_digest(c["hash"], hashlib.sha256(code.encode()).digest()):
                raise ValueError("邮箱或验证码不正确")
            email = c["email"]
            if self.state["account"].get("email") != email:
                self.state["subscriptions"] = {}
            self.state["account"] = {"email": email, "expires": self.clock() + 90 * 86400}
            self.challenge = None
            self.previews.clear()
            self._save()
            return self.status()

    def logout(self, _body):
        with self.lock:
            self.state["account"] = {}
            self.state["subscriptions"] = {}
            self.challenge = None
            self.previews.clear()
            self._save()
            return self.status()

    def subscribe(self, body):
        with self.lock:
            self._require_account()
            if any(type(body.get(k)) is not bool for k in ("weekly", "monthly")):
                raise ValueError("请分别选择周报、月报开关")
            if (body["weekly"] or body["monthly"]) and body.get("consent") is not True:
                raise ValueError("请确认将日期、键鼠日总量、前台时长和采集覆盖发送至已验证邮箱")
            if "week_start" in body:
                self._apply_week_start(body["week_start"])
            today = datetime.fromtimestamp(self.clock()).date()
            for kind in ("weekly", "monthly"):
                old = self.state["subscriptions"].get(kind, {})
                self.state["subscriptions"][kind] = {
                    "enabled": body[kind],
                    "since": old["since"] if old.get("enabled") and body[kind] else str(period_start(kind, today, self._week_anchor())),
                }
            self._save()
            return self.status()

    def set_preferences(self, body):
        with self.lock:
            self._apply_week_start(body.get("week_start"))
            self._save()
            return self.status()

    def preview(self, body):
        with self.lock:
            email = self._require_account()
            kind = body.get("kind", "weekly")
            start, end = latest_period(kind, datetime.fromtimestamp(self.clock()).date(), self._week_anchor())
            report = build_report(self.db, kind, start, end)
            token = secrets.token_urlsafe(24)
            self.previews = {key: value for key, value in self.previews.items()
                             if value["expires"] > self.clock()}
            if len(self.previews) >= 8:
                self.previews.pop(next(iter(self.previews)))
            self.previews[token] = {"report": report, "email": email, "expires": self.clock() + 600}
            return {"ok": True, "report": report, "preview_token": token}

    def _deliver(self, kind, start, end, *, retry=False, report=None):
        email = self._require_account()
        key = hashlib.sha256(f"{email}|{kind}|{start}".encode()).hexdigest()
        previous = self.state["deliveries"].get(key)
        if previous and (not retry or previous["status"] == "sent"):
            return
        report = report or build_report(self.db, kind, start, end)
        entry = dict(recipient=email, kind=kind, start=str(start), end=str(end), at=self.clock(),
                     status="sending", message_id=make_msgid(), error="")
        self.state["deliveries"][key] = entry
        self._save()  # Reserve before network I/O; a crash is an uncertain outcome, never an automatic retry.
        try:
            self.send(self.state["smtp"], email, report["subject"], report["text"], report["html"], entry["message_id"])
            entry["status"] = "sent"
        except (OSError, ValueError):
            entry["status"] = "uncertain"
            entry["error"] = "投递未确认。请检查邮箱与发信配置，确认未收到后手动重试；重试可能产生重复邮件。"
        self._save()

    def send_latest(self, body):
        with self.lock:
            email = self._require_account()
            if body.get("consent") is not True:
                raise ValueError("请确认邮件数据范围后再发送")
            preview = self.previews.get(body.get("preview_token"))
            if not preview or preview["expires"] <= self.clock() or preview["email"] != email:
                raise ValueError("报告预览已失效，请重新预览后发送")
            report = preview["report"]
            self._deliver(report["kind"], date.fromisoformat(report["start"]),
                          date.fromisoformat(report["end"]), report=report)
            return self.status()

    def retry(self, body):
        with self.lock:
            email = self._require_account()
            item = self.state["deliveries"].get(body.get("id"))
            if not item or item["recipient"] != email or body.get("confirm_possible_duplicate") is not True:
                raise ValueError("请确认未收到原邮件及可能重复投递的风险")
            self._deliver(item["kind"], date.fromisoformat(item["start"]), date.fromisoformat(item["end"]), retry=True)
            return self.status()

    def process_due(self):
        with self.lock:
            if self.storage_error or not self._account() or not self.state["smtp"]:
                return
            now = datetime.fromtimestamp(self.clock())
            for kind, sub in self.state["subscriptions"].items():
                if not sub["enabled"]:
                    continue
                cursor = date.fromisoformat(sub["since"])
                # Bound work per tick to avoid a large mailbox burst after a long absence.
                delivered = 0
                while delivered < 2:
                    following = period_after(kind, cursor)
                    if datetime.combine(following, day_time(9)) > now or self.stop_event.is_set():
                        break
                    key = hashlib.sha256(f'{self._require_account()}|{kind}|{cursor}'.encode()).hexdigest()
                    if key not in self.state["deliveries"]:
                        self._deliver(kind, cursor, following - timedelta(days=1))
                        delivered += 1
                    cursor = following

    def start(self):
        if self.thread:
            return
        def worker():
            while not self.stop_event.wait(60):
                try:
                    self.process_due()
                    self.worker_error = ""
                except Exception:
                    # Surface a safe operational error without leaking SMTP responses or report contents.
                    self.worker_error = "邮件任务暂未完成，请检查配置或本地数据目录"
        self.thread = threading.Thread(target=worker, daemon=True, name="email-reports")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)

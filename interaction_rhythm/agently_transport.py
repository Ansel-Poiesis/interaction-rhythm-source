"""Pinned QQ Agent Mail adapter, isolated from the user's default CLI account.

The application uses --confirmed only for the user's explicit OTP command or
an already-consented subscription. Chat-driven sends retain their review flow.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

WORKSPACE = "digitalrhythm"
CLI_VERSION = "1.0.18"


def cli_path() -> Path:
    if getattr(sys, "frozen", False):
        path = Path(sys._MEIPASS) / "tools" / "agently-cli.exe"
    else:
        path = Path(__file__).resolve().parent.parent / "dist" / "tools" / f"agently-cli-{CLI_VERSION}" / "agently-cli.exe"
    if not path.is_file():
        raise ValueError("缺少 QQ Agent 邮箱组件，请更新数字节律")
    return path


def _run(arguments: list[str], *, cwd: Path | None = None) -> dict:
    env = dict(os.environ)
    # Never accept a default-workspace or environment-token fallback.
    env.pop("AGENTLY_ACCESS_TOKEN", None)
    env.pop("AGENTLY_CLI_CONFIG_DIR", None)
    env["AGENTLY_WORKSPACE"] = WORKSPACE
    env["AGENTLY_DISABLE_TELEMETRY"] = "1"
    env["AGENTLY_CLI_NO_UPDATE_NOTIFIER"] = "1"
    try:
        result = subprocess.run([str(cli_path()), *arguments], cwd=cwd, env=env,
                                capture_output=True, encoding="utf-8", errors="replace",
                                timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("QQ Agent 邮箱连接未完成，请检查网络与独立工作区授权") from None
    if result.returncode:
        if result.returncode == 3:
            raise ValueError("数字节律 Agent 邮箱授权已失效，请重新连接")
        if result.returncode == 7:
            raise ValueError("QQ Agent 邮箱请求过于频繁，请稍后再试")
        raise ValueError("QQ Agent 邮箱操作失败，请检查连接与收件地址")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError("QQ Agent 邮箱返回格式无效，尚未确认发送结果") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("QQ Agent 邮箱未确认操作成功")
    return payload


def check_connection(sender: str) -> None:
    payload = _run(["+me"])
    aliases = payload.get("data", {}).get("aliases", [])
    # CLI 1.0.18 has no --from flag. Only its primary alias can be used.
    primary = [item.get("email", "").lower() for item in aliases if item.get("is_primary") is True]
    if primary != [sender.lower()]:
        raise ValueError("当前 Agent 授权的发件地址不匹配，请连接所选邮箱；没有使用其他邮箱代发")
    if "mail:send" not in payload.get("data", {}).get("scopes", []):
        raise ValueError("当前 Agent 邮箱授权不包含发信权限")


def send(config: dict, recipient: str, subject: str, text: str, body: str | None, message_id: str) -> None:
    check_connection(config["sender"])
    arguments = ["message", "+send", "--to", recipient, "--subject", subject, "--confirmed"]
    if body is None:
        # OTP stays in transient process memory/arguments, never in a body file.
        payload = _run(arguments + ["--body", text, "--body-format", "plain"])
    else:
        # Avoid Windows' command-line length limit for monthly HTML reports.
        folder = Path(tempfile.mkdtemp(prefix="DigitalRhythm-mail-"))
        message = folder / "report.html"
        try:
            message.write_text(body, encoding="utf-8")
            payload = _run(arguments + ["--body-file", "./report.html", "--body-format", "html"], cwd=folder)
        finally:
            message.unlink(missing_ok=True)
            folder.rmdir()
    data = payload.get("data", {})
    if data.get("confirmation_required") or not (payload.get("queued") is True or data.get("queued") is True):
        raise ValueError("QQ Agent 邮箱未确认邮件进入发送队列，请核对后再决定是否重试")

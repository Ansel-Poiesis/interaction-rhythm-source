"""Start a packaged build on an isolated port and verify its local API."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path


def wait_for_api(port: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/today"
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8-sig"))
        except Exception as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"packaged runtime did not become ready: {last_error}")


def port_is_released(port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            if client.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    parser.add_argument("--port", type=int, default=19031)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    exe = args.exe.resolve()
    if not exe.is_file():
        raise FileNotFoundError(exe)

    candidate_db = exe.parent / "data" / "tracker.db"
    if candidate_db.exists():
        raise RuntimeError(f"candidate contains a user database: {candidate_db}")

    with tempfile.TemporaryDirectory(prefix="interaction-rhythm-smoke-") as temp_data:
        env = dict(os.environ)
        env["INTERACTION_RHYTHM_PORT"] = str(args.port)
        env["INTERACTION_RHYTHM_DATA_DIR"] = str(Path(temp_data).resolve())
        env["INTERACTION_RHYTHM_MUTEX_NAME"] = (
            rf"Local\Kouxian.DigitalRhythm.Validation.{uuid.uuid4().hex}"
        )
        process = subprocess.Popen(
            [str(exe), "--background"],
            cwd=exe.parent,
            env=env,
        )
        try:
            payload = wait_for_api(args.port, args.timeout)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "pid": process.pid,
                        "port": args.port,
                        "date": payload.get("date"),
                        "has_coverage": "coverage" in payload,
                        "isolated_data": True,
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    if candidate_db.exists():
        raise RuntimeError(f"packaged smoke polluted the candidate data directory: {candidate_db}")

    if not port_is_released(args.port):
        raise RuntimeError(f"validation port {args.port} was not released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

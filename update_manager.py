"""扣舷-数字节律一键更新管理。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config
from diagnostics import log_event, log_exception
from interaction_rhythm.runtime_paths import resolve_data_dir


class UpdateError(RuntimeError):
    pass


@dataclass
class UpdatePackage:
    manifest: dict[str, Any]
    zip_path: Path
    staging_dir: Path


_CACHE_MAX_AGE_SECONDS = 60 * 60
_CHECK_LOCK = threading.Lock()
_CHECKING = False
_LEGACY_VERSION_EPOCH = 1


def app_root() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def data_dir() -> Path:
    return resolve_data_dir(app_root())


def updates_dir() -> Path:
    p = data_dir() / "updates"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cache_path() -> Path:
    return updates_dir() / "update-cache.json"


def _read_update_cache() -> dict[str, Any] | None:
    path = _cache_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None
    return None


def _write_update_cache(raw: dict[str, Any]) -> None:
    try:
        _cache_path().write_text(
            json.dumps({"checked_at": time.time(), "raw": raw}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for chunk in str(value).strip().lstrip("v").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num or 0))
    return tuple(parts or [0])


def _parse_version_epoch(value: Any, field_name: str = "version_epoch") -> int:
    text = str(value).strip()
    if not text.isdigit() or int(text) < _LEGACY_VERSION_EPOCH:
        raise UpdateError(f"更新清单里的 {field_name} 必须是大于等于 1 的整数")
    return int(text)


def _current_version_epoch() -> int:
    return _parse_version_epoch(
        getattr(config, "APP_VERSION_EPOCH", _LEGACY_VERSION_EPOCH),
        "APP_VERSION_EPOCH",
    )


def _version_key(version: str, version_epoch: int) -> tuple[int, tuple[int, ...]]:
    return version_epoch, _version_tuple(version)


def _is_newer_version(
    latest: str,
    latest_epoch: int,
    current: str | None = None,
    current_epoch: int | None = None,
) -> bool:
    current = current or config.APP_VERSION
    current_epoch = current_epoch if current_epoch is not None else _current_version_epoch()
    return _version_key(latest, latest_epoch) > _version_key(current, current_epoch)


def _http_json(url: str) -> dict[str, Any]:
    if not url:
        raise UpdateError("还没有配置更新清单地址")
    req = urllib.request.Request(url, headers={"User-Agent": f"InteractionRhythm/{config.APP_VERSION}"})
    log_event("update_manifest_request", {"url": url})
    with urllib.request.urlopen(req, timeout=config.UPDATE_TIMEOUT) as resp:
        status = getattr(resp, "status", None) or resp.getcode() or 200
        if status >= 400:
            raise UpdateError(f"更新清单读取失败：HTTP {status}")
        return json.loads(resp.read().decode("utf-8-sig"))


def _manifest_url() -> str:
    for path in (data_dir() / "update-url.txt", app_root() / "update-url.txt"):
        if path.is_file():
            url = path.read_text(encoding="utf-8-sig").strip()
            if url:
                return url
    return config.UPDATE_MANIFEST_URL


def _unconfigured_manifest() -> dict[str, Any]:
    current_epoch = _current_version_epoch()
    return {
        "app": config.APP_NAME,
        "channel": config.UPDATE_CHANNEL,
        "latest": config.APP_VERSION,
        "current": config.APP_VERSION,
        "version_epoch": current_epoch,
        "current_version_epoch": current_epoch,
        "has_update": False,
        "download_url": "",
        "sha256": "",
        "size": 0,
        "notes": ["当前测试版还没有配置更新清单地址。"],
        "published_at": "",
        "update_configured": False,
        "data_schema_min": 1,
        "data_schema_target": getattr(config, "DATA_SCHEMA_VERSION", 1),
        "data_migration_required": False,
    }


def _disabled_update_manifest() -> dict[str, Any]:
    """Expose the disabled update state for source builds."""
    manifest = _unconfigured_manifest()
    manifest["notes"] = ["当前源码构建未启用应用内更新。"]
    manifest["update_policy"] = "disabled_source"
    return manifest


def _update_checks_enabled() -> bool:
    return bool(getattr(config, "UPDATE_CHECK_ENABLED", False))


def _normalize_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    latest = str(raw.get("latest") or raw.get("version") or "").strip().lstrip("v")
    latest_epoch = _parse_version_epoch(raw.get("version_epoch", _LEGACY_VERSION_EPOCH))
    current_epoch = _current_version_epoch()
    download_url = str(raw.get("download_url") or raw.get("url") or "").strip()
    sha256 = str(raw.get("sha256") or "").strip().lower()
    current_schema = int(getattr(config, "DATA_SCHEMA_VERSION", 1))
    data_schema_min = int(raw.get("data_schema_min") or 1)
    data_schema_target = int(raw.get("data_schema_target") or current_schema)
    if data_schema_min > current_schema:
        raise UpdateError("更新需要更高的数据版本，当前客户端无法安全迁移")
    if not latest:
        raise UpdateError("更新清单缺少 latest/version")
    if not download_url:
        raise UpdateError("更新清单缺少 download_url")
    if sha256 and (len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256)):
        raise UpdateError("更新清单里的 sha256 格式不正确")
    notes = raw.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    return {
        "app": raw.get("app") or config.APP_NAME,
        "channel": raw.get("channel") or config.UPDATE_CHANNEL,
        "latest": latest,
        "current": config.APP_VERSION,
        "version_epoch": latest_epoch,
        "current_version_epoch": current_epoch,
        "has_update": _is_newer_version(latest, latest_epoch),
        "download_url": download_url,
        "sha256": sha256,
        "size": int(raw.get("size") or 0),
        "notes": [str(item) for item in notes],
        "published_at": raw.get("published_at") or "",
        "update_configured": True,
        "data_schema_min": data_schema_min,
        "data_schema_target": data_schema_target,
        "data_migration_required": data_schema_target != current_schema,
    }


def check_update() -> dict[str, Any]:
    if not _update_checks_enabled():
        return {
            "ok": True,
            "manifest": _disabled_update_manifest(),
            "cached": False,
            "offline": False,
            "checking": False,
        }
    url = _manifest_url()
    if not url:
        return {"ok": True, "manifest": _unconfigured_manifest(), "cached": False, "offline": False, "checking": False}
    try:
        raw = _http_json(url)
        manifest = _normalize_manifest(raw)
        _write_update_cache(raw)
        log_event("update_manifest_checked", {
            "latest": manifest.get("latest"),
            "current": manifest.get("current"),
            "version_epoch": manifest.get("version_epoch"),
            "current_version_epoch": manifest.get("current_version_epoch"),
            "has_update": manifest.get("has_update"),
        })
        return {"ok": True, "manifest": manifest, "cached": False, "offline": False, "checking": False, "checked_at": time.time()}
    except Exception as exc:
        log_exception("update_manifest_failed", exc, {"url": url})
        raise


def _manifest_from_cache(cache: dict[str, Any] | None) -> tuple[dict[str, Any] | None, float]:
    if not cache:
        return None, 0
    raw = cache.get("raw")
    if not isinstance(raw, dict):
        raw = cache.get("manifest")
    if not isinstance(raw, dict):
        return None, 0
    try:
        checked_at = float(cache.get("checked_at") or 0)
        manifest = _normalize_manifest(raw)
        if _version_key(manifest["latest"], manifest["version_epoch"]) < _version_key(
            config.APP_VERSION,
            _current_version_epoch(),
        ):
            return None, 0
        return manifest, checked_at
    except Exception:
        return None, 0


def _background_check_update() -> None:
    global _CHECKING
    try:
        check_update()
    except Exception as exc:
        log_exception("update_background_check_failed", exc)
        pass
    finally:
        with _CHECK_LOCK:
            _CHECKING = False


def start_update_check_async() -> bool:
    global _CHECKING
    if not _update_checks_enabled():
        return False
    if not _manifest_url():
        return False
    with _CHECK_LOCK:
        if _CHECKING:
            return True
        _CHECKING = True
    threading.Thread(target=_background_check_update, name="UpdateCheck", daemon=True).start()
    return True


def update_status(max_age_seconds: int = _CACHE_MAX_AGE_SECONDS) -> dict[str, Any]:
    if not _update_checks_enabled():
        return {
            "ok": True,
            "manifest": _disabled_update_manifest(),
            "cached": False,
            "offline": False,
            "checking": False,
        }
    if not _manifest_url():
        return {"ok": True, "manifest": _unconfigured_manifest(), "cached": False, "checking": False}

    manifest, checked_at = _manifest_from_cache(_read_update_cache())
    fresh = bool(manifest and checked_at and (time.time() - checked_at) < max_age_seconds)
    if fresh:
        return {"ok": True, "manifest": manifest, "cached": True, "offline": False, "checking": False, "checked_at": checked_at}

    checking = start_update_check_async()
    if manifest:
        return {"ok": True, "manifest": manifest, "cached": True, "offline": True, "checking": checking, "checked_at": checked_at}

    pending = {
        "app": config.APP_NAME,
        "channel": config.UPDATE_CHANNEL,
        "latest": config.APP_VERSION,
        "current": config.APP_VERSION,
        "version_epoch": _current_version_epoch(),
        "current_version_epoch": _current_version_epoch(),
        "has_update": False,
        "download_url": "",
        "sha256": "",
        "size": 0,
        "notes": ["正在后台检查更新。"],
        "published_at": "",
        "update_configured": True,
    }
    return {"ok": True, "manifest": pending, "cached": False, "offline": True, "checking": checking, "checked_at": 0}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, target: Path) -> None:
    tmp = target.with_suffix(target.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    req = urllib.request.Request(url, headers={"User-Agent": f"InteractionRhythm/{config.APP_VERSION}"})
    log_event("update_download_started", {"url": url, "target": target})
    with urllib.request.urlopen(req, timeout=config.UPDATE_TIMEOUT) as resp, tmp.open("wb") as f:
        status = getattr(resp, "status", None) or resp.getcode() or 200
        if status >= 400:
            raise UpdateError(f"更新包下载失败：HTTP {status}")
        shutil.copyfileobj(resp, f)
    tmp.replace(target)
    log_event("update_download_finished", {"target": target, "size": target.stat().st_size})


def _prepare_staging(manifest: dict[str, Any]) -> UpdatePackage:
    root = updates_dir()
    version = manifest["latest"]
    zip_path = root / f"InteractionRhythm-v{version}.zip"
    staging_dir = root / f"staging-v{version}"

    try:
        _download(manifest["download_url"], zip_path)
        expected_size = int(manifest.get("size") or 0)
        actual_size = zip_path.stat().st_size
        if expected_size and actual_size != expected_size:
            raise UpdateError("更新包校验失败，文件大小不匹配")
        expected = manifest.get("sha256") or ""
        actual = _sha256(zip_path).lower()
        if expected and actual != expected:
            raise UpdateError("更新包校验失败，sha256 不匹配")
        log_event("update_package_verified", {"version": version, "sha256": actual})

        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            base = staging_dir.resolve()
            for member in zf.infolist():
                target = (staging_dir / member.filename).resolve()
                try:
                    target.relative_to(base)
                except ValueError:
                    raise UpdateError("更新包包含不安全路径")
            zf.extractall(staging_dir)
    except zipfile.BadZipFile as exc:
        log_exception("update_package_bad_zip", exc, {"version": version, "zip_path": zip_path})
        raise UpdateError("更新包不是有效 zip 文件") from exc
    except Exception as exc:
        log_exception("update_prepare_staging_failed", exc, {"version": version, "zip_path": zip_path})
        raise

    if not _find_new_exe(staging_dir):
        log_event("update_package_missing_exe", {"version": version, "staging_dir": staging_dir})
        raise UpdateError("更新包里没有找到 InteractionRhythm.exe")
    return UpdatePackage(manifest=manifest, zip_path=zip_path, staging_dir=staging_dir)


def _find_new_exe(staging_dir: Path) -> Path | None:
    direct = staging_dir / "InteractionRhythm.exe"
    if direct.is_file():
        return direct
    matches = list(staging_dir.glob("**/InteractionRhythm.exe"))
    return matches[0] if matches else None


def _write_updater(package: UpdatePackage) -> Path:
    script_path = updates_dir() / "run-update.ps1"
    root = app_root()
    exe = root / "InteractionRhythm.exe"
    new_exe = _find_new_exe(package.staging_dir)
    if not new_exe:
        raise UpdateError("更新包里没有找到 InteractionRhythm.exe")
    source_root = new_exe.parent

    # 使用持久备份目录，便于失败后手动恢复。
    from interaction_rhythm.data_backup import DataBackupManager
    backup_manager = DataBackupManager(app_version=config.APP_VERSION)
    try:
        backup_info = backup_manager.create_update_backup()
        backup = backup_info
    except Exception as exc:
        log_exception("update_backup_create_failed", exc)
        # 回退到临时目录，避免阻塞更新。
        backup = Path(tempfile.gettempdir()) / f"InteractionRhythm-backup-{config.APP_VERSION}-{int(time.time())}"

    script = f"""$ErrorActionPreference = "Stop"
$Target = {json.dumps(str(root), ensure_ascii=False)}
$Source = {json.dumps(str(source_root), ensure_ascii=False)}
$Backup = {json.dumps(str(backup), ensure_ascii=False)}
$BackupRoot = {json.dumps(str(backup_manager.backup_root), ensure_ascii=False)}
$Exe = {json.dumps(str(exe), ensure_ascii=False)}
$Log = Join-Path {json.dumps(str(updates_dir()), ensure_ascii=False)} "last-update.log"
$State = Join-Path {json.dumps(str(updates_dir()), ensure_ascii=False)} "last-update-state.json"
function Log($m) {{ Add-Content -LiteralPath $Log -Value ("[{0}] {{1}}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8 }}
function State($s, $m) {{
  [ordered]@{{ ts = (Get-Date -Format "yyyy-MM-dd HH:mm:ss"); state = $s; message = $m; target = $Target; source = $Source; backup = $Backup }} |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath $State -Encoding UTF8
}}
try {{
  State "started" "update started"
  Log "update started"
  Start-Sleep -Milliseconds 1200
  $ProcessNames = @("InteractionRhythm", "TypeTracker")
  $deadline = (Get-Date).AddSeconds(20)
  while ((Get-Process -Name $ProcessNames -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 500
  }}
  Get-Process -Name $ProcessNames -ErrorAction SilentlyContinue | Stop-Process -Force

  if (-not (Test-Path $Backup)) {{ New-Item -Path $Backup -ItemType Directory -Force | Out-Null }}
  $Data = Join-Path $Target "data"
  $ProgramBackup = Join-Path $Backup "program-preserved"
  if (Test-Path $ProgramBackup) {{ Remove-Item -LiteralPath $ProgramBackup -Recurse -Force }}
  New-Item -Path $ProgramBackup -ItemType Directory -Force | Out-Null
  Get-ChildItem -LiteralPath $Target -Force | Where-Object {{ $_.Name -ne "data" }} | ForEach-Object {{
    Move-Item -LiteralPath $_.FullName -Destination $ProgramBackup -Force
  }}
  State "program_backed_up" "program files moved to backup"

  Get-ChildItem -LiteralPath $Source -Force | Where-Object {{ $_.Name -ne "data" }} | ForEach-Object {{
    Copy-Item -LiteralPath $_.FullName -Destination $Target -Recurse -Force
  }}
  State "program_copied" "new program files copied"
  if (-not (Test-Path $Data)) {{ New-Item -Path $Data -ItemType Directory -Force | Out-Null }}

  # 数据文件已由 Python DataBackupManager 在 $Backup 根目录保存；直接复制回 data。
  if (Test-Path $Backup) {{
    Get-ChildItem -LiteralPath $Backup -Force | Where-Object {{ ($_.Name -ne "program-preserved") -and ($_.Name -ne "backup-meta.json") }} | ForEach-Object {{
      $targetItem = Join-Path $Data $_.Name
      if ($_.PSIsContainer) {{
        if (Test-Path $targetItem) {{ Remove-Item -LiteralPath $targetItem -Recurse -Force }}
        Copy-Item -LiteralPath $_.FullName -Destination $Data -Recurse -Force
      }} else {{
        Copy-Item -LiteralPath $_.FullName -Destination $Data -Force
      }}
    }}
  }}
  State "data_restored" "user data restored from persistent backup"

  $SourceData = Join-Path $Source "data"
  if (Test-Path $SourceData) {{
    foreach ($name in @("update-url.txt","扣舷-数字节律.ico","交互节律.ico","叩舷.ico","扣舷.ico","interaction-rhythm-title.png")) {{
      $sourceItem = Join-Path $SourceData $name
      if (Test-Path $sourceItem) {{
        Copy-Item -LiteralPath $sourceItem -Destination $Data -Recurse -Force
      }}
    }}
  }}

  Start-Process -FilePath $Exe -WorkingDirectory $Target
  State "finished" "update finished"
  Log "update finished"
  # 不再删除持久备份；由保留策略定期清理。
}} catch {{
  Log ("update failed: " + $_.Exception.Message)
  State "failed" ($_.Exception.Message)
  try {{
    if (Test-Path $Backup) {{
      State "rollback_started" "cleaning partial update before restore"
      Get-ChildItem -LiteralPath $Target -Force | Where-Object {{ $_.Name -ne "data" }} | Remove-Item -Recurse -Force
      if (Test-Path $ProgramBackup) {{
        Get-ChildItem -LiteralPath $ProgramBackup -Force | ForEach-Object {{
          Copy-Item -LiteralPath $_.FullName -Destination $Target -Recurse -Force
        }}
      }}
      New-Item -Path (Join-Path $Target "data") -ItemType Directory -Force | Out-Null
      Get-ChildItem -LiteralPath $Backup -Force | Where-Object {{ ($_.Name -ne "program-preserved") -and ($_.Name -ne "backup-meta.json") }} | ForEach-Object {{
        $targetItem = Join-Path $Data $_.Name
        if ($_.PSIsContainer) {{
          if (Test-Path $targetItem) {{ Remove-Item -LiteralPath $targetItem -Recurse -Force }}
          Copy-Item -LiteralPath $_.FullName -Destination $Data -Recurse -Force
        }} else {{
          Copy-Item -LiteralPath $_.FullName -Destination $Data -Force
        }}
      }}
      Start-Process -FilePath $Exe -WorkingDirectory $Target
      State "rolled_back" "previous version restored"
    }}
  }} catch {{}}
}}
"""
    script_path.write_text(script, encoding="utf-8")
    return script_path


def install_update_async(manifest: dict[str, Any], shutdown_callback) -> dict[str, Any]:
    if not _update_checks_enabled():
        raise UpdateError("当前源码构建未启用应用内更新")
    if not getattr(sys, "frozen", False):
        raise UpdateError("源码运行模式不执行自我更新，请打包后测试")
    if not callable(shutdown_callback):
        raise UpdateError("更新退出回调未配置")
    manifest = _normalize_manifest(manifest)
    if not manifest["has_update"]:
        return {"ok": True, "message": "已经是最新版本"}

    try:
        log_event("update_install_requested", {"latest": manifest.get("latest")})
        package = _prepare_staging(manifest)
        script = _write_updater(package)
        subprocess.Popen([
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ], cwd=str(app_root()), close_fds=True)
        log_event("update_updater_launched", {"script": script, "latest": manifest.get("latest")})
    except Exception as exc:
        log_exception("update_install_prepare_failed", exc, {"latest": manifest.get("latest")})
        raise

    def _shutdown():
        time.sleep(0.4)
        shutdown_callback()

    threading.Thread(target=_shutdown, daemon=True).start()
    return {"ok": True, "message": "更新包已下载，应用即将关闭并自动重启"}

"""数据备份与恢复管理。

提供版本化的本地数据快照，支持：
- 手动创建快照（设置页/API）
- 列出、恢复、删除快照
- 更新/安装失败时从持久备份回滚
- 安装器升级前自动快照

备份目录：`<data_dir>/backups/<timestamp>-v<version>/`
保留策略：默认保留最近 10 个自动/更新备份 + 不限数量手动备份。
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from interaction_rhythm.runtime_paths import resolve_data_dir


# 默认需要备份的数据文件/目录。
# 注意：不包含 diagnostics 日志、updates 缓存和临时构建产物。
DEFAULT_BACKUP_PATTERNS = (
    "tracker.db",
    "tracker.db-wal",
    "tracker.db-shm",
    "settings.json",
    "app_labels.json",
    "icons",
)

MAX_AUTO_BACKUPS = 10


class DataBackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupInfo:
    id: str
    name: str
    created_at: float
    created_at_iso: str
    version: str
    size: int
    path: Path
    source: str
    is_auto: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "created_at_iso": self.created_at_iso,
            "version": self.version,
            "size": self.size,
            "path": str(self.path),
            "source": self.source,
            "is_auto": self.is_auto,
        }


def _data_dir(app_root: Path | None = None) -> Path:
    if app_root is None:
        import sys

        app_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
    return resolve_data_dir(app_root)


def _backup_root(data_dir: Path) -> Path:
    root = data_dir / "backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _backup_id(timestamp: float, version: str, source: str) -> str:
    dt = datetime.fromtimestamp(timestamp)
    return f"{dt.strftime('%Y%m%d-%H%M%S')}-{dt.microsecond // 1000:03d}-v{version}-{source}"


def _dir_size(path: Path) -> int:
    total = 0
    if path.is_file():
        return path.stat().st_size
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _copy_item(src: Path, dst: Path) -> None:
    if src.is_file():
        shutil.copy2(src, dst)
    elif src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _backup_items(data_dir: Path) -> list[str]:
    items = []
    for name in DEFAULT_BACKUP_PATTERNS:
        path = data_dir / name
        if path.exists():
            items.append(name)
    return items


class DataBackupManager:
    """线程安全的数据备份管理器。"""

    def __init__(
        self,
        data_dir: Path | None = None,
        app_version: str = "0.0.0",
        pause_callback: Callable[[], None] | None = None,
        resume_callback: Callable[[], None] | None = None,
    ):
        self._data_dir = data_dir or _data_dir()
        self._version = str(app_version).strip() or "0.0.0"
        self._pause_callback = pause_callback
        self._resume_callback = resume_callback
        self._lock = threading.RLock()

    @property
    def backup_root(self) -> Path:
        return _backup_root(self._data_dir)

    def list_backups(self) -> list[BackupInfo]:
        backups: list[BackupInfo] = []
        root = self.backup_root
        if not root.exists():
            return backups
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            meta_path = entry / "backup-meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
            else:
                meta = {}
            backup_id = entry.name
            created_at = meta.get("created_at") or entry.stat().st_ctime
            version = meta.get("version") or "unknown"
            source = meta.get("source") or "unknown"
            is_auto = bool(meta.get("is_auto", False))
            name = meta.get("name") or backup_id
            backups.append(
                BackupInfo(
                    id=backup_id,
                    name=name,
                    created_at=float(created_at),
                    created_at_iso=datetime.fromtimestamp(float(created_at)).isoformat(timespec="seconds"),
                    version=version,
                    size=_dir_size(entry),
                    path=entry,
                    source=source,
                    is_auto=is_auto,
                )
            )
        return sorted(backups, key=lambda b: b.created_at, reverse=True)

    def create_backup(
        self,
        name: str = "",
        source: str = "manual",
        is_auto: bool = False,
    ) -> BackupInfo:
        """创建一份数据快照。"""
        with self._lock:
            timestamp = time.time()
            backup_id = _backup_id(timestamp, self._version, source)
            backup_dir = self.backup_root / backup_id
            if backup_dir.exists():
                raise DataBackupError(f"备份目录已存在: {backup_id}")

            if self._pause_callback:
                try:
                    self._pause_callback()
                except Exception as exc:
                    raise DataBackupError(f"暂停采集失败: {exc}") from exc

            try:
                backup_dir.mkdir(parents=True, exist_ok=False)
                items = _backup_items(self._data_dir)
                for item in items:
                    src = self._data_dir / item
                    dst = backup_dir / item
                    _copy_item(src, dst)

                meta = {
                    "id": backup_id,
                    "name": str(name or backup_id),
                    "created_at": timestamp,
                    "version": self._version,
                    "source": source,
                    "is_auto": is_auto,
                    "items": items,
                }
                (backup_dir / "backup-meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                try:
                    shutil.rmtree(backup_dir, ignore_errors=True)
                except Exception:
                    pass
                raise DataBackupError(f"创建备份失败: {exc}") from exc
            finally:
                if self._resume_callback:
                    try:
                        self._resume_callback()
                    except Exception:
                        pass

            if is_auto:
                self._prune_auto_backups()

            return BackupInfo(
                id=backup_id,
                name=meta["name"],
                created_at=timestamp,
                created_at_iso=datetime.fromtimestamp(timestamp).isoformat(timespec="seconds"),
                version=self._version,
                size=_dir_size(backup_dir),
                path=backup_dir,
                source=source,
                is_auto=is_auto,
            )

    def _prune_auto_backups(self) -> None:
        """自动备份保留最近 N 个。"""
        auto = [b for b in self.list_backups() if b.is_auto]
        if len(auto) <= MAX_AUTO_BACKUPS:
            return
        for old in auto[MAX_AUTO_BACKUPS:]:
            try:
                shutil.rmtree(old.path, ignore_errors=True)
            except Exception:
                pass

    def restore_backup(self, backup_id: str) -> BackupInfo:
        """恢复快照到 data_dir。恢复前会先对当前数据做一次当前状态快照。"""
        with self._lock:
            target = self.backup_root / backup_id
            if not target.exists() or not target.is_dir():
                raise DataBackupError(f"备份不存在: {backup_id}")

            meta_path = target / "backup-meta.json"
            if not meta_path.exists():
                raise DataBackupError(f"备份缺少元数据: {backup_id}")

            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise DataBackupError(f"读取备份元数据失败: {exc}") from exc

            # 先对当前数据做一次"恢复前"快照，便于回退。
            pre_restore = None
            try:
                pre_restore = self.create_backup(
                    name=f"pre-restore-{backup_id}",
                    source="pre-restore",
                    is_auto=True,
                )
            except Exception as exc:
                raise DataBackupError(f"恢复前快照失败，取消恢复: {exc}") from exc

            if self._pause_callback:
                try:
                    self._pause_callback()
                except Exception as exc:
                    raise DataBackupError(f"暂停采集失败: {exc}") from exc

            try:
                items = meta.get("items") or _backup_items(target)
                for item in items:
                    src = target / item
                    dst = self._data_dir / item
                    if not src.exists():
                        continue
                    # 删除当前项，再复制备份项。
                    if dst.is_file() or dst.is_dir():
                        if dst.is_dir():
                            shutil.rmtree(dst, ignore_errors=True)
                        else:
                            dst.unlink(missing_ok=True)
                    _copy_item(src, dst)
            except Exception as exc:
                # 尝试从恢复前快照回退。
                try:
                    if pre_restore:
                        for item in pre_restore.path.iterdir():
                            if item.name == "backup-meta.json":
                                continue
                            dst = self._data_dir / item.name
                            if item.is_file() or item.is_dir():
                                if dst.is_dir():
                                    shutil.rmtree(dst, ignore_errors=True)
                                elif dst.exists():
                                    dst.unlink(missing_ok=True)
                                _copy_item(item, dst)
                except Exception:
                    pass
                raise DataBackupError(f"恢复备份失败并已回退: {exc}") from exc
            finally:
                if self._resume_callback:
                    try:
                        self._resume_callback()
                    except Exception:
                        pass

            # 返回被恢复的备份信息。
            return BackupInfo(
                id=backup_id,
                name=meta.get("name", backup_id),
                created_at=meta.get("created_at", 0),
                created_at_iso=datetime.fromtimestamp(meta.get("created_at", 0)).isoformat(timespec="seconds"),
                version=meta.get("version", "unknown"),
                size=_dir_size(target),
                path=target,
                source=meta.get("source", "unknown"),
                is_auto=bool(meta.get("is_auto", False)),
            )

    def delete_backup(self, backup_id: str) -> dict[str, Any]:
        """删除指定备份。"""
        with self._lock:
            target = self.backup_root / backup_id
            if not target.exists() or not target.is_dir():
                raise DataBackupError(f"备份不存在: {backup_id}")
            shutil.rmtree(target, ignore_errors=True)
            return {"ok": True, "deleted": backup_id}

    def create_update_backup(self) -> Path:
        """供 update_manager.py 调用：创建一次自动更新备份，返回备份目录。"""
        info = self.create_backup(
            name=f"auto-update-{self._version}",
            source="update",
            is_auto=True,
        )
        return info.path


def backup_root_for(app_root: Path) -> Path:
    """返回给定应用根目录对应的备份根目录。"""
    return _backup_root(resolve_data_dir(app_root))


def create_backup_at(
    app_root: Path,
    app_version: str = "0.0.0",
    source: str = "manual",
    is_auto: bool = False,
) -> Path:
    """无状态便捷函数：在指定 app_root 创建备份并返回目录。"""
    data_dir = resolve_data_dir(app_root)
    manager = DataBackupManager(
        data_dir=data_dir,
        app_version=app_version,
    )
    return manager.create_backup(source=source, is_auto=is_auto).path

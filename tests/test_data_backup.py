"""数据备份与恢复管理测试。"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase

from interaction_rhythm.data_backup import DataBackupManager, DataBackupError


class TestDataBackupManager(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ir-backup-test-"))
        self.data_dir = self.tmp / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "tracker.db").write_text("db content", encoding="utf-8")
        (self.data_dir / "settings.json").write_text('{"theme": "light"}', encoding="utf-8")
        icons = self.data_dir / "icons"
        icons.mkdir(parents=True, exist_ok=True)
        (icons / "app.ico").write_text("icon", encoding="utf-8")
        self.manager = DataBackupManager(
            data_dir=self.data_dir,
            app_version="0.11.3",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_backup_and_list(self):
        info = self.manager.create_backup(name="manual-test", source="manual")
        self.assertTrue(info.path.exists())
        self.assertTrue((info.path / "tracker.db").exists())
        self.assertTrue((info.path / "settings.json").exists())
        self.assertTrue((info.path / "icons" / "app.ico").exists())
        self.assertTrue((info.path / "backup-meta.json").exists())

        backups = self.manager.list_backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].id, info.id)
        self.assertEqual(backups[0].version, "0.11.3")

    def test_restore_backup(self):
        original = self.manager.create_backup(name="original", source="manual")
        # 修改当前数据
        (self.data_dir / "tracker.db").write_text("new content", encoding="utf-8")
        (self.data_dir / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")

        restored = self.manager.restore_backup(original.id)
        self.assertEqual(restored.id, original.id)
        self.assertEqual((self.data_dir / "tracker.db").read_text(encoding="utf-8"), "db content")
        self.assertEqual((self.data_dir / "settings.json").read_text(encoding="utf-8"), '{"theme": "light"}')

        # 恢复前快照应存在
        backups = self.manager.list_backups()
        pre_restore = [b for b in backups if b.source == "pre-restore"]
        self.assertEqual(len(pre_restore), 1)

    def test_delete_backup(self):
        info = self.manager.create_backup(name="to-delete", source="manual")
        self.assertTrue(info.path.exists())
        result = self.manager.delete_backup(info.id)
        self.assertTrue(result["ok"])
        self.assertFalse(info.path.exists())

    def test_restore_nonexistent_raises(self):
        with self.assertRaises(DataBackupError):
            self.manager.restore_backup("does-not-exist")

    def test_pause_resume_callback_invoked(self):
        events = []

        def pause():
            events.append("pause")

        def resume():
            events.append("resume")

        manager = DataBackupManager(
            data_dir=self.data_dir,
            app_version="0.11.3",
            pause_callback=pause,
            resume_callback=resume,
        )
        manager.create_backup(name="with-callback", source="manual")
        self.assertIn("pause", events)
        self.assertIn("resume", events)

    def test_auto_backup_pruning(self):
        manager = DataBackupManager(data_dir=self.data_dir, app_version="0.11.3")
        for i in range(12):
            manager.create_backup(source="update", is_auto=True)
            time.sleep(0.01)
        auto = [b for b in manager.list_backups() if b.is_auto]
        self.assertLessEqual(len(auto), 10)

    def test_backup_size_nonzero(self):
        info = self.manager.create_backup(name="sized", source="manual")
        self.assertGreater(info.size, 0)


class TestDataBackupConcurrency(TestCase):
    def test_concurrent_create_is_safe(self):
        tmp = Path(tempfile.mkdtemp(prefix="ir-backup-concurrent-"))
        try:
            data_dir = tmp / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "tracker.db").write_text("db", encoding="utf-8")
            (data_dir / "settings.json").write_text("{}", encoding="utf-8")
            manager = DataBackupManager(data_dir=data_dir, app_version="0.11.3")

            errors = []

            def create():
                try:
                    manager.create_backup(source="manual")
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=create) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            self.assertEqual(len(manager.list_backups()), 5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

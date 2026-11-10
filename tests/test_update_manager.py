import io
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import update_manager
from scripts import preflight_release


class _Response(io.BytesIO):
    status = 200

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class UpdateEncodingTests(unittest.TestCase):
    def test_manifest_url_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = root / "data"
            data.mkdir()
            expected = "https://example.test/update.json"
            (data / "update-url.txt").write_text(expected + "\n", encoding="utf-8-sig")

            with patch.object(update_manager, "app_root", return_value=root):
                self.assertEqual(update_manager._manifest_url(), expected)

    def test_http_json_accepts_utf8_bom(self):
        payload = {"latest": "1.9.8", "download_url": "https://example.test/app.zip"}
        body = b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")

        with patch("update_manager.urllib.request.urlopen", return_value=_Response(body)):
            self.assertEqual(update_manager._http_json("https://example.test/update.json"), payload)


class SourceBuildUpdateTests(unittest.TestCase):
    def test_source_build_never_requests_an_unconfigured_manifest(self):
        with patch.object(update_manager.config, "UPDATE_CHECK_ENABLED", False), patch.object(
            update_manager, "_http_json", side_effect=AssertionError("network must stay disabled")
        ):
            result = update_manager.check_update()

        manifest = result["manifest"]
        self.assertFalse(manifest["update_configured"])
        self.assertFalse(manifest["has_update"])
        self.assertEqual(manifest["update_policy"], "disabled_source")
        self.assertFalse(update_manager.start_update_check_async())

    def test_source_build_rejects_in_app_install(self):
        with patch.object(update_manager.config, "UPDATE_CHECK_ENABLED", False):
            with self.assertRaisesRegex(update_manager.UpdateError, "源码构建未启用"):
                update_manager.install_update_async({}, lambda: None)


class VersionEpochTests(unittest.TestCase):
    def _normalize(self, current: str, current_epoch: int, raw: dict) -> dict:
        with (
            patch.object(update_manager.config, "APP_VERSION", current),
            patch.object(update_manager.config, "APP_VERSION_EPOCH", current_epoch, create=True),
        ):
            return update_manager._normalize_manifest({
                "download_url": "https://example.test/interaction-rhythm.zip",
                **raw,
            })

    def test_legacy_1_9_8_can_discover_bridge_1_9_9(self):
        self.assertGreater(
            update_manager._version_tuple("1.9.9"),
            update_manager._version_tuple("1.9.8"),
        )

    def test_epoch_2_target_does_not_downgrade_to_1_9_9(self):
        manifest = self._normalize("0.10.1", 2, {
            "latest": "1.9.9",
            "version_epoch": 1,
        })

        self.assertFalse(manifest["has_update"])

    def test_manifest_without_epoch_stays_in_legacy_epoch(self):
        manifest = self._normalize("0.10.1", 2, {"latest": "1.9.9"})

        self.assertEqual(manifest["version_epoch"], 1)
        self.assertFalse(manifest["has_update"])

    def test_invalid_version_epoch_is_rejected(self):
        with self.assertRaises(update_manager.UpdateError):
            self._normalize("1.9.9", 1, {
                "latest": "0.10.1",
                "version_epoch": "future",
            })

    def test_manifest_exposes_data_schema_contract(self):
        manifest = self._normalize("0.11.1", 2, {
            "latest": "0.11.1",
            "version_epoch": 2,
            "data_schema_min": 2,
            "data_schema_target": 2,
        })
        self.assertEqual(manifest["data_schema_min"], 2)
        self.assertEqual(manifest["data_schema_target"], 2)
        self.assertFalse(manifest["data_migration_required"])

    def test_manifest_rejects_unknown_data_schema_requirement(self):
        with self.assertRaisesRegex(update_manager.UpdateError, "数据版本"):
            self._normalize("0.11.1", 2, {
                "latest": "0.11.1",
                "version_epoch": 2,
                "data_schema_min": 3,
            })


class ReleaseManifestTests(unittest.TestCase):
    def _release_root(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        root = Path(temp_dir.name)
        archive = root / "interaction-rhythm.zip"
        archive.write_bytes(b"0.10.1 package fixture")
        current_manifest = {
            "latest": "0.10.1",
            "version_epoch": 2,
            "download_url": "https://example.test/releases/v0.10.1/interaction-rhythm.zip",
            "sha256": preflight_release.sha256(archive),
            "size": archive.stat().st_size,
        }
        legacy_alias = {
            "latest": "1.9.9",
            "version_epoch": 1,
            "download_url": current_manifest["download_url"],
            "sha256": preflight_release.sha256(archive),
            "size": archive.stat().st_size,
        }
        (root / "update.json").write_text(json.dumps(legacy_alias), encoding="utf-8")
        (root / "update-v2.json").write_text(json.dumps(current_manifest), encoding="utf-8")
        return temp_dir, root

    def test_preflight_accepts_matching_version_epoch(self):
        temp_dir, root = self._release_root()
        with temp_dir:
            self.assertEqual(preflight_release.check_manifest(root, "0.10.1", 2), [])

    def test_preflight_rejects_mismatched_version_epoch(self):
        temp_dir, root = self._release_root()
        with temp_dir:
            errors = preflight_release.check_manifest(root, "0.10.1", 1)

        self.assertIn("manifest version_epoch mismatch: 2 != 1", errors)

    def test_epoch_2_release_uses_legacy_alias_for_same_zero_major_package(self):
        temp_dir, root = self._release_root()
        with temp_dir:
            self.assertEqual(
                preflight_release.check_legacy_manifest(root, 2, "0.10.1"),
                [],
            )

    def test_legacy_alias_must_keep_trigger_version_and_epoch(self):
        temp_dir, root = self._release_root()
        path = root / "update.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["latest"] = "0.10.1"
        manifest["version_epoch"] = 2
        path.write_text(json.dumps(manifest), encoding="utf-8")

        with temp_dir:
            errors = preflight_release.check_legacy_manifest(root, 2, "0.10.1")

        self.assertIn("legacy update.json must keep latest at 1.9.9", errors)
        self.assertIn("legacy update.json must keep version_epoch at 1", errors)

    def test_legacy_alias_must_point_to_and_hash_current_package(self):
        temp_dir, root = self._release_root()
        path = root / "update.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["download_url"] = "https://example.test/releases/v1.9.9/interaction-rhythm.zip"
        manifest["sha256"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")

        with temp_dir:
            errors = preflight_release.check_legacy_manifest(root, 2, "0.10.1")

        self.assertIn("legacy update.json must point to the current 0.10.1 package", errors)
        self.assertIn("legacy update.json sha256 must match interaction-rhythm.zip", errors)

    def test_package_update_urls_use_current_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "interaction-rhythm.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("InteractionRhythm.exe", b"fixture")
                zf.writestr("update-url.txt", "https://example.test/update-v2.json\n")
                zf.writestr("data/update-url.txt", "https://example.test/update-v2.json\n")

            self.assertEqual(preflight_release.check_zip(archive), [])

    def test_package_cannot_remain_on_legacy_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "interaction-rhythm.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("InteractionRhythm.exe", b"fixture")
                zf.writestr("update-url.txt", "https://example.test/update.json\n")
                zf.writestr("data/update-url.txt", "https://example.test/update.json\n")

            errors = preflight_release.check_zip(archive)

        self.assertIn("interaction-rhythm.zip: update-url.txt must point to update-v2.json", errors)
        self.assertIn("interaction-rhythm.zip: data/update-url.txt must point to update-v2.json", errors)

    def test_package_rejects_embedded_openai_style_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "interaction-rhythm.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("InteractionRhythm.exe", b"fixture")
                zf.writestr("update-url.txt", "https://example.test/update-v2.json\n")
                zf.writestr("data/update-url.txt", "https://example.test/update-v2.json\n")
                fake_key = b"sk-" + b"abcdefghijklmnopqrstuvwxyz123456"
                zf.writestr("static/config.js", b"const token = '" + fake_key + b"';")

            errors = preflight_release.check_zip(archive)

        self.assertIn(
            "interaction-rhythm.zip: embedded credential pattern in static/config.js",
            errors,
        )


class UpdateInstallationTests(unittest.TestCase):
    def test_prepare_staging_rejects_manifest_size_mismatch(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as zf:
            zf.writestr("InteractionRhythm.exe", b"fixture")
        archive_bytes = payload.getvalue()
        manifest = {
            "latest": "0.10.1",
            "download_url": "https://example.test/interaction-rhythm.zip",
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "size": len(archive_bytes) + 1,
        }

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            update_manager, "updates_dir", return_value=Path(temp_dir)
        ), patch.object(
            update_manager,
            "_download",
            side_effect=lambda _url, target: target.write_bytes(archive_bytes),
        ):
            with self.assertRaisesRegex(update_manager.UpdateError, "大小"):
                update_manager._prepare_staging(manifest)

    def test_generated_rollback_cleans_partial_program_before_restore(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "app"
            updates = app / "data" / "updates"
            staging = root / "staging"
            app.mkdir(parents=True)
            updates.mkdir(parents=True)
            staging.mkdir()
            backup_dir = app / "data" / "backups" / "update-backup"
            backup_dir.mkdir(parents=True)
            (app / "InteractionRhythm.exe").write_bytes(b"old")
            (staging / "InteractionRhythm.exe").write_bytes(b"new")
            package = update_manager.UpdatePackage(
                manifest={"latest": "0.10.1"},
                zip_path=root / "package.zip",
                staging_dir=staging,
            )

            from interaction_rhythm import data_backup

            class _FakeManager:
                backup_root = app / "data" / "backups"

                def create_update_backup(self):
                    (backup_dir / "tracker.db").write_text("db")
                    (backup_dir / "settings.json").write_text("{}")
                    (backup_dir / "backup-meta.json").write_text('{"version":"0.0.1"}')
                    return backup_dir

            with patch.object(update_manager, "app_root", return_value=app), patch.object(
                update_manager, "updates_dir", return_value=updates
            ), patch.object(data_backup, "DataBackupManager", return_value=_FakeManager()):
                script = update_manager._write_updater(package).read_text(encoding="utf-8")

        rollback = script[script.index('State "rollback_started"') :]
        cleanup = 'Get-ChildItem -LiteralPath $Target -Force | Where-Object { $_.Name -ne "data" } | Remove-Item -Recurse -Force'
        restore_program = 'Copy-Item -LiteralPath $_.FullName -Destination $Target -Recurse -Force'
        restore_data = 'Get-ChildItem -LiteralPath $Backup -Force | Where-Object { ($_.Name -ne "program-preserved") -and ($_.Name -ne "backup-meta.json") }'
        self.assertIn(cleanup, rollback)
        self.assertIn(restore_program, rollback)
        self.assertIn(restore_data, rollback)
        self.assertLess(rollback.index(cleanup), rollback.index(restore_program))
        self.assertLess(rollback.index(restore_program), rollback.index(restore_data))


if __name__ == "__main__":
    unittest.main()

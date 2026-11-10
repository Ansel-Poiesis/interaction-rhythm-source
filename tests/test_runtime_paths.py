import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from interaction_rhythm.runtime_paths import DATA_DIR_ENV, resolve_data_dir


ROOT = Path(__file__).resolve().parents[1]


class RuntimePathTests(unittest.TestCase):
    def test_packaged_smoke_uses_an_isolated_data_directory(self):
        smoke = (ROOT / "scripts" / "smoke_packaged_runtime.py").read_text(encoding="utf-8")
        self.assertIn('env["INTERACTION_RHYTHM_DATA_DIR"]', smoke)
        self.assertIn("candidate_db.exists()", smoke)

    def test_isolated_data_directory_is_shared_by_all_runtime_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            isolated = Path(temp_dir) / "isolated-data"
            env = dict(os.environ)
            env[DATA_DIR_ENV] = str(isolated)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import diagnostics, main, settings, update_manager; "
                        "print(main.data_path()); "
                        "print(settings._data_dir()); "
                        "print(diagnostics.data_dir()); "
                        "print(update_manager.data_dir())"
                    ),
                ],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            paths = [Path(line).resolve() for line in result.stdout.splitlines()]

        self.assertEqual(paths, [isolated.resolve()] * 4)

    def test_relative_override_is_rejected(self):
        previous = os.environ.get(DATA_DIR_ENV)
        os.environ[DATA_DIR_ENV] = "relative-data"
        try:
            with self.assertRaisesRegex(ValueError, "must be an absolute path"):
                resolve_data_dir(ROOT)
        finally:
            if previous is None:
                os.environ.pop(DATA_DIR_ENV, None)
            else:
                os.environ[DATA_DIR_ENV] = previous


if __name__ == "__main__":
    unittest.main()

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class FrontendLoadingRegressionTests(unittest.TestCase):
    def test_date_loading_is_progressive_and_stale_safe(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required for frontend regression checks")
        result = subprocess.run(
            [node, str(REPO_ROOT / "scripts" / "test_frontend_loading.js")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FRONTEND_LOADING_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()

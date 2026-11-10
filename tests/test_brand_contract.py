from pathlib import Path
import unittest

import config
import settings


ROOT = Path(__file__).resolve().parents[1]


class BrandContractTests(unittest.TestCase):
    def test_runtime_uses_the_public_product_name(self):
        self.assertEqual(config.APP_NAME, "扣舷-数字节律")
        self.assertEqual(settings.RUN_VALUE_NAME, "扣舷-数字节律")
        self.assertEqual(
            config.APP_MUTEX_NAME,
            r"Local\Kouxian.DigitalRhythm.SingleInstance",
        )

    def test_public_application_version_matches_initial_release(self):
        self.assertEqual(config.APP_VERSION, "0.1.0")
        self.assertEqual(config.APP_VERSION_EPOCH, 2)

    def test_source_builds_have_no_update_feed_by_default(self):
        self.assertEqual(config.UPDATE_MANIFEST_URL, "")
        self.assertFalse(config.UPDATE_CHECK_ENABLED)

    def test_frontend_keeps_the_product_name_and_brand_story(self):
        index_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>扣舷-数字节律</title>", index_html)
        self.assertIn("扣舷而歌", (ROOT / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

"""打赏（支持扣舷）功能：设置持久化、外部 URL 打开与前端契约。"""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from interaction_rhythm.runtime_paths import DATA_DIR_ENV
def _frontend_source() -> str:
    """index.html + 拆分后的 style.css / i18n.js / app.js 拼接（D9 机械三拆后测试仍以整体前端为合同对象）。"""
    static = Path(__file__).resolve().parents[1] / "static"
    parts = []
    for name in ("index.html", "style.css", "i18n.js", "app.js"):
        candidate = static / name
        if candidate.exists():
            parts.append(candidate.read_text(encoding="utf-8"))
    return "\n".join(parts)


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "static" / "index.html"
WINDOW_PY = REPO_ROOT / "window.py"


class SettingsSupportUrlTests(unittest.TestCase):
    """support_url 设置项：持久化、清除与长度上限。"""

    @classmethod
    def setUpClass(cls):
        cls._previous_data_dir = os.environ.get(DATA_DIR_ENV)
        cls._tmp = tempfile.TemporaryDirectory(prefix="ir-support-")
        os.environ[DATA_DIR_ENV] = str(Path(cls._tmp.name) / "data")
        import settings as settings_mod

        importlib.reload(settings_mod)
        cls.settings = settings_mod

    @classmethod
    def tearDownClass(cls):
        if cls._previous_data_dir is None:
            os.environ.pop(DATA_DIR_ENV, None)
        else:
            os.environ[DATA_DIR_ENV] = cls._previous_data_dir
        cls._tmp.cleanup()

    def setUp(self):
        self.settings.save_settings({"support_url": ""})

    def test_support_url_round_trip_and_clear(self):
        saved = self.settings.save_settings({"support_url": "https://example.com/support"})
        self.assertEqual(saved["support_url"], "https://example.com/support")
        loaded = self.settings.get_settings()
        self.assertEqual(loaded["support_url"], "https://example.com/support")

        self.settings.save_settings({"support_url": ""})
        self.assertEqual(self.settings.get_settings()["support_url"], "")

    def test_support_url_is_stripped_and_length_bounded(self):
        saved = self.settings.save_settings({"support_url": "  https://example.com/tip  "})
        self.assertEqual(saved["support_url"], "https://example.com/tip")

        long_url = "https://example.com/" + "a" * 600
        self.settings.save_settings({"support_url": long_url})
        self.assertEqual(self.settings.get_settings()["support_url"], "https://example.com/tip")


class WindowOpenUrlTests(unittest.TestCase):
    """open_url 桥接只放行完整的 http(s) 地址。"""

    def setUp(self):
        from window import WindowAPI

        self.api = WindowAPI(None)

    def test_opens_http_and_https_urls(self):
        for value in (
            "https://example.com/support",
            "http://127.0.0.1:8080/tip",
            "  https://example.com/  ",
            "HTTPS://example.com/support",
        ):
            with self.subTest(value=value):
                with mock.patch("window.webbrowser.open", return_value=True) as opener:
                    self.assertTrue(self.api.open_url(value))
                    opener.assert_called_once_with(value.strip(), new=2)

    def test_rejects_empty_and_non_web_urls(self):
        for value in (
            None,
            "",
            "   ",
            "http://",
            "https://",
            "javascript:alert(1)",
            "file:///C:/Windows/win.ini",
            "ftp://example.com/file",
            "not-a-url",
        ):
            with self.subTest(value=value):
                with mock.patch("window.webbrowser.open", return_value=True) as opener:
                    self.assertFalse(self.api.open_url(value))
                    opener.assert_not_called()

    def test_returns_false_when_browser_fails(self):
        with mock.patch("window.webbrowser.open", side_effect=OSError("no browser")):
            self.assertFalse(self.api.open_url("https://example.com/support"))


class SupportUIContractTests(unittest.TestCase):
    """前端入口、设置表单与双语文案契约。"""

    @classmethod
    def setUpClass(cls):
        cls.html = _frontend_source()
        cls.window_py = WINDOW_PY.read_text(encoding="utf-8")

    SUPPORT_KEYS = (
        "support",
        "supportDesc",
        "supportUrl",
        "supportUrlPlaceholder",
        "supportOpen",
        "supportNotConfigured",
        "supportInvalid",
    )

    def test_support_keys_exist_in_both_languages(self):
        zh = self.html[self.html.index("zh:{") : self.html.index("en:{")]
        en = self.html[self.html.index("en:{") : self.html.index("  }", self.html.index("en:{"))]
        for key in self.SUPPORT_KEYS:
            self.assertIn(key + ":", zh)
            self.assertIn(key + ":", en)
        self.assertIn("support:'支持扣舷'", zh)
        self.assertIn("support:'Support Kouxian'", en)

    def test_header_and_titlebar_entries_exist(self):
        self.assertIn('onclick="openSupport()"', self.html)
        self.assertIn("function openSupport()", self.html)
        self.assertIn("support_btn", self.window_py)
        self.assertIn('_run_js("openSupport()")', self.window_py)
        self.assertIn('tips.SetToolTip(support_btn, "支持扣舷")', self.window_py)

    def test_settings_form_and_validation_exist(self):
        self.assertIn('id="support-url-input"', self.html)
        self.assertIn("function saveSupportUrl()", self.html)
        self.assertIn("function validSupportUrl(value)", self.html)
        self.assertIn("support_url:''", self.html)
        self.assertIn("support_url:'", self.html)
        self.assertIn("JSON.stringify({support_url:value})", self.html)
        self.assertIn("new URL(value)", self.html)

    def test_open_url_bridge_validates_scheme_and_host(self):
        self.assertIn("def open_url(self, url)", self.window_py)
        self.assertIn("from urllib.parse import urlsplit", self.window_py)
        self.assertIn("parts.scheme not in (\"http\", \"https\")", self.window_py)
        self.assertIn("not parts.hostname", self.window_py)


if __name__ == "__main__":
    unittest.main()

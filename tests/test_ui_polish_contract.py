"""稳定版 UI 细节、视觉检查工具与日期切换性能的合同测试。"""
import re
import unittest
from pathlib import Path


def _frontend_source() -> str:
    static = Path(__file__).resolve().parents[1] / "static"
    parts = []
    for name in ("index.html", "style.css", "i18n.js", "app.js"):
        candidate = static / name
        if candidate.exists():
            parts.append(candidate.read_text(encoding="utf-8"))
    return "\n".join(parts)


REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = REPO_ROOT / "static" / "index.html"
UI_FIXTURE = REPO_ROOT / "scripts" / "run_ui_fixture.py"


class AutostepHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = _frontend_source()
        cls.fixture = UI_FIXTURE.read_text(encoding="utf-8")

    def test_autostep_hook_present_and_bounded(self):
        self.assertIn("function runAutostep()", self.index)
        self.assertIn("autostep", self.index)
        self.assertIn("runAutostep();", self.index)

    def test_ui_polish_contract_v0115(self):
        self.assertIn(".settings-action.danger", self.index)
        self.assertIn("settings-action danger", self.index)
        self.assertIn("toggleApiKeyVisibility", self.index)
        self.assertIn('id="key-visibility-btn"', self.index)
        self.assertIn('id="lg-less"', self.index)
        self.assertIn('id="lg-more"', self.index)
        self.assertIn("legendLess:'少'", self.index)
        self.assertIn("legendMore:'多'", self.index)

    def test_ui_iteration_contract_v0116(self):
        for n in range(1, 8):
            self.assertIn(f'id="settings-sec-{n}"', self.index)
        self.assertIn("function settingsNavHtml()", self.index)
        self.assertIn("function scrollSettingsSection(", self.index)
        self.assertIn("intro4:'", self.index)
        self.assertIn("esc(t('intro4'))", self.index)
        self.assertIn("waitingToday", self.index)
        self.assertIn("dataRootLabel", self.index)
        self.assertIn("SETTINGS_STATE.data_root", self.index)
        self.assertIn("recordingNow", self.index)
        self.assertIn('class="recording-dot"', self.index)
        self.assertIn("heat-period-label", self.index)
        self.assertIn("yearDrillHint", self.index)

    def test_stable_v0121_visual_baseline_is_restored(self):
        self.assertIn('<div class="brand-copy"><h1>扣舷-数字节律</h1><span>看见你的数字手感</span></div>', self.index)
        self.assertIn("--r:8px;--r-sm:7px", self.index)
        self.assertIn("--shadow:0 10px 28px", self.index)
        self.assertIn("--shadow-sm:0 5px 16px", self.index)
        self.assertIn("width:520px;max-width:100%", self.index)
        self.assertIn("grid-template-rows:42px 58px 120px minmax(152px,1fr) 340px", self.index)
        self.assertIn("body.nativebar .wrap{padding-top:11px;grid-template-rows:58px 120px minmax(152px,1fr) 340px}", self.index)
        self.assertNotIn('id="data-progress"', self.index)
        self.assertNotIn('aria-busy="true"', self.index)
        self.assertNotIn("width:min(1180px", self.index)

    def test_paging_performance_keeps_stable_renderers(self):
        for required in (
            "var dashboardCache=new Map();",
            "var dashboardCacheLimit=12;",
            "function dashboardCacheFresh(entry,date)",
            "function prefetchAdjacentDate(date,direction)",
            "var controller=new AbortController();",
            "if(dataLoadController)dataLoadController.abort();",
            "if(dashboardPrefetchController)dashboardPrefetchController.abort();",
            "loadData({force:true});",
        ):
            self.assertIn(required, self.index)
        load_start = self.index.index("async function loadData(options)")
        load_end = self.index.index("/* ── Init", load_start)
        load_data = self.index[load_start:load_end]
        self.assertNotIn("/api/agent/observations", load_data)
        self.assertIn("Promise.all(requests)", load_data)
        self.assertIn("primaryOk.some(Boolean)", load_data)

    def test_index_has_no_unmatched_script_closing_tag(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"<script\b", html, re.I)), len(re.findall(r"</script>", html, re.I)))

    def test_fixture_backup_endpoints_are_stubbed(self):
        for method in (
            "create_data_backup",
            "list_data_backups",
            "restore_data_backup",
            "delete_data_backup",
        ):
            self.assertIn(f"service.{method} = fake_", self.fixture)
        self.assertIn("C:\\\\Fixture\\\\data\\\\backups", self.fixture)


if __name__ == "__main__":
    unittest.main()

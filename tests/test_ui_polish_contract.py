"""v0.11.5 UI 细节与视觉检查工具的合同测试。

锁定三类内容：
1. 前端视觉检查钩子（?autostep=）存在于 index.html；
2. v0.11.5 UI 修复（danger 样式、API Key 显隐、图例本地化）不被回退；
3. UI fixture 的备份端点保持内存桩，不触碰真实 data/backups 目录
   （2026-09-29 视觉检查期间曾因缺隔离把快照写进真实数据目录）。
"""
import unittest
from pathlib import Path
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
UI_FIXTURE = REPO_ROOT / "scripts" / "run_ui_fixture.py"


class AutostepHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = _frontend_source()
        cls.fixture = UI_FIXTURE.read_text(encoding="utf-8")

    def test_autostep_hook_present_and_bounded(self):
        self.assertIn("function runAutostep()", self.index)
        self.assertIn("autostep", self.index)
        # init 完成后才执行，避免与首屏渲染竞态
        self.assertIn("runAutostep();", self.index)

    def test_ui_polish_contract_v0115(self):
        # 破坏性操作的红色 danger 样式
        self.assertIn(".settings-action.danger", self.index)
        self.assertIn("settings-action danger", self.index)
        # API Key 显隐切换
        self.assertIn("toggleApiKeyVisibility", self.index)
        self.assertIn('id="key-visibility-btn"', self.index)
        # 热力图图例本地化
        self.assertIn('id="lg-less"', self.index)
        self.assertIn('id="lg-more"', self.index)
        self.assertIn("legendLess:'少'", self.index)
        self.assertIn("legendMore:'多'", self.index)

    def test_ui_iteration_contract_v0116(self):
        # D1 设置锚点导航：7 个分区 id + sticky 导航
        for n in range(1, 8):
            self.assertIn(f'id="settings-sec-{n}"', self.index)
        self.assertIn("function settingsNavHtml()", self.index)
        self.assertIn("function scrollSettingsSection(", self.index)
        # D2 intro 第四点（采集预期管理）
        self.assertIn("intro4:'", self.index)
        self.assertIn("esc(t('intro4'))", self.index)
        # D3 冷启动空态区分今天/过去
        self.assertIn("waitingToday", self.index)
        # D4 数据目录展示
        self.assertIn("dataRootLabel", self.index)
        self.assertIn("SETTINGS_STATE.data_root", self.index)
        # D5 记录中徽标
        self.assertIn("recordingNow", self.index)
        self.assertIn('class="recording-dot"', self.index)
        # D7 周期标题层级与年视图下钻提示
        self.assertIn("heat-period-label", self.index)
        self.assertIn("yearDrillHint", self.index)

    def test_fixture_backup_endpoints_are_stubbed(self):
        # fixture 必须替换备份 service 方法，禁止落到真实 DataBackupManager
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

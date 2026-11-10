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
INDEX_HTML = REPO_ROOT / "static" / "index.html"  # 保留常量；合同对象改为拼接前端源


class AIReplayUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _frontend_source()

    def test_handfeel_replay_remains_the_primary_header_entry(self):
        self.assertIn('data-action="insights" onclick="openMimoInsights()"', self.html)
        self.assertNotIn('data-action="agent"', self.html)

    def test_native_titlebar_owns_the_desktop_header(self):
        self.assertIn("body.nativebar .header{display:none}", self.html)
        self.assertIn(
            "body.nativebar .wrap{padding-top:11px;grid-template-rows:58px 120px minmax(152px,1fr) 340px}",
            self.html,
        )
        self.assertIn("var IS_NATIVEBAR=QUERY.get('nativebar')==='1';", self.html)
        self.assertIn("if(IS_NATIVEBAR){document.body.classList.add('nativebar')}", self.html)

    def test_weekly_clues_are_reached_from_the_daily_replay(self):
        self.assertIn('onclick="openAgentObservations();return false"', self.html)
        self.assertIn("esc(t('agentWeeklyClues'))", self.html)
        self.assertIn("agentViewObservations:'本周路径建议'", self.html)

    def test_ai_surfaces_do_not_render_explanatory_small_print(self):
        for forbidden in (
            "用最近的本地记录，看看本周有哪些变化。",
            "根据本地记录整理，未使用 AI",
            "AI 会基于本周线索补充少量参考方向。",
            "不会显示模型的内部推理过程。",
            "API Key 只从本机环境读取，不会保存到设置。",
            "Local rule-based analysis. No upload",
        ):
            self.assertNotIn(forbidden, self.html)

        self.assertNotIn("var foot=d.model_note", self.html)
        self.assertNotIn("t('agentWeeklyCluesDesc')", self.html)
        self.assertNotIn("t('agentLocalLabel')", self.html)
        self.assertNotIn("t('agentDeepReadLocalReady')", self.html)
        self.assertNotIn("t('agentThinkingHint')", self.html)
        self.assertNotIn("t('aiCloudHint')", self.html)

    def test_research_contract_language_is_not_rendered(self):
        for forbidden in (
            "本周观察",
            "深入 Agent",
            "回看目的",
            "证据 ID",
            "Fact IDs",
            "理论视角",
        ):
            self.assertNotIn(forbidden, self.html)

    def test_settings_keep_the_original_section_order(self):
        settings_start = self.html.index("function renderSettings(settings)")
        settings_end = self.html.index("async function loadSettingsQuietly", settings_start)
        settings_html = self.html[settings_start:settings_end]

        headings = [
            settings_html.index("'外观'"),
            settings_html.index("'启动方式'"),
            settings_html.index("t('aiSettings')"),
            settings_html.index("'数据与版本'"),
        ]
        self.assertEqual(headings, sorted(headings))



    def test_ai_settings_ask_for_key_instead_of_showing_model(self):
        for forbidden in (
            "本地 MiMo",
            "模型名称",
            "ai-model",
            "ai-base-url",
            "aiCloudEnabled",
            "ollama",
        ):
            self.assertNotIn(forbidden, self.html)
        for required in (
            'id="ai-api-key"',
            "t('aiApiKey')",
            "openExportWithPrompt()",
            "['mimo','MiMo API']",
            "['deepseek','DeepSeek']",
        ):
            self.assertIn(required, self.html)

    def test_ai_entry_points_guide_when_no_key(self):
        self.assertIn("ensureAiKeyReady()", self.html)
        self.assertIn("showAiSetupGuide(", self.html)
        self.assertIn("t('aiNoKeyBody')", self.html)
        self.assertIn("t('aiExportPrompt')", self.html)
        self.assertIn("aiExportGuide:'导出最近 30 天的 JSON 数据", self.html)


    def test_agent_reading_is_single_item_with_pagination(self):
        self.assertIn("function agentReadingInner()", self.html)
        self.assertIn("readingNav(-1)", self.html)
        self.assertIn("readingNav(1)", self.html)
        self.assertIn("currentReadingIndex", self.html)
        self.assertIn('id="agent-reading-box"', self.html)

    def test_ai_replay_uses_12h_cache_and_manual_refresh(self):
        self.assertIn("var AI_REPLAY_TTL_MS=12*3600*1000;", self.html)
        self.assertIn("function readAiReplayCache(date)", self.html)
        self.assertIn("function writeAiReplayCache(date,data)", self.html)
        self.assertIn("function clearAiReplayCache(date)", self.html)
        self.assertIn("async function refreshMimoInsights()", self.html)
        self.assertIn("renderInsights(cached,{mimo:true,cached:true})", self.html)
        self.assertIn("esc(t('aiCachedNote'))", self.html)

    def test_closing_analysis_modal_cancels_insight_intent(self):
        self.assertIn("var insightLive=false;", self.html)
        self.assertIn("if(variant.indexOf('analysis-modal')!==-1||variant.indexOf('insight-modal')!==-1)insightLive=false;", self.html)
        self.assertIn("if(!insightLive)return;", self.html)

    def test_startup_focuses_today_and_rolls_after_midnight(self):
        self.assertIn("selectedDate=todayIso();", self.html)
        self.assertIn("function focusToday()", self.html)
        self.assertIn("var lastKnownToday=todayIso();", self.html)
        self.assertIn("if(nowToday!==lastKnownToday)", self.html)
        self.assertIn('onclick="focusToday()"', self.html)

    def test_agent_prompt_is_editable_in_settings(self):
        self.assertIn("ai_agent_prompt", self.html)
        self.assertIn('id="ai-agent-prompt"', self.html)
        self.assertIn("function resetAgentPrompt()", self.html)
        self.assertIn("async function refreshDefaultAgentPrompt()", self.html)
        self.assertIn("d.agent_prompt", self.html)


    def test_ai_config_guide_and_index_exist(self):
        self.assertIn("function showAiConfigGuide()", self.html)
        self.assertIn("function aiConfigGuideHtml()", self.html)
        self.assertIn("function showAiConfigIndex()", self.html)
        self.assertIn("function aiConfigIndexHtml()", self.html)
        self.assertIn("function aiIndexRow(it)", self.html)
        self.assertIn("var AI_CONFIG_INDEX=[", self.html)
        self.assertIn("var AI_GUIDE_SEEN_KEY=", self.html)
        self.assertIn("aiGuideTitle:'AI 配置引导'", self.html)
        self.assertIn("aiConfigIndexTitle:'AI 配置索引'", self.html)
        self.assertIn("'ai-guide-modal'", self.html)
        self.assertIn("showAiConfigGuide()", self.html)
        self.assertIn("showAiConfigIndex()", self.html)

    def test_first_run_chains_ai_config_guide_after_intro(self):
        self.assertIn("var aiGuidePending=false;", self.html)
        self.assertIn("async function showAiConfigGuideIfFirstRun()", self.html)
        self.assertIn("if(firstRun){aiGuidePending=true;}", self.html)
        self.assertIn("hideModal(function(){if(aiGuidePending)showAiConfigGuideIfFirstRun()})", self.html)
        self.assertIn("showModal(t('introTitle'),introHtml(),'intro-modal',{returnTo:null});\n  return true;", self.html)

    def test_reading_paragraphs_are_expanded_to_2_plus_sentences(self):
        import re
        match = re.search(r"var AGENT_READING_PARAGRAPHS=\[(.*?)\];\n+var AGENT_READING_FULLTEXT", self.html, re.S)
        self.assertIsNotNone(match)
        body = match.group(1)
        entries = re.findall(r"id:'(reading-\d+)',\s*zh:'(.*?)',\s*en:'(.*?)'", body, re.S)
        self.assertEqual(len(entries), 50)
        self.assertEqual([e[0] for e in entries], ["reading-%02d" % i for i in range(1, 51)])
        for pid, zh, en in entries:
            self.assertGreaterEqual(len(zh), 60, pid)
            self.assertGreaterEqual(len(en), 200, pid)
            self.assertGreaterEqual(len(re.findall(r"[。！？]", zh)), 2, pid)
            self.assertGreaterEqual(len(re.findall(r"\.(?=[\s”’“]|$)", en)), 2, pid)

    def test_agent_prompt_area_has_no_resize_handle(self):
        self.assertNotIn("min-height:180px;resize:vertical;", self.html)
        self.assertIn("min-height:180px;resize:none;", self.html)

    def test_stats_row_splits_mouse_into_clicks_and_scroll(self):
        self.assertIn(".stats-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));", self.html)
        self.assertIn(".stats-row{grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));", self.html)
        self.assertNotIn(".stats-row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));", self.html)
        self.assertNotIn(".stats-row{grid-template-columns:repeat(4,minmax(0,1fr));", self.html)
        self.assertNotIn(".stats-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));", self.html)
        self.assertNotIn(".stats-row{grid-template-columns:repeat(3,minmax(0,1fr));", self.html)
        for required in (
            'id="key-stat-label"',
            'id="clicks-stat-label"',
            'id="scroll-stat-label"',
            'id="time-stat-label"',
            'id="v-keys"',
            'id="v-clicks"',
            'id="v-scroll"',
            'id="v-time"',
            "t('mouseClicks')",
            "t('mouseScroll')",
            "mouseClicks:'点击'",
            "mouseScroll:'滚轮'",
            "mouseClicks:'Clicks'",
            "mouseScroll:'Scroll'",
            ".stat-label{font-size:14px",
            ".stat-val{font-size:20px",
            "      .stat-label{font-size:12px;",
            "      .stat-val{font-size:17px;",
            "flex:0 0 var(--stat-label-w,60px)",
            "min-width:var(--stat-val-minw,80px)",
        ):
            self.assertIn(required, self.html)
        self.assertNotIn(".stat-label{font-size:11px;", self.html)
        self.assertNotIn(".stat-label{font-size:12px;letter-spacing", self.html)
        self.assertNotIn(".stat-val{font-size:17px;font-weight", self.html)
        self.assertNotIn(".stat-val{font-size:15px", self.html)
        for forbidden in (
            'id="v-mouse"',
            'id="mouse-stat-label"',
            "t('mouse')",
            "t('mouseLegacy')",
            ".stat-val.split",
        ):
            self.assertNotIn(forbidden, self.html)


if __name__ == "__main__":

    unittest.main()

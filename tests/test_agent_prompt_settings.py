"""Agent 提示词设置：settings 持久化 + AI 出站提示词覆盖。"""
import os
import tempfile
import unittest
from pathlib import Path

from interaction_rhythm.runtime_paths import DATA_DIR_ENV

ROOT = Path(__file__).resolve().parents[1]


class AgentPromptSettingsTests(unittest.TestCase):
    """验证 ai_agent_prompt 设置：默认、保存、超长拒绝、AI 出站覆盖。"""

    @classmethod
    def setUpClass(cls):
        cls._previous_data_dir = os.environ.get(DATA_DIR_ENV)
        cls._tmp = tempfile.TemporaryDirectory(prefix="ir-prompt-settings-")
        os.environ[DATA_DIR_ENV] = str(Path(cls._tmp.name) / "data")
        # 让 settings 模块以隔离数据目录重新加载
        import importlib
        import settings as settings_mod
        import mimo_client as mimo_mod
        importlib.reload(settings_mod)
        importlib.reload(mimo_mod)
        cls.settings = settings_mod
        cls.mimo = mimo_mod

    @classmethod
    def tearDownClass(cls):
        if cls._previous_data_dir is None:
            os.environ.pop(DATA_DIR_ENV, None)
        else:
            os.environ[DATA_DIR_ENV] = cls._previous_data_dir
        cls._tmp.cleanup()

    def setUp(self):
        self.settings.save_settings({"ai_agent_prompt": ""})

    def test_default_prompt_is_empty_and_overrides_to_default(self):
        loaded = self.settings.get_settings()
        self.assertEqual(loaded["ai_agent_prompt"], "")
        self.assertEqual(self.mimo.current_agent_prompt(), self.mimo.DEFAULT_AGENT_PROMPT)

    def test_prompt_round_trip_and_oversize_rejected(self):
        self.settings.save_settings({"ai_agent_prompt": "你是我的专属节律顾问。"})
        loaded = self.settings.get_settings()
        self.assertEqual(loaded["ai_agent_prompt"], "你是我的专属节律顾问。")

        self.settings.save_settings({"ai_agent_prompt": "X" * 20000})
        loaded_again = self.settings.get_settings()
        self.assertEqual(loaded_again["ai_agent_prompt"], "你是我的专属节律顾问。")

    def test_daily_messages_use_override_when_set(self):
        self.settings.save_settings({"ai_agent_prompt": "自定义提示词：关注连续性。"})
        messages = self.mimo._build_ai_messages({"date": "2026-08-05"})
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "自定义提示词：关注连续性。")

    def test_deep_read_messages_use_override_when_set(self):
        self.settings.save_settings({"ai_agent_prompt": "自定义提示词：关注连续性。"})
        feed = {"observations": [], "evidence_pack": {}}
        messages = self.mimo.build_agent_deep_read_messages(feed, db=None)
        self.assertEqual(messages[0]["content"], "自定义提示词：关注连续性。")

    def test_clear_override_restores_default(self):
        self.settings.save_settings({"ai_agent_prompt": "临时提示词。"})
        self.settings.save_settings({"ai_agent_prompt": ""})
        self.assertEqual(self.mimo.current_agent_prompt(), self.mimo.DEFAULT_AGENT_PROMPT)

    def test_ai_config_exposes_current_agent_prompt(self):
        from interaction_rhythm.service import ApplicationService

        config = ApplicationService.ai_config(None)
        self.assertTrue(config["ok"])
        self.assertEqual(config["agent_prompt"], self.mimo.DEFAULT_AGENT_PROMPT)
        self.assertIn("你是个人交互节律分析助手", config["agent_prompt"])


if __name__ == "__main__":
    unittest.main()

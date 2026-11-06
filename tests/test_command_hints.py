"""未知指令检测 + 报错信息全矩阵（2026-11 关键词检测任务）。"""
import asyncio
import sys
import unittest
from unittest.mock import patch

from test_core import ROOT  # noqa: F401
from framework import Event, install

install()
from astrbot_plugin_hd2_common import commands, errors  # noqa: E402
from astrbot_plugin_hd2_common.commands import command_suggestion  # noqa: E402
from astrbot_plugin_hd2_guard.main import Hd2GuardPlugin  # noqa: E402


def suggestion(text, wake=True):
    return command_suggestion(Event(text, wake=wake))


class SuggestionMatrix(unittest.TestCase):
    """关键词检测：对应/相近指令提示。"""

    def test_exact_alias_bare_no_slash(self):
        self.assertIn("/roll", suggestion("roll"))
        self.assertIn("/战报", suggestion("战报"))
        self.assertIn("/dss", suggestion("dss"))
        self.assertIn("/帮助", suggestion("帮助"))
        self.assertIn("未知指令", suggestion("roll"))

    def test_official_at_wake(self):
        # 官方平台 @机器人 roll：@ 剥离后同为 roll
        self.assertIn("/roll", suggestion("@bot roll"))

    def test_typo_close_match(self):
        self.assertIn("/roll", suggestion("rolls"))
        self.assertIn("/wiki", suggestion("woki"))
        self.assertIn("/帮助", suggestion("hel"))
        self.assertIn("未知指令", suggestion("rolls"))

    def test_two_word_first_token(self):
        self.assertIn("/战报", suggestion("战报 今天"))

    def test_natural_language_alias(self):
        self.assertIn("/战报", suggestion("目前战况如何"))
        self.assertIn("/重要指令", suggestion("重要指令是什么"))

    def test_slash_unknown_fallback(self):
        got = suggestion("/zhanbao")
        self.assertIn("未知指令", got)
        self.assertIn("/帮助", got)

    def test_plain_chat_never_hinted(self):
        self.assertIsNone(suggestion("今天天气怎么样"))
        self.assertIsNone(suggestion("roll 一个全新的组合拳"))
        self.assertIsNone(suggestion("帮我写一首诗"))

    def test_numbers_and_empty(self):
        self.assertIsNone(suggestion(""))
        self.assertIsNone(suggestion("1"))
        self.assertIsNone(suggestion("。"))

    def test_variant_terms_parse_without_hint(self):
        # 变种裸词已被 parse 直接接住，不应走到提示
        parsed = commands.parse_event(Event("孢裂变种"))
        if parsed:  # 词条存在才断言
            self.assertEqual(parsed[0], "variant")
        else:
            self.assertIsNone(suggestion("孢裂变种"))

    def test_non_wake_ignored(self):
        self.assertIsNone(suggestion("roll", wake=False))


class GuardIntegration(unittest.TestCase):
    """guard 检测接线：指令尝试回复提示并拦截；普通聊天维持原行为。"""

    def _run(self, text, allow_chat=False):
        with tempfile_dir() as path:
            import json
            from pathlib import Path
            Path(path, "config.json").write_text(
                json.dumps({"bot": {"platform": "napcat", "groups": [], "allow_chat": allow_chat}}),
                encoding="utf-8")
            with patch.dict("os.environ", {"HD2_CONFIG": str(Path(path, "config.json"))}):
                plugin = Hd2GuardPlugin(None)
                event = Event(text)
                asyncio.run(plugin.guard(event))
                sent = getattr(event, "sent", [])
                return event, sent

    def test_command_attempt_replies_hint_and_stops(self):
        event, sent = self._run("roll", allow_chat=True)
        self.assertTrue(event.stopped)
        self.assertTrue(sent and "未知指令" in sent[0].chain[0][1])
        self.assertIn("/roll", sent[0].chain[0][1])

    def test_command_attempt_slash_unknown(self):
        event, sent = self._run("/zhanbao", allow_chat=True)
        self.assertTrue(event.stopped)
        self.assertTrue(sent and "未知指令" in sent[0].chain[0][1])

    def test_plain_chat_passes_to_llm_when_allowed(self):
        event, sent = self._run("今天天气怎么样", allow_chat=True)
        self.assertFalse(event.stopped)
        self.assertFalse(sent)

    def test_plain_chat_stops_when_chat_disabled(self):
        event, sent = self._run("普通消息", allow_chat=False)
        self.assertTrue(event.stopped)
        self.assertFalse(sent)

    def test_valid_command_never_hinted(self):
        event, sent = self._run("/查询", allow_chat=True)
        self.assertFalse(sent)  # 由 planet_info 的用法提示接管


def tempfile_dir():
    import tempfile
    return tempfile.TemporaryDirectory()


class ErrorFriendlyMatrix(unittest.TestCase):
    """errors.friendly 报错矩阵：网络 / 语义 / 未知三类。"""

    def test_network_family(self):
        import urllib.error
        for err in (TimeoutError(), ConnectionError(), urllib.error.URLError("x")):
            got = errors.friendly("report", err)
            self.assertTrue(got.startswith("⚠️ 战报"))
            self.assertIn("稍后重试", got)

    def test_semantic_valueerror(self):
        got = errors.friendly("planet", ValueError("未找到该星球，请使用完整中文名、英文名或星球 ID"))
        self.assertIn("星球查询", got)
        self.assertIn("未找到该星球", got)

    def test_unknown_exception_generic(self):
        got = errors.friendly("dss", RuntimeError("boom"))
        self.assertIn("DSS", got)
        self.assertIn("RuntimeError", got)

    def test_usage_aliases_cover_all_kinds(self):
        for kind in ("report", "mo", "campaigns", "dss", "briefing", "api", "map",
                     "planet", "variant", "wiki", "roll", "btk", "analysis", "help"):
            self.assertIn(kind, errors.USAGE)
            self.assertTrue(errors.USAGE[kind][0])
            self.assertTrue(errors.USAGE[kind][1])


if __name__ == "__main__":
    unittest.main()

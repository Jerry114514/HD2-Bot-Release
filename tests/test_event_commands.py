import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_core import ROOT  # also adds the plugin path
from framework import Event, install
install()
from astrbot_plugin_hd2_common import commands, diagnostics
from astrbot_plugin_hd2_guard.main import Hd2GuardPlugin
from astrbot_plugin_hd2_war_report.main import Hd2WarReportPlugin
from astrbot_plugin_hd2_roll import main as roll
from astrbot_plugin_hd2_lookup import main as lookup
from astrbot_plugin_hd2_monitor.main import Hd2MonitorPlugin


def stripped_event(original):
    event = Event(original[1:])
    event.message_obj.message_str = original
    return event


class EventCommandTests(unittest.TestCase):
    def test_original_text_is_authoritative_and_slash_stays_required(self):
        for original in ("/roll", "/api", "/战报 t", "/wiki AR23", "/星球 X45", "/变种", "/help"):
            event = stripped_event(original)
            self.assertEqual(commands.parse_event(event), commands.parse(original))
            self.assertEqual(event.message_str, original[1:])
        for original in ("roll", "api", "战报", "", "/unknown"):
            event = Event("/roll")
            event.message_obj.message_str = original
            self.assertIsNone(commands.parse_event(event))
        # Older event doubles/adapters without an original field still work;
        # a missing slash is never invented merely because the event is awake.
        self.assertEqual(commands.parse_event(Event("/roll")), ("roll", "", False))
        self.assertIsNone(commands.parse_event(Event("roll")))

    def test_guard_and_monitor_agree_after_wake_prefix_removal(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(diagnostics, "LOG_FILE", str(Path(temp) / "log")), \
             patch("astrbot_plugin_hd2_guard.main.setting", side_effect=lambda *k, default=None: default):
            event = stripped_event("/roll")
            asyncio.run(Hd2MonitorPlugin(None).monitor(event))
            asyncio.run(Hd2GuardPlugin(None).guard(event))
            self.assertFalse(event.stopped)
            self.assertIn('"received"', Path(temp, "log").read_text(encoding="utf-8"))
            with patch("astrbot_plugin_hd2_guard.main.setting", side_effect=lambda *k, default=None: ["different-group"] if k[-1] == "groups" else default):
                asyncio.run(Hd2GuardPlugin(None).guard(event))
                self.assertTrue(event.stopped)

    def test_screenshot_commands_and_help_reach_their_handlers(self):
        async def collect(plugin, text):
            event = stripped_event(text)
            outputs = [item async for item in plugin.keyword_trigger(event)]
            self.assertTrue(event.stopped)
            return outputs
        with tempfile.TemporaryDirectory() as temp, patch.object(diagnostics, "LOG_FILE", str(Path(temp) / "log")), \
             patch.object(commands, "result", side_effect=lambda kind: SimpleNamespace(text="result " + kind, document={})), \
             patch.object(commands, "render_result", side_effect=ImportError("fixture")), \
             patch.object(roll, "draw", return_value={}) as draw, \
             patch.object(roll, "format_result", return_value="seven slots"), \
             patch.object(roll, "render", side_effect=ImportError("fixture")), \
             patch.object(lookup, "_render_help_card", None), \
             patch.object(lookup, "_build_help_text", return_value="help"):
            for command, kind in (("/api", "api"), ("/战报", "report")):
                self.assertEqual(asyncio.run(collect(Hd2WarReportPlugin(None), command)), [("text", "result " + kind)])
            self.assertEqual(asyncio.run(collect(roll.Hd2RollPlugin(None), "/roll")), [("text", "seven slots")])
            draw.assert_called_once_with("assessment")
            self.assertEqual(asyncio.run(collect(lookup.Hd2LookupPlugin(None), "/help")), [("text", "help")])

    def test_group_allowlist_keeps_platform_identity_namespaces_separate(self):
        settings = {"groups": [12345], "platform": "napcat"}
        with patch("astrbot_plugin_hd2_guard.main.setting", side_effect=lambda *k, default=None: settings.get(k[-1], default)):
            for platform in ("qq_official", "qq_official_webhook"):
                event = stripped_event("/roll")
                event.get_platform_name = lambda: platform
                asyncio.run(Hd2GuardPlugin(None).guard(event))
                self.assertFalse(event.stopped)
            event = stripped_event("/roll")  # NapCat: keep enforcing its own list.
            asyncio.run(Hd2GuardPlugin(None).guard(event))
            self.assertTrue(event.stopped)
            settings["platform"] = "qq_official"
            for allowed in (False, True):
                settings["groups"] = ["official-group-A" if allowed else "different-openid"]
                event = stripped_event("/roll")
                event.get_platform_name = lambda: "qq_official"
                asyncio.run(Hd2GuardPlugin(None).guard(event))
                self.assertEqual(event.stopped, not allowed)


if __name__ == "__main__":
    unittest.main()

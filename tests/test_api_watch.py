import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import urllib.error

from framework import Event, install

install()

from test_core import ROOT  # noqa: F401  (adds project root and plugins to sys.path)

from astrbot_plugin_hd2_common import api_watch, commands
from astrbot_plugin_hd2_war_report import main as war


ORIGIN_A = "HD2_QQbot:GroupMessage:" + "A" * 32
ORIGIN_B = "HD2_QQbot:GroupMessage:" + "B" * 32
ORIGIN_C = "HD2_QQbot:GroupMessage:" + "C" * 32
ORIGIN_D = "HD2_QQbot:GroupMessage:" + "D" * 32


async def collect(plugin, event):
    return [item async for item in plugin.keyword_trigger(event)]


def issue(body, title="HD2 API 内容变更", updated_at="2026-11-04T10:56:41Z"):
    value = {"title": title, "body": body, "updated_at": updated_at}
    value["revision"] = api_watch.issue_revision(value)
    return value


class ApiWatchTests(unittest.TestCase):
    def test_revision_ignores_detection_time_but_tracks_content(self):
        common = (
            "- 检测时间（UTC）：{stamp}\n"
            "- 当前银河效果定义：315 条\n\n"
            "## 变更：天园六IV\n\n"
            "### 移除的星球效果 ❌\n\n"
            "- `1434` — 毒瘴变种"
        )
        first = issue(common.format(stamp="2026-11-04T10:56:39Z"))
        later = issue(common.format(stamp="2026-11-04T11:56:39Z"))
        changed = issue(common.replace("1434", "1435").format(stamp="2026-11-04T11:56:39Z"))
        self.assertEqual(first["revision"], later["revision"])
        self.assertNotEqual(first["revision"], changed["revision"])
        parsed = api_watch.parse_issue60(first["body"])
        self.assertEqual(parsed["detected"], "2026-11-04T10:56:39Z")
        self.assertEqual(parsed["effect_count"], 315)
        self.assertEqual(parsed["groups"][0]["removed"], [("1434", "毒瘴变种")])

    def test_state_persists_subscribers_and_delivery_revisions(self):
        origin = ORIGIN_A
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "api_watch.json"
            state = api_watch.ApiWatchState(path)
            self.assertTrue(state.subscribe(origin, "old"))
            self.assertFalse(state.subscribe("napcat", "old"))
            self.assertFalse(state.observe(issue("first")))
            self.assertTrue(state.observe(issue("second")))
            current = state.last_revision()
            self.assertIn(origin, state.pending_sessions(current))
            state.mark_delivered(origin, current)

            restored = api_watch.ApiWatchState(path)
            self.assertEqual(restored.last_revision(), current)
            self.assertEqual(restored.pending_sessions(current), [])
            next_issue = issue("third")
            self.assertTrue(restored.observe(next_issue))
            self.assertEqual(restored.pending_sessions(next_issue["revision"]), [origin])

    def test_discovers_only_valid_api_sessions_from_existing_log(self):
        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "commands.jsonl"
            rows = [
                {"text": "/api", "platform": ORIGIN_A},
                {"text": "/api 文字", "platform": ORIGIN_B},
                {"text": "/战报", "platform": ORIGIN_C},
                {"text": "/api", "platform": "napcat"},
                {"text": "/api", "platform": "test_adapter:GroupMessage:GROUP-D"},
            ]
            log_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                                encoding="utf-8")
            state = api_watch.ApiWatchState(Path(temp) / "state.json")
            self.assertEqual(state.discover_from_log(log_path), 2)
            self.assertEqual(set(state.subscribers()), {
                ORIGIN_A,
                ORIGIN_B,
            })

    def test_etag_revalidation_reuses_cached_issue_without_rate_limit_growth(self):
        body = json.dumps({
            "title": "fixture",
            "body": "content",
            "updated_at": "2026-11-04T10:56:41Z",
        }).encode()
        response = io.BytesIO(body)
        response.headers = {"ETag": '"fixture-etag"'}
        response.__enter__ = lambda: response
        response.__exit__ = lambda *_args: None
        requests = []

        def open_url(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                return response
            raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", {}, None)

        with patch.object(api_watch, "_fetch_cache", {"etag": None, "payload": None, "ts": 0.0}), \
             patch.object(api_watch.urllib.request, "urlopen", side_effect=open_url):
            first = api_watch.fetch_issue60(force=True)
            second = api_watch.fetch_issue60(force=True)
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(requests[1].get_header("If-none-match"), '"fixture-etag"')


class ApiPushTests(unittest.TestCase):
    def make_plugin(self, temp, origin=ORIGIN_A):
        context = type("Context", (), {})()
        context.send_message = AsyncMock(return_value=True)
        plugin = war.Hd2WarReportPlugin(context)
        plugin._api_lock = asyncio.Lock()
        plugin._api_state = api_watch.ApiWatchState(Path(temp) / "state.json")
        plugin._api_state.subscribe(origin, "old")
        plugin._api_state.observe(issue("old body"))
        return plugin, origin

    def test_60_second_poll_pushes_once_per_session(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, origin = self.make_plugin(temp)
            current = issue("new body")
            with patch.object(api_watch, "fetch_issue60", return_value=current), \
                 patch.object(commands, "render_result", return_value=str(Path("fixture.png"))):
                asyncio.run(plugin._poll_api_watch())
                asyncio.run(plugin._poll_api_watch())
            plugin.context.send_message.assert_awaited_once()
            sent_origin, chain = plugin.context.send_message.await_args.args
            self.assertEqual(sent_origin, origin)
            self.assertEqual(chain.chain, [("image", str(Path("fixture.png")))])

    def test_failed_push_is_retried_on_next_poll(self):
        with tempfile.TemporaryDirectory() as temp:
            plugin, origin = self.make_plugin(temp)
            plugin.context.send_message.return_value = False
            current = issue("new body")
            with patch.object(api_watch, "fetch_issue60", return_value=current), \
                 patch.object(commands, "render_result", return_value=None):
                asyncio.run(plugin._poll_api_watch())
                asyncio.run(plugin._poll_api_watch())
            self.assertEqual(plugin.context.send_message.await_count, 2)
            self.assertEqual(
                plugin.context.send_message.await_args.args[1].chain[0][0],
                "message",
            )
            self.assertIn(origin, plugin._api_state.pending_sessions(current["revision"]))

    def test_first_poll_establishes_baseline_without_historical_spam(self):
        with tempfile.TemporaryDirectory() as temp:
            context = type("Context", (), {})()
            context.send_message = AsyncMock(return_value=True)
            plugin = war.Hd2WarReportPlugin(context)
            plugin._api_lock = asyncio.Lock()
            plugin._api_state = api_watch.ApiWatchState(Path(temp) / "state.json")
            plugin._api_state.subscribe(ORIGIN_A)
            current = issue("existing change")
            with patch.object(api_watch, "fetch_issue60", return_value=current):
                asyncio.run(plugin._poll_api_watch())
            plugin.context.send_message.assert_not_awaited()
            self.assertEqual(plugin._api_state.pending_sessions(current["revision"]), [])

    def test_api_command_registers_current_session(self):
        with tempfile.TemporaryDirectory() as temp:
            context = type("Context", (), {})()
            context.send_message = AsyncMock(return_value=True)
            plugin = war.Hd2WarReportPlugin(context)
            plugin._api_state = api_watch.ApiWatchState(Path(temp) / "state.json")
            event = Event("/api")
            event.unified_msg_origin = ORIGIN_A
            view = commands.Result(
                "api",
                "fixture",
                {"fetchedAt": "2026-11-04T10:56:41Z", "_issue_revision": "revision-a"},
            )
            with patch.object(commands, "result", return_value=view), \
                 patch.object(commands, "render_result", return_value="/tmp/api.png"), \
                 patch.object(war, "_log"):
                outputs = asyncio.run(collect(plugin, event))
            self.assertEqual(outputs, [("image", "/tmp/api.png")])
            self.assertIn(event.unified_msg_origin, plugin._api_state.subscribers())


if __name__ == "__main__":
    unittest.main()

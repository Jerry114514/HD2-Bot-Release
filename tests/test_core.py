import asyncio
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))
from astrbot_plugin_hd2_common import data, commands
from astrbot_plugin_hd2_common.runtime import blocking


def load_console():
    spec = importlib.util.spec_from_file_location("test_console", ROOT / "scripts/hd2_webui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot():
    return {"fetchedAt": "2026-10-30T01:00:00Z", "impactMultiplier": .001,
            "assignments": {"id": 1, "tasks": [{"type": 11, "values": [1], "valueTypes": [3]}], "progress": [0]},
            "major_order": {"translated_brief": "测试指令"},
            "planets": [{"index": 45, "name": "X-45", "currentOwner": "Humans", "health": 1000000,
                         "maxHealth": 1000000, "players": 100, "resistance": 1}],
            "planet_events": [{"planetIndex": 45, "eventType": 1, "health": 750000, "maxHealth": 1000000,
                               "endTime": "2026-10-31T01:00:00Z"}],
            "campaigns": [{"type": 1, "faction": "Automaton", "planet": {"index": 45}}]}


class DataTests(unittest.TestCase):
    def test_units_and_unknown(self):
        self.assertEqual(data.fmt_big_num(1000000), "100万")
        self.assertEqual(data.fmt_big_num(1250000000), "12.5亿")
        self.assertIsNone(data.percent(None, 1))
        self.assertEqual(data.percent(0, 1), 0)

    def test_defense_uses_event_and_keeps_zero_rate(self):
        doc = snapshot()
        doc["campaigns"][0]["defendersRate"] = 0
        state = data.campaign_state(doc, doc["planets"][0])
        self.assertEqual(state["progress"], 25)
        self.assertEqual(state["rate"], 0)
        self.assertTrue(state["measured"])
        self.assertEqual(state["status"], "防御战役中")
        self.assertIn("25.0%", commands.result("planet", "x-45", doc).text)
        self.assertIn("防御截止", commands.result("campaigns", doc=doc).text)

    def test_region_keeps_outer_timestamp_and_measures(self):
        frames = {"data": [
                {"timestampUtc": "2026-10-30T00:00:00Z", "planetRegions": [{"planetIndex":45,"regionIndex":0,"owner":3,"health":80000,"regerPerSecond":0}]},
                {"timestampUtc": "2026-10-30T01:00:00Z", "planetRegions": [{"planetIndex":45,"regionIndex":0,"owner":3,"health":70000,"regerPerSecond":0}]}]}
        with patch.object(data, "get_json", return_value=frames), patch.object(data.time, "time", return_value=data.timestamp("2026-10-30T01:10:00Z")):
            self.assertEqual(data.region_rate({"index":45}, {"regionIndex":0,"owner":3,"maxHealth":100000,"players":0}, 0), (10., 0., True))
        with patch.object(data, "region_frames", return_value=None):
            self.assertEqual(data.region_rate({"index":45}, {"regionIndex":0,"owner":3,"maxHealth":100000,"players":0}, 0), (None, None, False))

    def test_snapshot_age_and_task_identity(self):
        doc = snapshot()
        with tempfile.TemporaryDirectory() as temp, patch.object(data, "CACHE", Path(temp)), patch.object(data, "get_json", return_value=doc):
            with patch.object(data.time, "time", return_value=data.timestamp(doc["fetchedAt"]) + 60):
                first = data.site_doc()
                self.assertFalse(first["_hd2"]["stale"])
                doc["assignments"]["id"] = 2
                doc["major_order"] = {"title": "新任务", "translated_brief": ""}
                second = data.site_doc(force=True)
                self.assertNotIn("测试指令", commands.result("mo", doc=second).text)
            with patch.object(data.time, "time", return_value=data.timestamp(doc["fetchedAt"]) + 100000):
                with self.assertRaisesRegex(RuntimeError, "过期"):
                    data.site_doc()

    def test_exact_routes(self):
        self.assertEqual(commands.parse("/wiki 战报"), ("wiki", "战报", False))
        self.assertEqual(commands.parse("/wiki roll"), ("wiki", "roll", False))
        self.assertEqual(commands.parse("@机器人 /战报t"), ("report", "", True))
        self.assertEqual(commands.parse("/查询 X-45"), ("planet", "X-45", False))
        self.assertIsNone(commands.parse("/foorollbar"))
        self.assertIsNone(commands.parse("123"))

    def test_blocking_does_not_stop_loop(self):
        async def probe():
            tick = asyncio.Event()
            async def clock():
                await asyncio.sleep(.01)
                tick.set()
            task = asyncio.create_task(clock())
            await blocking(time.sleep, .05)
            self.assertTrue(tick.is_set())
            await task
        asyncio.run(probe())


class ConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = load_console()

    def test_bad_target_never_falls_back(self):
        with patch.object(self.web, "_default_group", return_value=12345):
            self.assertEqual(self.web._target_group(None), 12345)
            for raw in ("abc12-345", "-12345", "0", "foo"):
                with self.assertRaises(ValueError):
                    self.web._target_group(raw)

    def test_concurrent_sends_have_independent_content(self):
        barrier = threading.Barrier(2)
        deliveries = {}
        def sender(args, **kwargs):
            barrier.wait(timeout=3)
            deliveries[args[-1]] = Path(args[-2]).read_text(encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="RESULT: OK", stderr="")
        with tempfile.TemporaryDirectory() as temp, patch.object(self.web, "TEMP", temp), patch.object(self.web, "run_sub", side_effect=sender):
            threads = [threading.Thread(target=self.web.send_msg, args=(text,gid)) for text,gid in (("甲",12345),("乙",23456))]
            for t in threads:t.start()
            for t in threads:t.join(timeout=4)
            self.assertEqual(deliveries, {"12345":"甲","23456":"乙"})
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_failed_generation_never_sends_old_report(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(self.web, "TEMP", temp), patch.object(self.web, "_target_group", return_value=12345), patch.object(self.web, "send_msg") as send:
            Path(temp,"report_live2.txt").write_text("旧报告",encoding="utf-8")
            with patch.object(self.web,"run_sub",return_value=types.SimpleNamespace(returncode=1,stdout="",stderr="failed")):
                self.assertIn("❌", self.web.action_report())
                self.assertIn("❌", self.web.action_push_all_report())
                send.assert_not_called()

    def test_real_http_requires_trusted_origin_and_session(self):
        calls=[]
        with patch.dict(self.web.ACTIONS, {"probe":lambda:calls.append(1) or "ok"}):
            server=ThreadingHTTPServer(("127.0.0.1",0),self.web.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for headers,status in [({},403),({"X-HD2-CSRF":self.web.CSRF_TOKEN,"Origin":"https://evil.invalid"},403),({"X-HD2-CSRF":self.web.CSRF_TOKEN},200)]:
                    conn=HTTPConnection("127.0.0.1",server.server_port,timeout=2)
                    conn.request("POST","/api/action",json.dumps({"action":"probe"}),{"Content-Type":"application/json",**headers})
                    response=conn.getresponse();self.assertEqual(response.status,status);response.read();conn.close()
                self.assertEqual(calls,[1])
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__ == "__main__":
    unittest.main()

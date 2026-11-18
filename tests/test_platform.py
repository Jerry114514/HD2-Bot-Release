import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from test_core import ROOT,load_console
import hd2_config as cfg
from scripts import instance, launch


class PlatformTests(unittest.TestCase):
    def test_official_start_skips_snowluma_and_waits(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp,"config.json");path.write_text('{"bot":{"platform":"qq_official"}}')
            with patch.dict("os.environ",{"HD2_CONFIG":str(path)}),patch.object(launch,"start_astrbot",return_value="ready"),patch.object(launch,"start_webui",return_value="ready"),patch.object(launch,"start_snowluma") as snowluma,patch.object(launch.time,"sleep"),patch.object(launch.webbrowser,"open"),contextlib.redirect_stdout(io.StringIO()):
                launch.main();snowluma.assert_not_called()

    def test_wrong_port_owner_is_never_selected(self):
        app = ROOT / "fixture App"
        python = str(app / "python.exe")
        backend = str(app / "launch_backend.py")
        def get(*keys,default=None):
            return {("astrbot","exe"):str(app / "astrbot.exe"),
                    ("astrbot","python"):python,("astrbot","app_dir"):str(app)}.get(keys,default)
        with patch.object(cfg,"get",side_effect=get):
            for command in ({"CommandLine":f'"{python}" "{backend}"'}, {"Argv":[python,backend]}):
                records=[{"ProcessId":1,"ExecutablePath":python,**command},
                         {"ProcessId":2,"ExecutablePath":str(ROOT / "Elsewhere" / "python.exe"),
                          "CommandLine":"AstrBot launch_backend.py"}]
                self.assertEqual(instance.select(records,[1],"astrbot"),[1])
                self.assertEqual(instance.select(records,[1,1],"astrbot"),[1])
                with self.assertRaises(RuntimeError):instance.select(records,[2],"astrbot")
                with self.assertRaises(RuntimeError):instance.select(records,[999],"astrbot")
        with patch.object(cfg,"get",return_value=""):
            self.assertFalse(instance.matches({"ExecutablePath":None},"napcat"))

    def test_official_push_refused_before_sender(self):
        web=load_console()
        with patch.object(instance,"platform",return_value="qq_official"),patch.object(web,"run_sub") as sender:
            with self.assertRaises(ValueError):web.send_msg("fixture","opaque-id")
            sender.assert_not_called()

    def test_timeout_is_unknown_not_failed_or_retried(self):
        import subprocess
        web=load_console()
        with tempfile.TemporaryDirectory() as temp,patch.object(web,"TEMP",temp),patch.object(web,"_target_group",return_value=12345),patch.object(web,"hd2_log") as log,patch.object(web,"run_sub",side_effect=subprocess.TimeoutExpired("mock",1)) as send:
            self.assertIn("结果不明",web.send_msg("fixture"))
            self.assertEqual(send.call_count,1)
            self.assertEqual(log.call_args.args[0],"unknown")

    def test_invalid_config_raises_and_defaults_not_mutated(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp,"config.json");path.write_text('{"bot":{"groups":[12345]}}')
            with patch.dict("os.environ",{"HD2_CONFIG":str(path)}):
                cfg.load_config(force=True)["bot"]["groups"].append(23456)
                self.assertEqual(cfg.DEFAULTS["bot"]["groups"],[])
                path.write_text('{"bot":{"groups":[]}}')
                self.assertEqual(cfg.get("bot","groups"),[])
                path.write_text("not json")
                with self.assertRaises(RuntimeError):cfg.load_config(force=True)


class GroupManagementTests(unittest.TestCase):
    """任务 E：控制台「群号管理」只维护本项目 config.json 的 bot.groups，不再读写旧版 platform_whitelist.json"""

    def _fixture(self, root, legacy=None):
        data = root / "data"
        data.mkdir()
        if legacy is not None:
            (data / "platform_whitelist.json").write_text(json.dumps(legacy), encoding="utf-8")
        path = root / "config.json"
        path.write_text(json.dumps({"bot": {"platform": "napcat", "platform_id": "selected",
                                            "groups": [12345], "default_group": 12345},
                                    "astrbot": {"data_dir": str(data)}}), encoding="utf-8")
        return data, path

    def test_group_management_only_touches_bot_groups(self):
        web = load_console()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, path = self._fixture(root, legacy={"selected": ["12345"], "other": ["abc-official"]})
            legacy = data / "platform_whitelist.json"
            legacy_bytes = legacy.read_bytes()
            with patch.dict("os.environ", {"HD2_CONFIG": str(path)}), patch.object(web, "TEMP", temp):
                self.assertIn("67890", web.action_group_add("67890"))
                saved = json.loads(path.read_text(encoding="utf-8"))["bot"]
                self.assertEqual(saved["groups"], [12345, 67890])
                self.assertEqual(saved["default_group"], 12345, "添加群不应抢占默认群")
                self.assertEqual(legacy.read_bytes(), legacy_bytes, "旧白名单文件不应被写入")

                info = json.loads(web.action_group_list())
                self.assertEqual(info["groups"], ["12345", "67890"])
                self.assertEqual(info["source_kind"], "本项目 config.json 的 bot.groups（推送群 / 指令守卫范围）")
                self.assertTrue(info["legacy"]["exists"])
                self.assertEqual(info["legacy"]["adapter"], "selected")
                self.assertEqual(info["legacy"]["groups"], ["12345"])
                self.assertIn("AstrBot WebUI", info["framework_whitelist"])
                self.assertIn("platform_settings.id_whitelist", info["framework_whitelist"])
                self.assertEqual(legacy.read_bytes(), legacy_bytes)

                self.assertTrue(web.action_group_remove("12345").startswith("✅"))
                saved = json.loads(path.read_text(encoding="utf-8"))["bot"]
                self.assertEqual(saved["groups"], [67890])
                self.assertEqual(saved["default_group"], 67890, "默认群被移除后应回落到剩余首个群")
                self.assertEqual(legacy.read_bytes(), legacy_bytes)
                self.assertTrue(list(root.glob("config_backups/*")), "改配置前应留下备份")

                self.assertTrue(web.action_group_default("67890").startswith("✅"))
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["bot"]["default_group"], 67890)
                self.assertTrue(web.action_group_remove("67890").startswith("✅"))
                saved = json.loads(path.read_text(encoding="utf-8"))["bot"]
                self.assertEqual(saved["groups"], [])
                self.assertEqual(saved["default_group"], 0, "群列表清空后默认群应归零")
                self.assertEqual(legacy.read_bytes(), legacy_bytes)

    def test_legacy_migration_merges_and_never_rewrites_legacy_file(self):
        web = load_console()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, path = self._fixture(root, legacy={"selected": ["12345", "23456", "oops"], "other": ["99999"]})
            legacy = data / "platform_whitelist.json"
            legacy_bytes = legacy.read_bytes()
            with patch.dict("os.environ", {"HD2_CONFIG": str(path)}), patch.object(web, "TEMP", temp):
                output = web.action_group_migrate()
                self.assertIn("23456", output)
                self.assertIn("oops", output, "非数字群号应被跳过并说明")
                self.assertIn("旧文件保持原样", output)
                saved = json.loads(path.read_text(encoding="utf-8"))["bot"]
                self.assertEqual(saved["groups"], [12345, 23456])
                self.assertEqual(saved["default_group"], 12345)
                self.assertEqual(legacy.read_bytes(), legacy_bytes, "迁移只读旧文件，绝不回写")

                again = web.action_group_migrate()
                self.assertIn("未修改配置", again, "重复迁移应幂等且提示无需改动")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["bot"]["groups"], [12345, 23456])
                self.assertEqual(legacy.read_bytes(), legacy_bytes)

    def test_group_list_without_legacy_or_data_dir(self):
        web = load_console()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "config.json"
            path.write_text(json.dumps({"bot": {"platform": "napcat", "platform_id": "selected", "groups": []}}),
                            encoding="utf-8")
            with patch.dict("os.environ", {"HD2_CONFIG": str(path)}), patch.object(web, "TEMP", temp):
                info = json.loads(web.action_group_list())
                self.assertFalse(info["legacy"]["exists"])
                self.assertEqual(info["legacy"]["path"], "", "未配置 astrbot.data_dir 时不猜测数据目录")
                self.assertIn("AstrBot WebUI", info["framework_whitelist"])
                self.assertIn("未发现", web.action_group_migrate())

    def test_http_group_add_forwards_value(self):
        web = load_console()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, path = self._fixture(root)
            with patch.dict("os.environ", {"HD2_CONFIG": str(path)}), patch.object(web, "TEMP", temp):
                server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    conn.request("POST", "/api/action", json.dumps({"action": "group_add", "value": "67890"}),
                                 {"Content-Type": "application/json", "X-HD2-CSRF": web.CSRF_TOKEN})
                    response = conn.getresponse()
                    body = response.read()
                    conn.close()
                    self.assertEqual(response.status, 200, body)
                    self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["bot"]["groups"], [12345, 67890])
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join()


if __name__=="__main__":unittest.main()

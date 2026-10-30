import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from test_core import ROOT,load_console
import hd2_config as cfg
from scripts import instance, launch


class PlatformTests(unittest.TestCase):
    def test_official_start_skips_napcat_and_waits(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp,"config.json");path.write_text('{"bot":{"platform":"qq_official"}}')
            with patch.dict("os.environ",{"HD2_CONFIG":str(path)}),patch.object(launch,"start_astrbot",return_value="ready"),patch.object(launch,"start_webui",return_value="ready"),patch.object(launch,"start_napcat") as napcat,patch.object(launch.time,"sleep"),patch.object(launch.webbrowser,"open"),contextlib.redirect_stdout(io.StringIO()):
                launch.main();napcat.assert_not_called()

    def test_wrong_port_owner_is_never_selected(self):
        def get(*keys,default=None):
            return {("astrbot","exe"):r"D:\App\astrbot.exe",("astrbot","python"):r"D:\App\python.exe",("astrbot","app_dir"):r"D:\App"}.get(keys,default)
        with patch.object(cfg,"get",side_effect=get):
            records=[{"ProcessId":1,"ExecutablePath":r"D:\App\python.exe","CommandLine":r'"D:\App\python.exe" "D:\App\launch_backend.py"'},
                     {"ProcessId":2,"ExecutablePath":r"D:\Elsewhere\python.exe","CommandLine":"AstrBot launch_backend.py"}]
            self.assertEqual(instance.select(records,[1],"astrbot"),[1])
            self.assertEqual(instance.select(records,[1,1],"astrbot"),[1])
            with self.assertRaises(RuntimeError):instance.select(records,[2],"astrbot")
            with self.assertRaises(RuntimeError):instance.select(records,[999],"astrbot")
        with patch.object(cfg,"get",return_value=""):
            self.assertFalse(instance.matches({"ExecutablePath":None},"napcat"))

    def test_platform_whitelist_changes_only_selected_key_and_clears_last_default(self):
        web=load_console()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=root/'data';target.mkdir()
            whitelist=target/'platform_whitelist.json';whitelist.write_text(json.dumps({"selected":["12345"],"other":["abc-official"]}))
            path=root/'config.json';path.write_text(json.dumps({"bot":{"platform":"napcat","platform_id":"selected","groups":[12345],"default_group":12345},"astrbot":{"data_dir":str(target)}}))
            with patch.dict("os.environ",{"HD2_CONFIG":str(path)}),patch.object(web,"TEMP",temp):
                output=web.action_group_remove("12345")
                self.assertTrue(output.startswith("✅"),output)
                self.assertEqual(json.loads(whitelist.read_text()),{"selected":[],"other":["abc-official"]})
                self.assertEqual(json.loads(path.read_text())["bot"]["default_group"],0)
                self.assertTrue(list(root.glob("config_backups/*")))
                with self.assertRaises(ValueError):web._target_group(None)

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


if __name__=="__main__":unittest.main()

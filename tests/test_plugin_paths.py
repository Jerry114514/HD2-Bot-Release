import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

import astrbot_plugin_hd2_common.paths as paths  # noqa: E402


class PluginPathsTests(unittest.TestCase):
    """插件数据目录解析：必须落在 data/plugin_data 下，且优先使用 AstrBot 官方接口。"""

    def setUp(self):
        self._saved_env = {key: os.environ.get(key) for key in ("HD2_PLUGIN_DATA_DIR", "ASTRBOT_ROOT")}
        self._saved_modules = {
            key: sys.modules.get(key) for key in ("astrbot", "astrbot.api", "astrbot.api.star")
        }
        for key in ("HD2_PLUGIN_DATA_DIR", "ASTRBOT_ROOT"):
            os.environ.pop(key, None)
        paths._RESOLVED.clear()

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for key, value in self._saved_modules.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
        paths._RESOLVED.clear()

    def test_explicit_env_override_selects_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HD2_PLUGIN_DATA_DIR"] = tmp
            self.assertEqual(Path(tmp), paths.data_root())

    def test_astrbot_root_env_maps_to_data_plugin_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ASTRBOT_ROOT"] = tmp
            self.assertEqual(Path(tmp) / "data" / "plugin_data", paths.data_root())

    def test_repo_layout_falls_back_to_data_plugin_data(self):
        root = paths.data_root()
        self.assertEqual(["data", "plugin_data"], list(root.parts[-2:]))

    def test_framework_star_tools_is_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            class StarTools:
                @classmethod
                def get_data_dir(cls, plugin_name=None):
                    calls.append(plugin_name)
                    target = Path(tmp) / "plugin_data" / plugin_name
                    target.mkdir(parents=True, exist_ok=True)
                    return str(target)

            star = types.ModuleType("astrbot.api.star")
            star.StarTools = StarTools
            sys.modules["astrbot"] = types.ModuleType("astrbot")
            sys.modules["astrbot.api"] = types.ModuleType("astrbot.api")
            sys.modules["astrbot.api.star"] = star

            path = paths.plugin_data_dir("astrbot_plugin_hd2_wiki")
            self.assertEqual(Path(tmp) / "plugin_data" / "astrbot_plugin_hd2_wiki", path)
            self.assertTrue(path.is_dir())
            self.assertEqual(["astrbot_plugin_hd2_wiki"], calls)

    def test_explicit_env_override_beats_framework(self):
        with tempfile.TemporaryDirectory() as tmp:
            star = types.ModuleType("astrbot.api.star")

            class StarTools:
                @classmethod
                def get_data_dir(cls, plugin_name=None):
                    raise AssertionError("env override must short-circuit StarTools")

            star.StarTools = StarTools
            sys.modules["astrbot"] = types.ModuleType("astrbot")
            sys.modules["astrbot.api"] = types.ModuleType("astrbot.api")
            sys.modules["astrbot.api.star"] = star
            os.environ["HD2_PLUGIN_DATA_DIR"] = tmp
            self.assertEqual(Path(tmp) / "astrbot_plugin_hd2_btk", paths.plugin_data_dir("astrbot_plugin_hd2_btk"))

    def test_cache_and_log_helpers_live_under_plugin_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HD2_PLUGIN_DATA_DIR"] = tmp
            self.assertEqual(Path(tmp, "p", "cache"), paths.plugin_cache_dir("p", create=True))
            self.assertEqual(Path(tmp, "p", "logs"), paths.plugin_log_dir("p"))
            self.assertEqual(Path(tmp, "p", "cache", "banners"), paths.plugin_cache_dir("p", "banners", create=True))

    def test_empty_plugin_name_is_rejected(self):
        with self.assertRaises(ValueError):
            paths.plugin_data_dir("")

    def test_runtime_constants_never_point_into_plugin_install_dir(self):
        import astrbot_plugin_hd2_common.data as common_data
        import astrbot_plugin_hd2_common.diagnostics as diagnostics

        install_dir = str((ROOT / "plugins").resolve())
        for label, value in (("data.CACHE", common_data.CACHE), ("diagnostics.LOG_FILE", diagnostics.LOG_FILE)):
            resolved = os.path.abspath(str(value))
            self.assertFalse(resolved.startswith(install_dir), f"{label} 仍写在插件安装目录: {resolved}")
            self.assertIn("plugin_data", resolved, label)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""新旧配置兼容测试：旧 config.json（无新键）可启动，新键有默认值，环境变量覆盖生效"""
import json
import os
import tempfile
import unittest
from unittest import mock

from conftest import ROOT  # noqa: F401
import hd2_config as cfg


class TestConfigCompat(unittest.TestCase):
    def test_defaults_have_new_data_keys(self):
        cfg.load_config(force=True)
        data = cfg.get("data")
        for key in ("site_base", "news_json_url", "recent_base", "recent_fresh_max_hours",
                    "transnews_url", "campaign_zh_url", "tables_base",
                    "official_api_url", "super_client", "news_source", "news_cache_ttl",
                    "translate_url", "planets_url", "live_api_url", "extended_api_url"):
            self.assertIn(key, data, f"默认配置缺少 data.{key}")

    def test_old_config_still_loads(self):
        """旧版 config.json（只有 bot/napcat，没有任何 data.* 键）也能启动并补齐默认值"""
        old = {"bot": {"self_id": "123", "groups": [1]}, "napcat": {"token": "x"}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(old, f)
            path = f.name
        try:
            with mock.patch.dict(os.environ, {"HD2_CONFIG": path}):
                c = cfg.load_config(force=True)
                self.assertEqual(c["bot"]["self_id"], "123")
                # 旧文件没有 data 段 -> 默认值补齐
                self.assertEqual(c["data"]["news_source"], "hd2map")
                self.assertIn("recent_base", c["data"])
                # 旧键也保留
                self.assertEqual(c["napcat"]["token"], "x")
        finally:
            os.unlink(path)

    def test_partial_override_merges(self):
        """config 只覆盖部分键时，其余键仍是默认值（深合并）"""
        part = {"data": {"news_source": "api"}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(part, f)
            path = f.name
        try:
            with mock.patch.dict(os.environ, {"HD2_CONFIG": path}):
                c = cfg.load_config(force=True)
                self.assertEqual(c["data"]["news_source"], "api")
                self.assertEqual(c["data"]["news_cache_ttl"], 120)
        finally:
            os.unlink(path)

    def test_get_keypath_missing(self):
        self.assertIsNone(cfg.get("data", "not_exist_key", default=None))
        self.assertEqual(cfg.get("data", "news_source"), "hd2map")

    def test_corrupt_config_falls_back_to_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{broken")
            path = f.name
        try:
            with mock.patch.dict(os.environ, {"HD2_CONFIG": path}):
                c = cfg.load_config(force=True)
                self.assertEqual(c["data"]["news_source"], "hd2map")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

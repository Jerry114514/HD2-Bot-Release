# -*- coding: utf-8 -*-
"""格式化工具与新闻缓存优先级测试"""
import unittest

from conftest import ROOT  # noqa: F401
import hd2_datasource as ds
import astrbot_plugin_hd2_war_report.main as wr


class TestFmtHelpers(unittest.TestCase):
    def test_big_num(self):
        self.assertEqual(ds.fmt_big_num(1_000_000), "100万")
        self.assertEqual(ds.fmt_big_num(1_250_000_000), "12.5亿")
        self.assertEqual(ds.fmt_big_num(12345), "1.2万")
        self.assertEqual(ds.fmt_big_num(999), "999")

    def test_seconds(self):
        self.assertEqual(ds.fmt_seconds(90061), "1天1小时")
        self.assertEqual(ds.fmt_seconds(3660), "1小时1分")
        self.assertEqual(ds.fmt_seconds(59), "0分钟")
        self.assertEqual(ds.fmt_seconds(-5), "0分钟")

    def test_utc8(self):
        self.assertEqual(ds.fmt_utc8("2026-10-24T00:30:00Z"), "10-24 08:30")
        self.assertEqual(ds.fmt_utc8(""), "")
        self.assertEqual(ds.fmt_utc8("bad"), "bad")


class TestNewsCachePriority(unittest.TestCase):
    """站点译文（site）权威：可覆盖 LLM 旧译文；LLM 永不覆盖 site"""

    def test_site_overrides_llm(self):
        old = wr._news_rec_make("旧 LLM 译文", "llm", "")
        self.assertTrue(wr._news_should_update(old, "站点新译文", "site", "2026-10-24T00:00:00Z"))

    def test_site_version_bump(self):
        old = wr._news_rec_make("站点译文", "site", "2026-10-23T00:00:00Z")
        self.assertTrue(wr._news_should_update(old, "站点译文改", "site", "2026-10-24T00:00:00Z"))
        self.assertFalse(wr._news_should_update(old, "站点译文改", "site", "2026-10-23T00:00:00Z"))

    def test_llm_never_overrides_site(self):
        old = wr._news_rec_make("站点译文", "site", "2026-10-23T00:00:00Z")
        self.assertFalse(wr._news_should_update(old, "LLM 另一种译法", "llm", ""))

    def test_same_content_no_write(self):
        old = wr._news_rec_make("同文", "site", "v1")
        self.assertFalse(wr._news_should_update(old, "同文", "site", "v1"))

    def test_empty_updates(self):
        self.assertTrue(wr._news_should_update(None, "新译文", "site", ""))
        self.assertTrue(wr._news_should_update({}, "新译文", "site", ""))


class TestTransNewsFallbackItems(unittest.TestCase):
    def test_transnews_items_shape(self):
        orig = ds.transnews
        ds.transnews = lambda force=False: {
            "items": [{"id": 3960, "original": "EN", "translated": "中文",
                       "published_at": "2026-10-20T09:09:25Z", "translated_at": "v"}]
        }
        try:
            items = wr._transnews_feed_items(5)
        finally:
            ds.transnews = orig
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], 3960)
        self.assertEqual(items[0]["_zh"], "中文")

    def test_transnews_unavailable_returns_empty(self):
        orig = ds.transnews
        ds.transnews = lambda force=False: {}
        try:
            self.assertEqual(wr._transnews_feed_items(5), [])
        finally:
            ds.transnews = orig


if __name__ == "__main__":
    unittest.main()

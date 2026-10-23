# -*- coding: utf-8 -*-
"""主站 data.json 字段映射测试：字段缺失/空数组/类型异常都不抛出，归一化正确"""
import json
import os
import unittest

from conftest import ROOT  # noqa: F401  (sys.path 引导副作用)
import hd2_datasource as ds

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "site_doc.json")


def load_fixture() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


class TestFieldMapping(unittest.TestCase):
    def setUp(self):
        self.doc = load_fixture()

    def test_player_distribution(self):
        pd = ds.player_distribution(self.doc)
        self.assertIsInstance(pd.get("total"), int)
        self.assertGreater(pd["total"], 0)
        for k in ("humans", "terminids", "automatons", "illuminate"):
            self.assertIsInstance(pd.get(k), int)

    def test_planet_map_owner_normalization(self):
        pm = ds.planet_map(self.doc)
        self.assertIn("7", pm)
        p7 = pm["7"]
        self.assertEqual(p7["name"], "ZEA RUGOSIA")
        # 站点字符串 owner -> 数字枚举
        self.assertEqual(p7["owner"], 4)
        self.assertIsInstance(p7["resistance"], float)
        self.assertIsInstance(p7["activeEffects"], list)
        self.assertIsInstance(p7["regions"], list)

    def test_planet_map_keeps_regions(self):
        pm = ds.planet_map(self.doc)
        p7 = pm["7"]
        self.assertTrue(p7["regions"], "夹具星球 7 应带 regions")
        r0 = p7["regions"][0]
        for k in ("regionIndex", "owner", "health", "isAvailable"):
            self.assertIn(k, r0)

    def test_mo_assignment_fields(self):
        a = ds.mo_assignment(self.doc)
        self.assertTrue(a)
        self.assertIn("briefing", a)
        self.assertIn("tasks", a)
        self.assertIn("deadline", a)
        self.assertIsInstance(a.get("progress"), list)

    def test_news_zh_map(self):
        m = ds.news_zh_map(self.doc)
        self.assertTrue(m)
        nid = next(iter(m))
        self.assertTrue(m[nid]["text"])
        self.assertIn("ver", m[nid])

    def test_mo_brief_zh(self):
        self.assertTrue(ds.mo_brief_zh(self.doc))
        self.assertEqual(ds.mo_brief_zh({"major_order": {}}), "")

    def test_active_campaign_chinese(self):
        ac = ds.active_campaign(self.doc)
        self.assertTrue(ac.get("title_cn"))
        self.assertTrue(ac.get("description_cn"))
        ph = (ac.get("phases") or [{}])[0]
        self.assertIn("briefing_cn", ph)

    def test_dss_fields(self):
        d = ds.dss(self.doc)
        self.assertIn("planetIndex", d)
        self.assertIn("tacticalActions", d)

    def test_missing_and_empty_are_safe(self):
        empty = {}
        self.assertEqual(ds.player_distribution(empty), {})
        self.assertEqual(ds.planet_map(empty), {})
        self.assertEqual(ds.campaigns(empty), [])
        self.assertEqual(ds.mo_assignment(empty), {})
        self.assertEqual(ds.mo_brief_zh(empty), "")
        self.assertEqual(ds.news_zh_map(empty), {})
        self.assertEqual(ds.active_campaign(empty), {})
        self.assertEqual(ds.dss(empty), {})
        self.assertIsNone(ds.ownership_history({}).get("events"))

    def test_garbage_types_are_safe(self):
        bad = {"player_distribution": "x", "planets": "y", "campaigns": 3,
               "assignments": [1, 2], "news": "no", "dss": None}
        self.assertEqual(ds.player_distribution(bad), {})
        self.assertEqual(ds.planet_map(bad), {})
        self.assertEqual(ds.campaigns(bad), [])
        self.assertEqual(ds.mo_assignment(bad), {})
        self.assertEqual(ds.news_zh_map(bad), {})
        self.assertEqual(ds.dss(bad), {})


if __name__ == "__main__":
    unittest.main()

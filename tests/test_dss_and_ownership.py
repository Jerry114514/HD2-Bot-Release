# -*- coding: utf-8 -*-
"""DSS 捐献与星图归属变化测试（dss-donations.js / ownership-changes.js 口径移植）"""
import unittest

from conftest import ROOT  # noqa: F401
import hd2_datasource as ds


class TestDssDonations(unittest.TestCase):
    def test_donation_pct(self):
        a = {"cost": [{"itemMixId": 3992382197, "target": 86400, "current": 43200}]}
        self.assertAlmostEqual(ds.dss_donation_pct(a), 50.0, places=6)

    def test_donation_pct_missing_cost(self):
        self.assertIsNone(ds.dss_donation_pct({}))
        self.assertIsNone(ds.dss_donation_pct({"cost": []}))
        self.assertIsNone(ds.dss_donation_pct({"cost": [{"itemMixId": 1, "target": 0, "current": 5}]}))

    def test_resource_lines_limits(self):
        a = {"cost": [{"itemMixId": 3992382197, "target": 86400, "current": 30845.562,
                       "deltaPerSec": 0.101, "maxDonation": 75, "maxDonationPeriodSec": 86400}]}
        lines = ds.dss_resource_lines(a)
        self.assertEqual(len(lines), 1)
        self.assertIn("普通样本", lines[0])
        self.assertIn("75", lines[0])
        self.assertIn("24小时", lines[0])

    def test_resource_lines_unknown_mix(self):
        lines = ds.dss_resource_lines({"cost": [{"itemMixId": 12345, "target": 10, "current": 1}]})
        self.assertEqual(lines, ["捐赠资源待确认"])

    def test_snapshot_label(self):
        orig_dss = ds.dss
        ds.dss = lambda doc=None: {}   # 隔离：不读真实站点数据
        try:
            label = ds.dss_snapshot_label({"donationUpdatedAtMs": 1792771268974})
            self.assertTrue(label.startswith("捐赠数据截至"))
            self.assertNotIn("未知", label)
            self.assertEqual(ds.dss_snapshot_label({}), "捐赠数据时间未知")
            self.assertEqual(ds.dss_snapshot_label({"donationUpdatedAtMs": 0}), "捐赠数据时间未知")
        finally:
            ds.dss = orig_dss

    def test_status_cn_table(self):
        self.assertEqual(ds.TACTICAL_STATUS_CN[1], "投票中")
        self.assertEqual(ds.TACTICAL_STATUS_CN[2], "已激活")
        self.assertEqual(ds.TACTICAL_STATUS_CN[3], "恢复中")

    def test_resources_map(self):
        self.assertEqual(ds.DSS_RESOURCES[3992382197], "普通样本")
        self.assertEqual(ds.DSS_RESOURCES[2985106497], "稀有样本")
        self.assertEqual(ds.DSS_RESOURCES[3608481516], "申购点")


class TestOwnershipChanges(unittest.TestCase):
    def setUp(self):
        self._orig = ds.ownership_history

    def tearDown(self):
        ds.ownership_history = self._orig

    def _patch(self, h):
        ds.ownership_history = lambda doc=None: h

    FIX = {
        "startedAt": "2026-10-23T00:00:00+08:00",
        "updatedAt": "2026-10-24T00:00:00+08:00",
        "events": [
            {"detectedAt": "2026-10-23T10:00:00+08:00", "planetIndex": 7,
             "fromOwner": "Humans", "toOwner": "Illuminate", "previousSeenAt": "2026-10-23T09:00:00+08:00"},
            {"detectedAt": "2026-10-23T12:00:00+08:00", "planetIndex": 7,
             "fromOwner": "Illuminate", "toOwner": "Humans", "previousSeenAt": "2026-10-23T10:30:00+08:00"},
            {"detectedAt": "2026-10-23T13:00:00+08:00", "planetIndex": 99,
             "fromOwner": "Terminids", "toOwner": "Humans", "previousSeenAt": "2026-10-22T01:00:00+08:00"},
            {"detectedAt": "2026-10-20T13:00:00+08:00", "planetIndex": 1,
             "fromOwner": "Humans", "toOwner": "Illuminate", "previousSeenAt": "2026-10-19T00:00:00+08:00"},
        ],
        "defenseStartedAt": "2026-10-23T00:00:00+08:00",
        "defenseUpdatedAt": "2026-10-24T00:00:00+08:00",
        "defenseEvents": [
            {"detectedAt": "2026-10-23T15:00:00+08:00", "planetIndex": 229, "attacker": "Illuminate"},
        ],
    }

    def test_window_filters_old_events(self):
        self._patch(self.FIX)
        # now 固定在 updatedAt：近 24h 只含 10-23 的事件，10-20 的事件被过滤
        now = ds.to_ms("2026-10-24T00:00:00+08:00")
        v = ds.ownership_changes(hours=24, now_ms=now)
        self.assertEqual(v["changes"], 3)
        self.assertEqual(v["defenses"], 1)
        by_planet = {r["planet_index"]: r for r in v["rows"]}
        # 星球 7 两次易主应串成一条链 Humans→Illuminate→Humans
        self.assertEqual(by_planet[7]["paths"], [["Humans", "Illuminate", "Humans"]])
        self.assertEqual(by_planet[7]["changes"], 2)
        # 星球 99 的 previousSeenAt 早于窗口起点 -> 范围待确认
        self.assertTrue(by_planet[99]["uncertain"])
        self.assertEqual(by_planet[229]["victories"][0]["attacker"], "Illuminate")

    def test_no_events(self):
        self._patch({"startedAt": "2026-10-23T00:00:00+08:00", "updatedAt": "2026-10-24T00:00:00+08:00",
                     "events": [], "defenseEvents": []})
        now = ds.to_ms("2026-10-24T00:00:00+08:00")
        v = ds.ownership_changes(hours=24, now_ms=now)
        self.assertEqual(v["rows"], [])
        self.assertEqual(v["changes"], 0)

    def test_missing_history_returns_none(self):
        self._patch({})
        self.assertIsNone(ds.ownership_changes(hours=24))

    def test_garbage_events_skipped(self):
        self._patch({"startedAt": "2026-10-23T00:00:00+08:00", "updatedAt": "2026-10-24T00:00:00+08:00",
                     "events": ["x", None, {"planetIndex": "no"}, {"detectedAt": "bad", "planetIndex": 7}],
                     "defenseEvents": None})
        now = ds.to_ms("2026-10-24T00:00:00+08:00")
        v = ds.ownership_changes(hours=24, now_ms=now)
        self.assertEqual(v["rows"], [])


class TestFactionCn(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ds.faction_cn("Humans"), "超级地球")
        self.assertEqual(ds.faction_cn("Automaton"), "机器人")
        self.assertEqual(ds.faction_cn("Illuminate"), "光能者")
        self.assertEqual(ds.faction_cn(4), "光能者")
        self.assertEqual(ds.faction_cn("Terminids"), "终结族")


class TestRegionModel(unittest.TestCase):
    def test_available_region(self):
        m = ds.region_model(
            {"index": 7, "currentOwner": "Illuminate", "health": 800000, "maxHealth": 1000000},
            {"regionIndex": 0, "owner": 4, "isAvailable": True, "maxHealth": 200000,
             "damageMultiplier": 1.5, "availabilityFactor": 0.62},
            {"name_en": "NEW COB", "name_cn": "新科布", "size": "Town"},
            doc={"campaigns": [], "planet_events": []},
        )
        self.assertEqual(m["status"], "已开放")
        self.assertEqual(m["contribution"], 200000 * 1.5 / 1000000 * 100)

    def test_open_and_threshold(self):
        m = ds.region_model(
            {"index": 7, "currentOwner": "Illuminate", "health": 500000, "maxHealth": 1000000},
            {"regionIndex": 0, "owner": 4, "isAvailable": False, "maxHealth": 200000,
             "damageMultiplier": 1.5, "availabilityFactor": 0.3},
            {}, doc={"campaigns": [], "planet_events": []},
        )
        self.assertEqual(m["status"], "未开放")
        self.assertAlmostEqual(m["threshold"], 70.0, places=6)
        self.assertIn("主星球解放 ≥70.00%", m["condition"])

    def test_no_region_human_owned(self):
        m = ds.region_model(
            {"index": 0, "currentOwner": "Humans", "health": 1000000, "maxHealth": 1000000},
            None, {}, doc={"campaigns": [], "planet_events": []},
        )
        self.assertEqual(m["status"], "无区域战役 · 已解放")

    def test_defense_condition(self):
        m = ds.region_model(
            {"index": 229, "currentOwner": "Humans", "health": 1000000, "maxHealth": 1000000},
            {"regionIndex": 0, "owner": 4, "isAvailable": False, "maxHealth": 200000,
             "damageMultiplier": 1.5, "availabilityFactor": 0.25},
            {}, doc={"campaigns": [{"planet": {"index": 229}, "campaignType": "defense"}],
                     "planet_events": []},
        )
        self.assertTrue(m["defense"])
        self.assertIn("入侵进度", m["condition"])


if __name__ == "__main__":
    unittest.main()

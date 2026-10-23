# -*- coding: utf-8 -*-
"""实测速率测试（rate-source.js 口径）：帧不足/满血顶格/易主 -> None，绝不冒充 0"""
import unittest

from conftest import ROOT  # noqa: F401
import hd2_datasource as ds


def frame(t: str, health: float, owner=4, regen: float = 0.0, players: int = 100):
    return {"timestampUtc": t, "health": health, "maxHealth": 1000000,
            "regenPerSecond": regen, "players": players, "owner": owner}


class TestNetFromFrames(unittest.TestCase):
    def test_insufficient_frames_returns_none(self):
        self.assertIsNone(ds.net_from_frames([], 1000000, 1))
        self.assertIsNone(ds.net_from_frames([frame("2026-10-23T10:00:00Z", 900000)], 1000000, 1))

    def test_short_span_returns_none(self):
        # 两帧间隔 < 14 分钟 -> None（宁可没有实测也不要假实测）
        frames = [frame("2026-10-23T10:00:00Z", 900000), frame("2026-10-23T10:05:00Z", 890000)]
        self.assertIsNone(ds.net_from_frames(frames, 1000000, 1))

    def test_real_delta_positive(self):
        frames = [frame("2026-10-23T10:00:00Z", 900000), frame("2026-10-23T11:00:00Z", 880000)]
        n = ds.net_from_frames(frames, 1000000, 1)
        self.assertIsNotNone(n)
        # 2 万血 / 100 万满血 / 1 小时 = 2%/h
        self.assertAlmostEqual(n["net"], 2.0, places=6)
        self.assertFalse(n["owner_changed"])
        self.assertFalse(n["capped"])

    def test_owner_change_flagged(self):
        frames = [frame("2026-10-23T10:00:00Z", 900000, owner=1),
                  frame("2026-10-23T11:00:00Z", 880000, owner=4)]
        n = ds.net_from_frames(frames, 1000000, 1)
        self.assertTrue(n["owner_changed"])

    def test_capped_flagged(self):
        mh = 1000000
        frames = [frame("2026-10-23T10:00:00Z", mh), frame("2026-10-23T11:00:00Z", mh)]
        n = ds.net_from_frames(frames, mh, 1)
        self.assertTrue(n["capped"])

    def test_window_picks_latest_span(self):
        frames = [frame("2026-10-23T08:00:00Z", 800000),
                  frame("2026-10-23T10:00:00Z", 900000),
                  frame("2026-10-23T11:00:00Z", 880000)]
        n = ds.net_from_frames(frames, 1000000, 1)
        # 窗口取最近 1 小时（10:00-11:00），不是全程均速
        self.assertAlmostEqual(n["net"], 2.0, places=6)


class TestPlanetMeasured(unittest.TestCase):
    def _patch_frames(self, frames):
        orig = ds.recent_planet_frames
        ds.recent_planet_frames = lambda idx: frames
        orig_idx = ds.recent_index
        ds.recent_index = lambda force=False: {"windowHours": 1, "planets": [7], "generatedAt": "2026-10-23T12:00:00Z"}
        return orig, orig_idx

    def tearDown(self):
        ds.recent_planet_frames = self._orig_frames
        ds.recent_index = self._orig_idx

    def setUp(self):
        self._orig_frames = ds.recent_planet_frames
        self._orig_idx = ds.recent_index

    def test_valid_measurement(self):
        self._patch_frames([frame("2026-10-23T10:00:00Z", 900000),
                            frame("2026-10-23T11:00:00Z", 880000)])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        m = ds.planet_measured(p)
        self.assertIsNotNone(m)
        self.assertTrue(m["measured"])
        self.assertAlmostEqual(m["net"], 2.0, places=6)
        self.assertAlmostEqual(m["gross"], 3.5, places=6)   # net + resistance
        self.assertAlmostEqual(m["res_eq"], 1.5, places=6)  # resistance * 1e6/1e6

    def test_capped_returns_none_not_zero(self):
        self._patch_frames([frame("2026-10-23T10:00:00Z", 1000000),
                            frame("2026-10-23T11:00:00Z", 1000000)])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        self.assertIsNone(ds.planet_measured(p), "满血顶格必须交给推演兜底，不能当实测 0")

    def test_owner_change_returns_none(self):
        self._patch_frames([frame("2026-10-23T10:00:00Z", 900000, owner=1),
                            frame("2026-10-23T11:00:00Z", 880000, owner=4)])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        self.assertIsNone(ds.planet_measured(p))

    def test_no_frames_returns_none(self):
        self._patch_frames([])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        self.assertIsNone(ds.planet_measured(p))

    def test_zero_delta_uncapped_is_real_zero(self):
        # 未满血但血量没变 = 真实停滞 0.0（与“数据缺失 None”区分）
        self._patch_frames([frame("2026-10-23T10:00:00Z", 900000),
                            frame("2026-10-23T11:00:00Z", 900000)])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 0.0}
        m = ds.planet_measured(p)
        self.assertIsNotNone(m)
        self.assertEqual(m["net"], 0.0)


class TestRegionMeasured(unittest.TestCase):
    def setUp(self):
        self._orig_frames = ds.recent_region_frames
        self._orig_idx = ds.recent_index

    def tearDown(self):
        ds.recent_region_frames = self._orig_frames
        ds.recent_index = self._orig_idx

    def test_region_measured(self):
        ds.recent_index = lambda force=False: {"windowHours": 1, "planets": [7], "generatedAt": "2026-10-23T12:00:00Z"}
        ds.recent_region_frames = lambda: [
            {"timestampUtc": "2026-10-23T10:00:00Z",
             "planetRegions": [{"planetIndex": 7, "regionIndex": 0, "owner": 4, "health": 200000,
                                "regerPerSecond": 5.0, "players": 3, "isAvailable": True}]},
            {"timestampUtc": "2026-10-23T11:00:00Z",
             "planetRegions": [{"planetIndex": 7, "regionIndex": 0, "owner": 4, "health": 190000,
                                "regerPerSecond": 5.0, "players": 5, "isAvailable": True}]},
        ]
        m = ds.region_measured(7, {"regionIndex": 0, "maxHealth": 200000})
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m["net"], 5.0, places=6)          # 1万/20万 = 5%/h
        self.assertAlmostEqual(m["res"], 9.0, places=6)          # 5*3600/200000*100
        self.assertEqual(m["players"], 5)

    def test_region_missing_frames_none(self):
        ds.recent_region_frames = lambda: []
        self.assertIsNone(ds.region_measured(7, {"regionIndex": 0, "maxHealth": 200000}))


if __name__ == "__main__":
    unittest.main()

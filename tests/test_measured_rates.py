# -*- coding: utf-8 -*-
"""实测速率测试（rate-source.js 口径）：帧不足/满血顶格/易主/断更 -> None，绝不冒充 0"""
import time
import unittest
from datetime import datetime, timedelta, timezone

from conftest import ROOT  # noqa: F401
import hd2_datasource as ds


def _iso(hours_ago: float) -> str:
    """相对当前时间的 ISO 时间戳（保证通过“末帧断更守卫”）"""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def frame(t, health: float, owner=4, regen: float = 0.0, players: int = 100):
    """t 可传“相对当前的小时数”或现成 ISO 字符串"""
    t = _iso(t) if isinstance(t, (int, float)) else t
    return {"timestampUtc": t, "health": health, "maxHealth": 1000000,
            "regenPerSecond": regen, "players": players, "owner": owner}


class TestNetFromFrames(unittest.TestCase):
    def test_insufficient_frames_returns_none(self):
        self.assertIsNone(ds.net_from_frames([], 1000000, 1))
        self.assertIsNone(ds.net_from_frames([frame(1.0, 900000)], 1000000, 1))

    def test_short_span_returns_none(self):
        # 两帧间隔 < 14 分钟 -> None（宁可没有实测也不要假实测）
        frames = [frame(1.0, 900000), frame(0.9, 890000)]
        self.assertIsNone(ds.net_from_frames(frames, 1000000, 1))

    def test_real_delta_positive(self):
        frames = [frame(1.0, 900000), frame(0.0, 880000)]
        n = ds.net_from_frames(frames, 1000000, 1)
        self.assertIsNotNone(n)
        # 2 万血 / 100 万满血 / 1 小时 = 2%/h
        self.assertAlmostEqual(n["net"], 2.0, places=6)
        self.assertFalse(n["owner_changed"])
        self.assertFalse(n["capped"])

    def test_owner_change_flagged(self):
        frames = [frame(1.0, 900000, owner=1),
                  frame(0.0, 880000, owner=4)]
        n = ds.net_from_frames(frames, 1000000, 1)
        self.assertTrue(n["owner_changed"])

    def test_capped_flagged(self):
        mh = 1000000
        frames = [frame(1.0, mh), frame(0.0, mh)]
        n = ds.net_from_frames(frames, mh, 1)
        self.assertTrue(n["capped"])

    def test_capped_with_missing_frame_maxhealth(self):
        """区域帧可能缺 maxHealth 字段：用传入的满血兜底判定顶格"""
        mh = 200000
        f1 = {"timestampUtc": _iso(1.0), "health": mh, "owner": 4}
        f2 = {"timestampUtc": _iso(0.0), "health": mh, "owner": 4}
        n = ds.net_from_frames([f1, f2], mh, 1)
        self.assertTrue(n["capped"])

    def test_window_picks_latest_span(self):
        frames = [frame(3.0, 800000),
                  frame(1.0, 900000),
                  frame(0.0, 880000)]
        n = ds.net_from_frames(frames, 1000000, 1)
        # 窗口取最近 1 小时（1h前-现在），不是全程均速
        self.assertAlmostEqual(n["net"], 2.0, places=6)


class TestPlanetMeasured(unittest.TestCase):
    def setUp(self):
        self._orig_frames = ds.recent_planet_frames
        self._orig_idx = ds.recent_index

    def tearDown(self):
        ds.recent_planet_frames = self._orig_frames
        ds.recent_index = self._orig_idx

    def _patch_frames(self, frames, generated_at=None):
        ds.recent_planet_frames = lambda idx: frames
        ds.recent_index = lambda force=False: {
            "windowHours": 1, "planets": [7],
            "generatedAt": generated_at or time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }

    def test_valid_measurement(self):
        self._patch_frames([frame(1.0, 900000), frame(0.0, 880000)])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        m = ds.planet_measured(p)
        self.assertIsNotNone(m)
        self.assertTrue(m["measured"])
        self.assertAlmostEqual(m["net"], 2.0, places=6)
        self.assertAlmostEqual(m["gross"], 3.5, places=6)   # net + resistance
        self.assertAlmostEqual(m["res_eq"], 1.5, places=6)  # resistance * 1e6/1e6

    def test_capped_returns_none_not_zero(self):
        self._patch_frames([frame(1.0, 1000000), frame(0.0, 1000000)])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        self.assertIsNone(ds.planet_measured(p), "满血顶格必须交给推演兜底，不能当实测 0")

    def test_owner_change_returns_none(self):
        self._patch_frames([frame(1.0, 900000, owner=1),
                            frame(0.0, 880000, owner=4)])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        self.assertIsNone(ds.planet_measured(p))

    def test_no_frames_returns_none(self):
        self._patch_frames([])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        self.assertIsNone(ds.planet_measured(p))

    def test_stale_last_frame_returns_none(self):
        """末帧超过断更阈值（3h）：旧差分不得冒充当前实测"""
        self._patch_frames([frame(9.0, 900000), frame(8.0, 880000)])
        p = {"index": 7, "maxHealth": 1000000, "resistance": 1.5}
        self.assertIsNone(ds.planet_measured(p))

    def test_zero_delta_uncapped_is_real_zero(self):
        # 未满血但血量没变 = 真实停滞 0.0（与“数据缺失 None”区分）
        self._patch_frames([frame(1.0, 900000), frame(0.0, 900000)])
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

    def _fresh_index(self):
        return {"windowHours": 1, "planets": [7],
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00")}

    def test_region_measured(self):
        ds.recent_index = lambda force=False: self._fresh_index()
        ds.recent_region_frames = lambda: [
            {"timestampUtc": _iso(1.0),
             "planetRegions": [{"planetIndex": 7, "regionIndex": 0, "owner": 4, "health": 200000,
                                "regerPerSecond": 5.0, "players": 3, "isAvailable": True}]},
            {"timestampUtc": _iso(0.0),
             "planetRegions": [{"planetIndex": 7, "regionIndex": 0, "owner": 4, "health": 190000,
                                "regerPerSecond": 5.0, "players": 5, "isAvailable": True}]},
        ]
        m = ds.region_measured(7, {"regionIndex": 0, "maxHealth": 200000})
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m["net"], 5.0, places=6)          # 1万/20万 = 5%/h
        self.assertAlmostEqual(m["res"], 9.0, places=6)          # 5*3600/200000*100
        self.assertEqual(m["players"], 5)

    def test_region_capped_returns_none(self):
        """区域帧缺 maxHealth 时按传入满血判定顶格"""
        ds.recent_index = lambda force=False: self._fresh_index()
        ds.recent_region_frames = lambda: [
            {"timestampUtc": _iso(1.0),
             "planetRegions": [{"planetIndex": 7, "regionIndex": 0, "owner": 4, "health": 200000,
                                "regerPerSecond": 5.0, "players": 3, "isAvailable": False}]},
            {"timestampUtc": _iso(0.0),
             "planetRegions": [{"planetIndex": 7, "regionIndex": 0, "owner": 4, "health": 200000,
                                "regerPerSecond": 5.0, "players": 0, "isAvailable": False}]},
        ]
        self.assertIsNone(ds.region_measured(7, {"regionIndex": 0, "maxHealth": 200000}))

    def test_region_missing_frames_none(self):
        ds.recent_region_frames = lambda: []
        self.assertIsNone(ds.region_measured(7, {"regionIndex": 0, "maxHealth": 200000}))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""MO 预测口径测试（forecast.js 移植）：order_window / task_state / defense_model / reinforcement"""
import unittest

from conftest import ROOT  # noqa: F401
import hd2_datasource as ds


class TestOrderWindow(unittest.TestCase):
    def test_basic_window(self):
        a = {"startedAt": "2026-10-20T09:10:02Z", "deadline": "2026-10-24T09:10:00Z",
             "serverTime": "2026-10-22T09:10:00Z"}
        w = ds.order_window(a)
        self.assertIsNotNone(w)
        self.assertAlmostEqual(w["hours"], 96.0, places=1)
        # 已历 48/96 小时 -> 时间基准 50%
        self.assertAlmostEqual(w["expected"], 50.0, places=1)

    def test_missing_deadline_none(self):
        self.assertIsNone(ds.order_window({"startedAt": "2026-10-20T09:10:02Z"}))

    def test_missing_start_uses_dispatch_fallback(self):
        doc_dispatches = [{"message": "<i=3>NEW MAJOR ORDER</i>", "published": "2026-10-20T09:10:02Z"}]
        orig = ds.dispatches
        ds.dispatches = lambda doc=None: doc_dispatches
        try:
            w = ds.order_window({"deadline": "2026-10-24T09:10:00Z",
                                 "serverTime": "2026-10-22T09:10:00Z"})
        finally:
            ds.dispatches = orig
        self.assertIsNotNone(w)


class TestTaskState(unittest.TestCase):
    def setUp(self):
        self.window = {"start": 0.0, "end": 96 * 3600000.0, "now": 48 * 3600000.0, "expected": 50.0}

    def test_complete(self):
        st = ds.task_state({"type": 11, "goal": 100}, 100, self.window)
        self.assertEqual(st["state"], "complete")

    def test_hold_task(self):
        st = ds.task_state({"type": 13, "goal": 1}, 1, self.window)
        self.assertEqual(st["state"], "hold")
        self.assertTrue(st["satisfied"])

    def test_active_projection(self):
        # 目标 100，已 25，全程 96h 已历 48h -> 均速 25/48，剩 48h -> 预计 50%
        st = ds.task_state({"type": 11, "goal": 100}, 25, self.window)
        self.assertEqual(st["state"], "active")
        self.assertAlmostEqual(st["rate"], 25 / 48, places=6)
        self.assertAlmostEqual(st["projected"], 50.0, places=1)
        self.assertAlmostEqual(st["delta"], -25.0, places=1)
        self.assertFalse(st["win"])

    def test_winning_pace(self):
        # 已 75/100，同样 48h -> 均速 75/48，剩余 48h 完成所需 25 -> eta ≈ 16h < 48h
        st = ds.task_state({"type": 11, "goal": 100}, 75, self.window)
        self.assertTrue(st["win"])
        self.assertAlmostEqual(st["eta_h"], 16.0, places=1)

    def test_recent_rate_projection(self):
        st = ds.task_state({"type": 11, "goal": 100}, 25, self.window, recent_rate=1.0)
        # 近 1h 实测 1/h，剩 48h -> (25+48)/100 = 73%
        self.assertAlmostEqual(st["recent_projected"], 73.0, places=1)

    def test_unknown_without_window(self):
        st = ds.task_state({"type": 11, "goal": 100}, 25, None)
        self.assertEqual(st["state"], "unknown")

    def test_bad_goal(self):
        self.assertEqual(ds.task_state({"type": 11, "goal": 0}, 25, self.window)["state"], "unknown")
        self.assertEqual(ds.task_state({"type": 11}, 25, self.window)["state"], "unknown")


class TestDefenseModel(unittest.TestCase):
    def test_measured_rate_priority(self):
        c = {"defendersProgress": 30.0, "attackersProgress": 55.0, "attackersRate": 2.083,
             "defendersRate": 0.85, "remainingTime": 43200}
        f = ds.defense_model(c)
        self.assertEqual(f["source"], "实测防守速率")
        self.assertAlmostEqual(f["rate"], 0.85, places=6)
        # 30 + 0.85*12 = 40.2
        self.assertAlmostEqual(f["projected"], 40.2, places=1)
        self.assertFalse(f["win"])
        self.assertAlmostEqual(f["required_rate"], 70.0 / 12.0, places=3)

    def test_zero_measured_rate_is_real_stall(self):
        c = {"defendersProgress": 30.0, "attackersProgress": 55.0, "attackersRate": 2.083,
             "defendersRate": 0.0, "remainingTime": 43200}
        f = ds.defense_model(c)
        self.assertEqual(f["rate"], 0.0, "实测 0 是真实停滞，不得替换成历史正值")
        self.assertAlmostEqual(f["projected"], 30.0, places=6)

    def test_fallback_mean_rate(self):
        # 无 defendersRate：用开战以来平均 def/elapsed
        c = {"defendersProgress": 24.0, "attackersProgress": 50.0, "attackersRate": 2.083,
             "remainingTime": 43200}
        f = ds.defense_model(c)
        self.assertEqual(f["source"], "开战以来平均防守速率")
        # elapsed = 50/2.083 ≈ 24.008h；rate = 24/24.008
        self.assertAlmostEqual(f["rate"], 24.0 / (50.0 / 2.083), places=3)

    def test_already_won(self):
        c = {"defendersProgress": 100.0, "attackersProgress": 90.0, "attackersRate": 2.0,
             "remainingTime": 100}
        f = ds.defense_model(c)
        self.assertTrue(f["win"])
        self.assertEqual(f["eta_h"], 0.0)

    def test_missing_data(self):
        f = ds.defense_model({})
        self.assertIsNone(f["def"])
        self.assertIsNone(f["win"])


class TestReinforcement(unittest.TestCase):
    def test_liberation_push_model(self):
        doc = {"player_distribution": {"total": 80000},
               "impactMultiplier": 0.008, "planet_events": []}
        p = {"index": 7, "currentOwner": "Illuminate", "players": 2000,
             "resistance": 3.0, "health": 800000, "maxHealth": 1000000}
        r = ds.reinforcement(doc, p, None)
        self.assertEqual(r["mode"], "liberation")
        self.assertEqual(r["source"], "推演")
        self.assertEqual(r["state"], "active")
        self.assertAlmostEqual(r["current_share"], 2.5, places=6)
        # ratePerShare = 80000*0.008*90/1e6 = 0.0576 %/h per 1% share
        self.assertAlmostEqual(r["rate_per_share"], 80000 * 0.008 * 90 / 1e6, places=9)
        self.assertAlmostEqual(r["required_share"], 3.0 / r["rate_per_share"], places=6)
        self.assertFalse(r["sufficient"], "占比 2.5% 低于所需 52% -> 不足")
        self.assertGreater(r["gap"], 0)

    def test_liberation_complete_when_human_owned(self):
        doc = {"player_distribution": {"total": 80000}, "impactMultiplier": 0.008}
        p = {"index": 0, "currentOwner": "Humans", "players": 100, "resistance": 0, "maxHealth": 1000000}
        r = ds.reinforcement(doc, p, None)
        self.assertEqual(r["state"], "complete")

    def test_insufficient_data_unknown(self):
        r = ds.reinforcement({"player_distribution": {}}, {"index": 7, "currentOwner": "Illuminate"}, None)
        self.assertEqual(r["state"], "unknown")


class TestIsDefense(unittest.TestCase):
    def test_variants(self):
        self.assertTrue(ds.is_defense({"campaignType": "defense"}))
        self.assertTrue(ds.is_defense({"eventType": 1}))
        self.assertTrue(ds.is_defense({"type": 1}))
        self.assertTrue(ds.is_defense({"type": "4"}))
        self.assertFalse(ds.is_defense({"campaignType": "liberation", "type": 0}))


if __name__ == "__main__":
    unittest.main()

"""Fixed-snapshot tests for the unified planet rate source (no network)."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins"))
from astrbot_plugin_hd2_common import data


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


T0 = int(data.timestamp("2026-10-30T00:00:00Z"))


def frame(epoch, health, owner="Terminids", max_health=1000000):
    return {"timestampUtc": iso(epoch), "health": health, "maxHealth": max_health,
            "owner": owner, "players": 100, "regenPerSecond": 0}


def planet(index=7, health=1000000, max_health=1000000, players=6000, resistance=0.9,
           owner="Terminids"):
    return {"index": index, "name": "ZEA RUGOSIA", "currentOwner": owner,
            "health": health, "maxHealth": max_health, "players": players,
            "resistance": resistance}


def doc(planet_obj, total=10000, impact=0.02, campaigns=None, events=None, war_total=None):
    return {"fetchedAt": "2026-10-30T01:00:00Z", "impactMultiplier": impact,
            "player_distribution": {"total": total},
            "war": {"statistics": {"playerCount": war_total if war_total is not None else total + 500}},
            "planets": [planet_obj],
            "campaigns": campaigns if campaigns is not None else [
                {"planet": {"index": planet_obj["index"]}, "campaignType": "liberation",
                 "faction": "Illuminate"}],
            "planet_events": events or []}


def rising_frames(step=900, count=5, start=940000, step_health=15000):
    """Frames whose health rises → negative net rate (enemy counterattack)."""
    return [frame(T0 + i * step, start + i * step_health) for i in range(count)]


class PlanetRateSourceTests(unittest.TestCase):
    def test_measured_recent_beats_estimate_and_exposes_conflict(self):
        p = planet()  # 6000/10000 = 60%; resistance 0.9 → threshold 50%
        frames = rising_frames()  # 940k → 1M over 1h → -6.0 %/h
        state = data.planet_rate_state(doc(p), p, frames=frames, now=T0 + 3600 + 30)
        self.assertEqual(state["source"], "measured_recent")
        self.assertEqual(state["source_label"], "实测 · 近 1 小时")
        self.assertTrue(state["measured"])
        self.assertAlmostEqual(state["net"], -6.0)
        self.assertAlmostEqual(state["actual"], -6.0)
        self.assertAlmostEqual(state["current"], -5.1)  # -6.0 + 0.9
        self.assertAlmostEqual(state["model_net"], 0.18)  # 推演仍为正
        self.assertAlmostEqual(state["res_eq"], 0.9)
        self.assertEqual(state["strength_label"], "低")
        # 门槛按推演算：60% > 50%，但实测在倒退 → 文案必须承认矛盾
        self.assertTrue(state["threshold_met"])
        self.assertEqual(state["threshold_state"], "met")
        self.assertEqual(state["threshold_text"],
                         "持续推进需 >50.00% 在线兵力 · 当前 60.00% · "
                         "按当前在线兵力可持续推进（实测仍在倒退）")
        self.assertIn("实测仍在倒退", state["threshold_note"])
        self.assertEqual(state["eta_reason"], "nonpositive")
        self.assertEqual(state["eta_text"], "无法完成（净速率 ≤ 0）")

    def test_measured_progress_eta_and_current_output(self):
        p = planet()
        frames = [frame(T0 + i * 900, 1000000 - i * 15000) for i in range(5)]  # 1M → 940k
        state = data.planet_rate_state(doc(p), p, frames=frames, now=T0 + 3600 + 30)
        self.assertEqual(state["source"], "measured_recent")
        self.assertAlmostEqual(state["net"], 6.0)
        self.assertAlmostEqual(state["current"], 6.9)
        self.assertAlmostEqual(state["eta_hours"], 100 / 6)
        self.assertEqual(state["eta_text"], "16 小时 40 分钟")
        self.assertEqual(state["eta_reason"], "ok")
        self.assertEqual(state["threshold_note"], "按当前在线兵力可持续推进")

    def test_measured_progress_but_model_short_keeps_copy_consistent(self):
        p = planet(players=3000)  # 30% < 50% threshold
        frames = [frame(T0 + i * 900, 1000000 - i * 15000) for i in range(5)]
        state = data.planet_rate_state(doc(p), p, frames=frames, now=T0 + 3600 + 30)
        self.assertFalse(state["threshold_met"])
        self.assertEqual(state["threshold_state"], "short")
        self.assertIn("还差约 20.00 个百分点", state["threshold_text"])
        self.assertIn("实测仍在推进", state["threshold_text"])
        self.assertEqual(state["threshold_note"],
                         "实测仍在推进 · 但按当前在线兵力推演不足以抵消抵抗")

    def test_recent_window_uses_last_hour_only(self):
        p = planet()
        frames = [frame(T0, 1000000), frame(T0 + 3600, 900000), frame(T0 + 7200, 850000)]
        state = data.planet_rate_state(doc(p), p, frames=frames, now=T0 + 7200 + 30)
        self.assertEqual(state["source"], "measured_recent")
        self.assertAlmostEqual(state["hours"], 1.0)
        self.assertAlmostEqual(state["net"], 5.0)  # (900k → 850k) / 1M / 1h

    def test_short_recent_window_falls_back_to_full_span(self):
        p = planet(health=700000)
        frames = [frame(T0, 1000000), frame(T0 + 2.5 * 3600 - 600, 740000),
                  frame(T0 + 2.5 * 3600, 700000)]
        state = data.planet_rate_state(doc(p), p, frames=frames, now=T0 + 2.5 * 3600 + 30)
        self.assertEqual(state["source"], "measured_all")
        self.assertEqual(state["source_label"], "实测 · 全程均速")
        self.assertAlmostEqual(state["hours"], 2.5)
        self.assertAlmostEqual(state["net"], 12.0)
        self.assertAlmostEqual(state["eta_hours"], 70 / 12)

    def test_capped_frames_fall_back_to_estimate(self):
        p = planet()
        frames = [frame(T0 + i * 900, 1000000) for i in range(5)]  # 满血顶格
        state = data.planet_rate_state(doc(p), p, frames=frames, now=T0 + 3600 + 30)
        self.assertEqual(state["source"], "estimated")
        self.assertIn("满血顶格", state["source_label"])
        self.assertEqual(state["why"], "capped")
        self.assertFalse(state["measured"])
        self.assertIsNone(state["actual"])
        self.assertAlmostEqual(state["net"], 0.18)
        self.assertAlmostEqual(state["current"], 1.08)
        self.assertEqual(state["eta_text"], "23 天 3 小时")
        self.assertEqual(state["eta_reason"], "ok")
        self.assertTrue(state["threshold_met"])
        self.assertEqual(state["threshold_note"], "按当前在线兵力可持续推进")

    def test_stale_owner_changed_and_short_frames_fall_back(self):
        p = planet()
        stale = rising_frames()
        state = data.planet_rate_state(doc(p), p, frames=stale, now=T0 + 3600 + 4 * 3600)
        self.assertEqual((state["source"], state["why"]), ("estimated", "stale"))

        changed = rising_frames()
        changed[-1] = frame(T0 + 3600, 1000000, owner="Humans")
        state = data.planet_rate_state(doc(p), p, frames=changed, now=T0 + 3600 + 30)
        self.assertEqual((state["source"], state["why"]), ("estimated", "owner"))
        self.assertIn("窗口内易主", state["source_label"])

        short = [frame(T0, 1000000), frame(T0 + 600, 990000)]
        state = data.planet_rate_state(doc(p), p, frames=short, now=T0 + 600 + 30)
        self.assertEqual((state["source"], state["why"]), ("estimated", "short"))
        self.assertIn("实测跨度过短", state["source_label"])

    def test_estimate_label_without_frames_and_campaign_state_compat(self):
        p = planet()
        d = doc(p)
        state = data.planet_rate_state(d, p, frames=[])
        old = data.campaign_state(d, p)
        self.assertEqual(set(old), {"defending", "progress", "health", "max_health", "deadline",
                                    "rate", "measured", "resistance", "faction", "status"})
        self.assertAlmostEqual(state["net"], old["rate"])
        self.assertEqual(state["source"], "estimated")
        self.assertEqual(state["source_label"], "推演 · 无实测帧")
        self.assertFalse(state["measured"])

    def test_frames_are_fetched_when_not_injected(self):
        p = planet()
        frames = rising_frames()
        with patch.object(data, "planet_frames", return_value=frames) as fetch:
            state = data.planet_rate_state(doc(p), p, now=T0 + 3600 + 30)
        fetch.assert_called_once_with(7)
        self.assertEqual(state["source"], "measured_recent")
        self.assertAlmostEqual(state["net"], -6.0)

    def test_planet_frames_sorts_drops_future_and_is_defensive(self):
        payload = {"data": [frame(T0 + 1800, 990000), frame(T0, 1000000),
                            frame(T0 + 7200, 900000)]}
        with patch.object(data, "get_json", return_value=payload):
            with data.snapshot_scope({"fetchedAt": iso(T0 + 3600)}):
                frames = data.planet_frames(7)
        self.assertEqual([f["health"] for f in frames], [1000000, 990000])

        with patch.object(data, "get_json", side_effect=OSError("offline")):
            self.assertIsNone(data.planet_frames(7))
        self.assertIsNone(data.planet_frames(7.5))
        self.assertIsNone(data.planet_frames(None))

    def test_defense_measured_zero_rate_and_opening_mean(self):
        p = planet()
        event = {"planetIndex": 7, "eventType": 1, "health": 750000,
                 "maxHealth": 1000000, "endTime": "2026-10-31T01:00:00Z"}
        camp = {"planet": {"index": 7}, "campaignType": "defense", "faction": "Automaton",
                "defendersProgress": 42, "defendersRate": 0, "endTime": "2026-10-31T01:00:00Z"}
        state = data.planet_rate_state(doc(p, campaigns=[camp], events=[event]), p, frames=[])
        self.assertEqual(state["mode"], "defense")
        self.assertEqual(state["source"], "measured_campaign")
        self.assertEqual(state["source_label"], "实测 · 防守速率")
        self.assertTrue(state["measured"])
        self.assertEqual(state["net"], 0)
        self.assertAlmostEqual(state["res_eq"], 0)
        self.assertIsNone(state["threshold"])
        self.assertIsNone(state["strength_label"])
        self.assertEqual(state["eta_text"], "无法完成（防守速率 ≤ 0）")

        camp = {"planet": {"index": 7}, "campaignType": "defense", "faction": "Automaton",
                "defendersProgress": 30, "attackersProgress": 50, "attackersRate": 2,
                "remainingTime": 36000}
        state = data.planet_rate_state(doc(p, campaigns=[camp]), p, frames=[])
        self.assertEqual(state["source"], "estimated_defense")
        self.assertEqual(state["source_label"], "推演 · 开战以来均速")
        self.assertFalse(state["measured"])
        self.assertIsNone(state["actual"])
        self.assertAlmostEqual(state["net"], 1.2)
        self.assertAlmostEqual(state["eta_hours"], 70 / 1.2)
        self.assertEqual(state["eta_text"], "2 天 10 小时")

    def test_unknown_inputs_and_threshold_edges(self):
        p = planet(players=None)
        state = data.planet_rate_state(doc(p), p, frames=[])
        self.assertIsNone(state["net"])
        self.assertEqual((state["source"], state["source_label"]), ("unknown", "未知"))
        self.assertEqual(state["eta_reason"], "unknown")
        self.assertEqual(state["eta_text"], "无法推算")
        self.assertAlmostEqual(state["threshold"], 50.0)
        self.assertIsNone(state["threshold_met"])
        self.assertEqual(state["threshold_state"], "unknown")
        self.assertIn("当前占比未知", state["threshold_text"])

        p = planet(players=327, health=2000000, max_health=2000000, resistance=6.67)
        state = data.planet_rate_state(doc(p, total=73064, impact=0.015164208), p, frames=[])
        self.assertGreater(state["threshold"], 100)
        self.assertEqual(state["threshold_state"], "impossible")
        self.assertFalse(state["threshold_met"])
        self.assertIn("当前效率下无法达成", state["threshold_text"])
        self.assertIn("即使全服在线", state["threshold_note"])

    def test_threshold_met_matches_model_net(self):
        for players in (0, 1000, 5000, 6000, 10000):
            p = planet(players=players)
            state = data.planet_rate_state(doc(p), p, frames=[])
            self.assertEqual(state["threshold_met"], state["model_net"] > 0, players)
            self.assertEqual(state["threshold_state"],
                             "met" if state["model_net"] > 0 else "short")

    def test_zea_rugosia_cached_snapshot_shape(self):
        # Values from the cached 2026-10-27 site snapshot and the real
        # data/recent/planet_7.json frames (5 capped frames, 60.1 min).
        p = {"index": 7, "name": "ZEA RUGOSIA", "currentOwner": "Illuminate",
             "health": 1700000, "maxHealth": 1700000, "players": 327, "resistance": 2.94}
        d = {"fetchedAt": "2026-10-27T01:35:49+08:00", "impactMultiplier": 0.015164208,
             "player_distribution": {"total": 73064},
             "war": {"statistics": {"playerCount": 74446}},
             "planets": [p],
             "campaigns": [{"planet": {"index": 7}, "campaignType": "liberation",
                            "faction": "Illuminate"}],
             "planet_events": []}
        start = int(data.timestamp("2026-10-26T16:15:57Z"))
        frames = [{"timestampUtc": iso(start + i * 900), "health": 1700000,
                   "maxHealth": 1700000, "owner": 4, "players": 327} for i in range(5)]
        state = data.planet_rate_state(d, p, frames=frames, now=start + 3600 + 30)
        self.assertEqual(state["source"], "estimated")
        self.assertEqual(state["why"], "capped")
        self.assertAlmostEqual(state["net"], -2.913748, places=6)
        self.assertAlmostEqual(state["current"], 0.026252, places=6)
        self.assertAlmostEqual(state["res_eq"], 4.998)
        self.assertAlmostEqual(state["player_share"], 0.447553, places=6)
        self.assertEqual(state["online_total"], 73064)
        self.assertEqual(state["online_source"], "player_distribution")
        self.assertAlmostEqual(state["threshold"], 50.12225, places=4)
        self.assertFalse(state["threshold_met"])
        self.assertEqual(state["threshold_state"], "short")
        self.assertEqual(state["threshold_text"],
                         "持续推进需 >50.13% 在线兵力 · 当前 0.45% · 还差约 49.68 个百分点")
        self.assertEqual(state["threshold_note"], "按当前在线兵力无法抵消抵抗")
        self.assertEqual(state["eta_text"], "无法完成（净速率 ≤ 0）")
        self.assertEqual(state["strength_label"], "中")
        self.assertEqual(state["frame_count"], 5)
        self.assertAlmostEqual(state["model_net"], state["net"])


if __name__ == "__main__":
    unittest.main()

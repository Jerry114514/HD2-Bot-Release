import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framework import install

install()

from test_core import ROOT, snapshot  # noqa: F401
from PIL import Image, ImageDraw

from astrbot_plugin_hd2_common import commands, data, tactical
from astrbot_plugin_hd2_war_report import report_card


def dss_variables():
    return {
        "categories": [{
            "key": "tactical_action",
            "entries": [
                {
                    "key": "Eagle Storm [1209/1212/1216]",
                    "name": "Eagle Storm [1209/1212/1216]",
                    "cn": "✈ 飞鹰风暴",
                    "desc": "飞鹰编队提供密集空中火力支援。",
                    "effect_ids": [1209, 1212, 1216],
                },
                {
                    "key": "Orbital Block [1213/1215]",
                    "name": "Orbital Block [1213/1215]",
                    "cn": "🚫 轨道封锁",
                    "desc": "反舰导弹阻止敌军撤离并封锁增援。",
                    "effect_ids": [1213, 1215],
                },
                {
                    "key": "Heavy Ordnance Distribution [1214]",
                    "name": "Heavy Ordnance Distribution [1214]",
                    "cn": "💣 重型军械分发",
                    "desc": "超级驱逐舰获得额外重型军械支援。",
                    "effect_ids": [1214],
                },
                {
                    "key": "Operational Support [1238]",
                    "name": "Operational Support [1238]",
                    "cn": "✊ 行动支持",
                    "desc": "外骨骼机甲冷却时间减少 35%。",
                    "effect_ids": [1238],
                },
            ],
        }]
    }


def dss_document():
    return {
        "fetchedAt": "2026-11-12T01:00:00Z",
        "planets": [{
            "index": 246,
            "name": "X-45",
            "sector": "Test Sector",
        }],
        "dss": {
            "planetIndex": 246,
            "currentWarTime": 83900000,
            "electionEndWarTime": 84000000,
            "clientTimeMs": 1762909200000,
            "donationUpdatedAtMs": 1762909200000,
            "donationSource": "official",
            "activeEffectIds": [1213, 1215],
            "tacticalActions": [
                {
                    "id32": 1,
                    "name": "EAGLE STORM",
                    "status": 1,
                    "effectIds": [1209, 1212, 1216],
                    "cost": [{
                        "itemMixId": 3992382197,
                        "current": 38824.844,
                        "target": 86400,
                        "maxDonation": 75,
                        "maxDonationPeriodSec": 86400,
                    }],
                },
                {
                    "id32": 2,
                    "name": "ORBITAL BLOCK",
                    "status": 2,
                    "effectIds": [1213, 1215],
                    "cost": [{
                        "itemMixId": 2985106497,
                        "current": 30,
                        "target": 50,
                        "maxDonation": 10,
                        "maxDonationPeriodSec": 3600,
                    }],
                },
                {
                    "id32": 3,
                    "name": "HEAVY ORDNANCE DISTRIBUTION",
                    "status": 3,
                    "effectIds": [1214],
                    "cost": [{
                        "itemMixId": 3608481516,
                        "current": 70,
                        "target": 100,
                        "maxDonation": 20,
                        "maxDonationPeriodSec": 7200,
                    }],
                },
            ],
        },
    }


class TacticalObjectiveTest(unittest.TestCase):
    def test_effect_1219_uses_main_site_equivalent_resistance(self):
        tables = ROOT / "tables"
        values = json.loads((tables / "effect_values.json").read_text(encoding="utf-8"))
        details = json.loads((tables / "galactic_effects.json").read_text(encoding="utf-8"))
        names = json.loads((tables / "effect_id_cn.json").read_text(encoding="utf-8"))
        count = values["effects"]["1219"]["v"][0]
        self.assertEqual(count / 10_000, -1.0)
        self.assertEqual(details["1219"]["name"], "Planetary regen adjustment")
        self.assertIn("理论抵抗度 -1.00%", details["1219"]["description_cn"])
        self.assertEqual(names["1219"], "星球抵抗度调整")

    def test_waypoints_prefer_live_main_site_table(self):
        with patch.object(
            data,
            "get_json",
            return_value={"waypoints": {"124": [269]}},
        ):
            self.assertEqual(tactical.load_waypoints(), {"124": [269]})

    def test_compute_matches_site_topology_and_exposes_target_links(self):
        doc = {
            "planets": [
                {"index": 1, "name": "SOURCE", "currentOwner": "Automaton",
                 "sector": "Test Sector"},
                {"index": 2, "name": "TARGET", "currentOwner": "Humans",
                 "sector": "Test Sector"},
            ],
            "campaigns": [
                {"campaignType": "liberation", "planet": {"index": 1}},
                {"campaignType": "defense", "planet": {"index": 2}},
            ],
        }
        objectives = tactical.compute_for(
            doc, 1, waypoints={"1": [2], "2": [1]}
        )
        self.assertEqual(
            [item["key"] for item in objectives],
            ["besieged", "circumvallation", "gambit", "libsector"],
        )
        linked = [target for item in objectives for target in item.get("target_links") or []]
        self.assertTrue(linked)
        self.assertTrue(all("planet.html?idx=2" in target["url"] for target in linked))

    def test_planet_text_shows_objectives_but_campaign_summary_does_not(self):
        doc = snapshot()
        doc["planets"][0]["currentOwner"] = "Automaton"
        doc["campaigns"] = [{
            "campaignType": "liberation",
            "type": 0,
            "faction": "Automaton",
            "planet": {"index": 45},
        }]
        with patch.object(tactical, "compute_for", return_value=[{
            "title": "对弈点",
            "text": "解放该星将结束相邻防御战役。",
            "target_links": [{
                "index": 46,
                "name": "相邻星",
                "url": "https://example.invalid/planet.html?idx=46",
            }],
        }]):
            planet_text = commands.planet_text(doc, doc["planets"][0])
            campaigns_text = commands.campaigns_text(doc)
        self.assertIn("战术目标", planet_text)
        self.assertIn("对弈点", planet_text)
        self.assertIn("planet.html?idx=46", planet_text)
        self.assertNotIn("战术目标", campaigns_text)


class DssParityTest(unittest.TestCase):
    def test_cooldown_tone_is_red(self):
        self.assertEqual(
            report_card._dss_action_tone({"is_cooldown": True}),
            report_card.RED,
        )
        self.assertEqual(
            report_card._dss_action_tone({"is_active": True}),
            report_card.GREEN,
        )
        self.assertEqual(report_card._dss_action_tone({}), report_card.GOLD)

    def test_models_use_runtime_action_fields_and_catalog_descriptions(self):
        models = data.dss_action_models(dss_document()["dss"], dss_variables())
        self.assertEqual(
            [item["name"] for item in models],
            ["飞鹰风暴", "轨道封锁", "重型军械分发"],
        )
        self.assertEqual(
            [item["image"] for item in models],
            ["eagle_storm.webp", "orbital_blockade.webp", "heavy_ordnance.webp"],
        )
        self.assertAlmostEqual(models[0]["progress"], 44.936162, places=5)
        self.assertEqual([item["phase"] for item in models],
                         ["募捐中", "已激活", "冷却中"])
        self.assertTrue(all(item["description"] for item in models))
        with patch.object(
            data,
            "_dss_catalog_entries",
            return_value=dss_variables()["categories"][0]["entries"],
        ):
            text = commands.dss_text(dss_document())
        for value in ("飞鹰风暴", "轨道封锁", "重型军械分发",
                      "飞鹰编队提供密集空中火力支援", "捐献进度"):
            self.assertIn(value, text)

    def test_dss_card_renders_all_three_as_separate_full_action_blocks(self):
        labels = []
        original = ImageDraw.ImageDraw.text

        def text_hook(draw, xy, value, *args, **kwargs):
            labels.append(str(value))
            return original(draw, xy, value, *args, **kwargs)

        variables = dss_variables()

        def get_site(url, *args, **kwargs):
            if url == report_card.DATA_URL:
                return dss_document()
            return variables

        with tempfile.TemporaryDirectory() as temp, \
             patch.object(report_card, "_site_get", side_effect=get_site), \
             patch.object(report_card, "_starmap_cn_map", return_value={}), \
             patch.object(ImageDraw.ImageDraw, "text", text_hook):
            path = report_card.render_dss_card(temp)
            self.assertTrue(path and Path(path).is_file())
            with Image.open(path) as image:
                self.assertEqual(image.width, report_card.W)
                self.assertLessEqual(image.height, 3200)

        rendered = "\n".join(labels)
        for value in (
            "飞鹰风暴", "轨道封锁", "重型军械分发",
            "飞鹰编队提供密集空中火力支援",
            "反舰导弹阻止敌军撤离并封锁增援",
            "超级驱逐舰获得额外重型军械支援",
            "每人每 1 小时最多捐赠 10 稀有样本",
            "空间站即将移动：27 小时 46 分 40 秒",
            "贡献度 / CONTRIBUTION", "44.9%",
        ):
            self.assertIn(value, rendered)
        self.assertEqual(rendered.count("空间站即将移动："), 1)
        self.assertNotIn("已筹集", rendered)
        self.assertNotIn("45%", rendered)
        self.assertIn("3 项同屏", rendered)
        self.assertNotIn("查看", rendered)

    def test_planet_command_card_contains_full_width_tactical_section(self):
        doc = snapshot()
        labels = []
        original = ImageDraw.ImageDraw.text

        def text_hook(draw, xy, value, *args, **kwargs):
            labels.append(str(value))
            return original(draw, xy, value, *args, **kwargs)

        objectives = [{
            "title": "对弈点",
            "text": "解放该星将立即结束相邻星球上的敌方防御战役。",
            "target_links": [{
                "index": 46,
                "name": "相邻星",
                "url": "https://example.invalid/planet.html?idx=46",
            }],
        }]
        with tempfile.TemporaryDirectory() as temp, \
             data.snapshot_scope(doc), \
             patch.object(report_card, "_starmap_cn_map", return_value={}), \
             patch.object(report_card, "_planet_intel", return_value=({}, {})), \
             patch.object(report_card, "_sector_maps", return_value={}), \
             patch.object(report_card, "_planet_icon_file", return_value=None), \
             patch.object(report_card, "_biome_landscape", return_value=None), \
             patch.object(report_card, "_planet_biome_name", return_value="Test Biome"), \
             patch.object(report_card, "_region_tables", return_value=({}, {})), \
             patch.object(report_card.tactical, "compute_for", return_value=objectives), \
             patch.object(ImageDraw.ImageDraw, "text", text_hook):
            path = report_card.render_planet_card("X-45", title_cn="X-45", out_dir=temp)
            self.assertTrue(path and Path(path).is_file())

        rendered = "\n".join(labels)
        self.assertIn("战术目标", rendered)
        self.assertIn("对弈点", rendered)
        self.assertIn("目标星球：相邻星", rendered)
        self.assertIn("planet.html?idx=46", rendered)


if __name__ == "__main__":
    unittest.main()

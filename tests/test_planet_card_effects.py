"""环境与作战词条：chip 下方必须带中文描述，且同一词条只出现一次。"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import framework

framework.install()

from astrbot_plugin_hd2_common import data  # noqa: E402
from astrbot_plugin_hd2_war_report import report_card as card  # noqa: E402
from test_core import snapshot  # noqa: E402


class EffectDescriptionTests(unittest.TestCase):
    def test_descriptions_are_read_from_local_variables_table(self):
        payload = {"categories": [
            {"name": "光能者宗派", "entries": [
                {"cn": "入侵舰队", "desc": "舰队已抵达。", "effect_ids": [100, 101]},
                {"cn": "空描述", "desc": "", "effect_ids": [102]},
            ]},
        ]}
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "hd2_variables.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            with patch.object(card, "_table_candidates", return_value=[path]):
                table = card._effect_descriptions()
        self.assertEqual(table[100], {"desc": "舰队已抵达。", "group": "光能者宗派"})
        self.assertEqual(table[101], {"desc": "舰队已抵达。", "group": "光能者宗派"})
        self.assertNotIn(102, table)  # 没描述的词条不值得占一行

    def test_items_dedupe_by_label_and_keep_the_first_description(self):
        desc = {10: {"desc": "", "group": "光能者宗派"},
                11: {"desc": "比无票者更暴力的选民。", "group": "光能者宗派"},
                12: {"desc": "", "group": "友军火力"}}
        items = card._effect_items(
            [10, 11, 12], cn_map={10: "无票者变种", 11: "无票者变种", 12: "友军火力"},
            grp={}, desc_map=desc)
        self.assertEqual([row["label"] for row in items], ["无票者变种", "友军火力"])
        self.assertEqual(items[0]["desc"], "比无票者更暴力的选民。")
        self.assertEqual(items[0]["group"], "光能者宗派")
        self.assertEqual(items[1]["desc"], "")

    def test_render_draws_each_effect_with_its_description(self):
        doc = snapshot()
        planet = next(row for row in doc["planets"] if row["name"] == "X-45")
        planet["activeEffects"] = [1234]  # 夹具本身不带词条，这里补一条
        ids = [row for row in (planet.get("activeEffects") or []) if row is not None]
        self.assertTrue(ids, "夹具星球没有 activeEffects")
        labels = {ids[0]: "测试词条"}
        descs = {ids[0]: {"desc": "测试词条的中文描述。", "group": "测试分组"}}
        drawn = []
        original = card.ImageDraw.ImageDraw.text

        def text(draw, xy, value, *args, **kwargs):
            drawn.append(str(value))
            return original(draw, xy, value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp, data.snapshot_scope(doc), \
                patch.object(card, "_starmap_cn_map", return_value={}), \
                patch.object(card, "_planet_intel", return_value=(labels, {})), \
                patch.object(card, "_effect_descriptions", return_value=descs), \
                patch.object(card, "_sector_maps", return_value={}), \
                patch.object(card, "_planet_icon_file", return_value=None), \
                patch.object(card, "_biome_landscape", return_value=None), \
                patch.object(card, "_planet_biome_name", return_value="Test Biome"), \
                patch.object(card, "_region_tables", return_value=({}, {})), \
                patch.object(card.ImageDraw.ImageDraw, "text", text):
            path = card.render_planet_card("X-45", title_cn="X-45", out_dir=temp)
        self.assertTrue(path)
        self.assertEqual(drawn.count("测试词条"), 1)
        self.assertEqual(drawn.count("测试分组"), 1)
        self.assertEqual(drawn.count("测试词条的中文描述。"), 1)


if __name__ == "__main__":
    unittest.main()

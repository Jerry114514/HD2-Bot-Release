import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from framework import Event, install

install()

from test_core import ROOT  # noqa: F401  (adds project root and plugins to sys.path)
from PIL import Image

from astrbot_plugin_hd2_common import commands
from astrbot_plugin_hd2_btk import demolition
from astrbot_plugin_hd2_btk.runtime_bridge import run as run_bridge
from astrbot_plugin_hd2_demolition import demolition_card, main as plugin


class DemolitionTests(unittest.TestCase):
    def test_command_alias_and_direct_verdict(self):
        self.assertEqual(commands.parse("/拆毁值 直击 EAT-17 非法广播塔"),
                         ("demolition", "直击 EAT-17 非法广播塔", False))
        view = demolition.build_view("直击 EAT-17 非法广播塔", run_bridge)
        self.assertEqual(view["kind"], "demolition")
        self.assertEqual(len(view["results"]), 1)
        self.assertEqual(view["results"][0]["mode"], "直击 · 射弹直击")
        self.assertTrue(view["results"][0]["verdict"]["ok"])

    def test_direct_and_explosive_are_compared_separately(self):
        view = demolition.build_view("EAT-17 炮舰设施", run_bridge)
        modes = [row["mode"] for row in view["results"]]
        self.assertTrue(any(row.startswith("直击") for row in modes))
        self.assertTrue(any(row.startswith("爆炸") for row in modes))
        self.assertFalse(any(row["verdict"]["ok"] for row in view["results"]))
        self.assertIn("当前列出的直击/爆炸路径均不能拆毁", view["text"])

    def test_source_only_compares_direct_and_explosion_against_common_targets(self):
        view = demolition.build_view("EAT-17", run_bridge)
        self.assertEqual(view["kind"], "demolition")
        self.assertEqual(view["view_mode"], "common_comparison")
        rows = {row["label"]: row for row in view["comparisons"]}
        self.assertTrue(rows["广播塔"]["direct"]["verdict"]["ok"])
        self.assertTrue(rows["广播塔"]["explosion"]["verdict"]["ok"])
        self.assertEqual(rows["构筑者通风口"]["direct"]["verdict"]["reason"], "badr")
        self.assertTrue(rows["构筑者通风口"]["explosion"]["verdict"]["ok"])
        self.assertFalse(rows["轨道加农炮"]["direct"]["verdict"]["ok"])
        self.assertFalse(rows["轨道加农炮"]["explosion"]["verdict"]["ok"])
        self.assertIn("直击无距离衰减", view["text"])
        self.assertIn("两者分别判定", view["text"])

    def test_source_comparison_can_be_filtered_to_one_damage_path(self):
        direct = demolition.build_view("直击 EAT-17", run_bridge)
        self.assertEqual(direct["mode"], "direct")
        self.assertNotIn("爆炸：", direct["text"])
        explosion = demolition.build_view("爆炸 EAT-17", run_bridge)
        self.assertEqual(explosion["mode"], "explosion")
        self.assertNotIn("直击：", explosion["text"])

    def test_explicit_source_lookup_keeps_detailed_catalog_text(self):
        view = demolition.build_view("来源 EAT-17", run_bridge)
        self.assertEqual(view["kind"], "info")
        self.assertIn("直击拆毁", view["text"])
        self.assertIn("爆炸拆毁", view["text"])

    def test_explosive_only_target_rejects_direct_hit(self):
        view = demolition.build_view("EAT-17 机器人构筑者（通风口）", run_bridge)
        results = {row["mode"]: row["verdict"] for row in view["results"]}
        direct = next(value for key, value in results.items() if key.startswith("直击"))
        explosion = next(value for key, value in results.items() if key.startswith("爆炸"))
        self.assertEqual(direct["reason"], "badr")
        self.assertTrue(explosion["ok"])

    def test_explosion_threshold_and_distance_curve(self):
        view = demolition.build_view("NUX-223 地狱火炸弹 巨石", run_bridge)
        self.assertTrue(view["results"][0]["verdict"]["ok"])
        self.assertEqual(view["results"][0]["verdict"]["maxDistance"], 17)
        curve = demolition.build_view(
            "爆炸 “飞鹰”500KG炸弹（延时爆炸） 炮舰设施 距离=20", run_bridge
        )
        self.assertEqual(curve["results"][0]["distanceDf"], 27)
        self.assertFalse(curve["results"][0]["verdict"]["ok"])

    def test_demolition_card_renders_png(self):
        view = demolition.build_view("NUX-223 地狱火炸弹 巨石", run_bridge)
        with tempfile.TemporaryDirectory() as temp:
            path = demolition_card.render_demolition_card(view, temp)
            self.assertIsInstance(path, str)
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                # 2026-11 卡片重构：save_pages 单页页脚条 64 -> 56px，整体高度 -8
                self.assertGreater(image.height, 550)
                self.assertLessEqual(image.width, 1080)

    def test_common_target_comparison_card_renders_png(self):
        view = demolition.build_view("EAT-17", run_bridge)
        with tempfile.TemporaryDirectory() as temp:
            path = demolition_card.render_demolition_card(view, temp)
            self.assertIsInstance(path, str)
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.width, 1080)
                self.assertGreater(image.height, 900)

    def test_plugin_uses_image_and_text_fallback(self):
        async def run():
            event = Event("/拆毁值 NUX-223 地狱火炸弹 巨石")
            return [item async for item in plugin.Hd2DemolitionPlugin(None).keyword_trigger(event)]
        with patch.object(plugin, "render", return_value=str(Path("fixture.png"))):
            output = asyncio.run(run())
        self.assertEqual(output, [("image", str(Path("fixture.png")))])
        with patch.object(plugin, "render", side_effect=ImportError("no image")):
            fallback = asyncio.run(run())
        self.assertEqual(fallback[0][0], "text")
        self.assertIn("拆毁值评估", fallback[0][1])


if __name__ == "__main__":
    unittest.main()

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
from astrbot_plugin_hd2_btk import btk_card, main as btk


class BtkTests(unittest.TestCase):
    def test_command_alias_and_options(self):
        self.assertEqual(commands.parse("/btk AR-23 武斗虫 头部"),
                         ("btk", "AR-23 武斗虫 头部", False))
        clean, options = btk._extract_options(
            "AR-23 武斗虫 头部 距离=20 角度=小角度 破片=3 波及=主体"
        )
        self.assertEqual(clean, "AR-23 武斗虫 头部")
        self.assertEqual(options["distance"], 20)
        self.assertEqual(options["angle"], 1)
        self.assertEqual(options["shrapnelHits"], 3)
        self.assertEqual(options["blastParts"], "主体")

    def test_real_sdk_compute_and_scan(self):
        output = btk.handle_command("AR-23 武斗虫 头部")
        self.assertIn("BTK 武器伤害测试", output)
        self.assertIn("武斗虫（低难） · 头部", output)
        self.assertIn("BTK：", output)
        self.assertNotIn("None", output)
        scan = btk.handle_command("卡阈值 P/40-K")
        self.assertIn("卡阈值扫描", scan)
        self.assertIn("BTK", scan)

    def test_menus_and_catalog(self):
        weapons, enemies = btk._data()
        self.assertEqual(len(weapons), 149)
        self.assertEqual(len(enemies), 118)
        self.assertIn("BTK 武器库", btk.handle_command("武器"))
        enemy = btk.handle_command("敌人 武斗虫")
        self.assertIn("头部", enemy)
        self.assertIn("致死", enemy)
        self.assertIn("E/AT-12", btk.handle_command("武器 防御工事"))
        self.assertIn("防空炮台", btk.handle_command("敌人 机器人"))

    def test_dot_weapon_and_difficulty_variants(self):
        low = btk.build_view("AR-23 武斗虫 头部")
        high = btk.build_view("AR-23 武斗虫（高难） 头部")
        self.assertEqual(low["enemy"]["enemy"], "武斗虫（低难）")
        self.assertEqual(high["enemy"]["enemy"], "武斗虫（高难）")
        dot = btk.build_view("DOT-FIRE 强袭虫")
        self.assertEqual(dot["kind"], "compute")
        self.assertEqual(dot["result"]["type"], "dot")
        self.assertIn("触发次数", dot["text"])
        self.assertIn("元素倍率", dot["text"])
        with tempfile.TemporaryDirectory() as temp:
            path = btk_card.render_btk_card(dot, temp)
            self.assertIsInstance(path, str)
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG")
        scan = btk.handle_command("卡阈值 DOT-FIRE")
        self.assertIn("不参与普通 BTK 卡阈值", scan)

    def test_compute_and_scan_render_as_single_cards(self):
        compute = btk.build_view("AR-23 武斗虫 头部")
        scan = btk.build_view("卡阈值 P/40-K")
        with tempfile.TemporaryDirectory() as temp:
            dot = btk.build_view("DOT-FIRE 强袭虫")
            for value, minimum in ((compute, 600), (scan, 700), (dot, 600)):
                path = btk_card.render_btk_card(value, temp)
                self.assertIsInstance(path, str)
                with Image.open(path) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertGreater(image.height, minimum)
                    self.assertLessEqual(image.width, 1080)

    def test_plugin_reply_uses_one_image_and_text_fallback(self):
        async def run():
            event = Event("/btk AR-23 武斗虫 头部")
            return [item async for item in btk.Hd2BtkPlugin(None).keyword_trigger(event)]
        with patch.object(btk, "render", return_value=str(Path("fixture.png"))):
            output = asyncio.run(run())
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0][0], "image")
        with patch.object(btk, "render", side_effect=ImportError("no image")):
            fallback = asyncio.run(run())
        self.assertEqual(fallback[0][0], "text")
        self.assertIn("BTK 武器伤害测试", fallback[0][1])


if __name__ == "__main__":
    unittest.main()

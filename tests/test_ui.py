# -*- coding: utf-8 -*-
"""common.ui 基建：Token 感知换行、流式画布测高与缓存页脚。"""
import os
import tempfile
import unittest

from test_core import ROOT  # noqa: F401  (adds project root and plugins to sys.path)

from PIL import Image, ImageDraw

from astrbot_plugin_hd2_common.ui import layout, pages
from astrbot_plugin_hd2_common.ui import tokens as T


class WrapTests(unittest.TestCase):
    def setUp(self):
        self.img = Image.new("RGB", (16, 16))
        self.dr = ImageDraw.Draw(self.img)
        self.font = T.F(14)

    def test_tokens_keep_units_whole(self):
        self.assertEqual(layout.tokens("50%"), ["50%"])
        self.assertEqual(layout.tokens("AR-23 发"), ["AR-23", " ", "发"])
        self.assertEqual(layout.tokens("X-45"), ["X-45"])

    def test_wrap_never_splits_percent_or_word(self):
        lines = layout.wrap_runs(self.dr, [("进度 50% 达成 AR-23", None)], self.font, 70)
        text = "".join("".join(tok for tok, _ in line) for line in lines)
        self.assertIn("50%", text)
        self.assertIn("AR-23", text)

    def test_ellipsize_stays_within_width(self):
        out = layout.ellipsize(self.dr, "超长中文名称测试文本", self.font, 40)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(self.dr.textlength(out, font=self.font), 40)


class CanvasTests(unittest.TestCase):
    def test_layout_is_monotonic_and_height_matches(self):
        c = layout.Canvas()
        start = c.y
        c.header("银河战争速报", "GALACTIC WAR REPORT")
        self.assertGreater(c.y, start)
        after_header = c.y
        c.section("重要指令", "MAJOR ORDER")
        c.kpi([("在线指挥官", "62,480", "3 战区活跃"), ("MO 进度", "2/3", "74.2%")], cols=2, h=96)
        self.assertGreater(c.y, after_header)
        c.banner("结论前置", "结论", tone="cyan")
        c.rows([("项目", "值"), ("项目二", "值二")], row_h=44)
        c.chips(["战略指令", "战略机遇", "次要指令"])
        img, height = c.done()
        self.assertEqual(img.width, T.W)
        self.assertGreaterEqual(height, c.y)
        self.assertLessEqual(height, T.TALL)


class PagesTests(unittest.TestCase):
    def test_render_cached_single_page_and_caption(self):
        with tempfile.TemporaryDirectory() as temp:
            out = os.path.join(temp, "card.png")

            def draw(dr, img):
                dr.rectangle([0, 0, 40, 40], fill=T.AMBER)
                return 120

            path = pages.render_cached(out, out + ".meta", "ui-rev-1", draw,
                                       width=320, tall=600, caption="静态卡页脚")
            with Image.open(path) as image:
                self.assertEqual(image.width, 320)
                self.assertGreater(image.height, 120)
            again = pages.render_cached(out, out + ".meta", "ui-rev-1", draw,
                                        width=320, tall=600, caption="静态卡页脚")
            self.assertEqual(path, again)

    def test_render_cached_bumps_on_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            out = os.path.join(temp, "card.png")

            def draw(dr, img):
                return 80

            first = pages.render_cached(out, out + ".meta", "rev-a", draw, width=320, tall=600)
            second = pages.render_cached(out, out + ".meta", "rev-b", draw, width=320, tall=600)
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()

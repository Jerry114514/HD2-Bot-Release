"""星球指挥台渲染缓存：A 区版式版本必须参与缓存键，改版后不得复用旧布局图片。"""
import os
import tempfile
import unittest
from unittest.mock import patch

import framework

framework.install()

from astrbot_plugin_hd2_war_report import report_card as card  # noqa: E402
from test_core import snapshot  # noqa: E402


class PlanetCardCacheTests(unittest.TestCase):
    def _render_rev(self, version):
        """渲染一次，截获传给 _render_cached 的缓存键（不真画图）。"""
        revs = []

        def capture(out_path, meta_path, rev, draw_fn, page_info=None, column_height=2100):
            revs.append(str(rev))
            return out_path

        doc = snapshot()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(card, "PLANET_CARD_LAYOUT_VERSION", version), \
                patch.object(card, "_site_get", return_value=doc), \
                patch.object(card, "_render_cached", side_effect=capture):
            card.render_planet_card("X-45", title_cn="X-45", out_dir=temp)
        self.assertTrue(revs, "render_planet_card 未调用 _render_cached")
        return revs[0]

    def test_layout_version_is_part_of_cache_key(self):
        old_rev = self._render_rev(1)
        new_rev = self._render_rev(2)
        self.assertIn("layout1", old_rev)
        self.assertIn("layout2", new_rev)
        self.assertNotEqual(old_rev, new_rev)

    def test_bumped_layout_version_redraws_instead_of_hitting_old_image(self):
        calls = []

        def draw_fn(draw, img):
            calls.append(1)
            draw.text((10, 10), "x")
            return 120

        with tempfile.TemporaryDirectory() as temp:
            out = os.path.join(temp, "planet_x45.png")
            meta = out + ".meta"
            key_v1 = "planet-tactical-v2:layout1:2026-10-30T01:00:00Z:X-45"
            key_v2 = "planet-tactical-v2:layout2:2026-10-30T01:00:00Z:X-45"
            first = card._render_cached(out, meta, key_v1, draw_fn)
            again = card._render_cached(out, meta, key_v1, draw_fn)
            bumped = card._render_cached(out, meta, key_v2, draw_fn)
        # 同一版式第二次命中缓存不再重画；版式版本一变必须重画成新文件
        self.assertEqual(len(calls), 2)
        self.assertEqual(first, again)
        self.assertNotEqual(first, bumped)


if __name__ == "__main__":
    unittest.main()

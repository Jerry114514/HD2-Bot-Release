from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from framework import install

install()

from test_core import ROOT  # noqa: F401  (adds project root and plugins to sys.path)

from astrbot_plugin_hd2_war_report import report_card as card


class HelpLayoutTests(unittest.TestCase):
    def test_help_includes_complete_btk_parameters(self):
        descriptions = dict(
            (label, desc)
            for _title, _subtitle, rows in card.HELP_SECTIONS
            for label, desc in rows
        )
        btk_params = descriptions["BTK 可选参数"]
        for value in ("距离", "角度", "破片", "波及"):
            self.assertIn(value, btk_params)

    def test_help_card_wraps_long_commands_and_descriptions(self):
        captured = []
        original = card.ImageDraw.ImageDraw.text

        def text_hook(draw, xy, value, *args, **kwargs):
            captured.append((str(value), xy))
            return original(draw, xy, value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp, \
             patch.object(card.ImageDraw.ImageDraw, "text", text_hook):
            path = card.render_help_card(temp)
            paths = path if isinstance(path, list) else [path]
            self.assertTrue(paths and all(Path(item).is_file() for item in paths))
            with card.Image.open(paths[0]) as image:
                self.assertEqual(image.width, card.W)
        lines = [value for value, _xy in captured]
        self.assertIn("BTK 可选参数", lines)
        joined = "\n".join(lines)
        for value in ("距离", "角度", "破片", "波及", "查看直击来源"):
            self.assertIn(value, joined)


if __name__ == "__main__":
    unittest.main()

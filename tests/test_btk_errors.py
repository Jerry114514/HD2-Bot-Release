import unittest

from framework import install

install()

from test_core import ROOT  # noqa: F401  (adds project root and plugins to sys.path)

from astrbot_plugin_hd2_btk import main as btk


class BtkErrorGuidanceTests(unittest.TestCase):
    def test_errors_offer_btk_and_wiki_recovery_paths(self):
        cases = (
            ("XX-999 武斗虫 头部", "未识别武器", "/wiki"),
            ("AR-23 不存在虫 头部", "未识别敌人", "/wiki 不存在虫"),
            ("AR-23 武斗虫", "缺少部位", "/wiki 武斗虫"),
            ("AR-23 武斗虫 引擎", "未找到部位「引擎」", "/wiki 武斗虫"),
        )
        for command, expected, hint in cases:
            with self.subTest(command=command):
                with self.assertRaises(ValueError) as raised:
                    btk.handle_command(command)
                message = str(raised.exception)
                self.assertIn(expected, message)
                self.assertIn(hint, message)
                self.assertIn("/btk", message)


if __name__ == "__main__":
    unittest.main()

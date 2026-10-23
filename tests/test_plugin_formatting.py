# -*- coding: utf-8 -*-
"""指令格式化测试：插件格式化函数在站点数据（夹具）+ 全断网环境下不报错、输出关键字段"""
import json
import os
import unittest
from unittest import mock

from conftest import ROOT  # noqa: F401  (astrbot 桩 + sys.path)
import hd2_datasource as ds

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "site_doc.json")


def _raise_net(*a, **k):
    raise OSError("测试环境禁止真实网络")


class _NetGuard:
    """把两个插件的全部网络出口都换成 raise，保证用例纯离线"""

    def __init__(self):
        self._saved = []

    def __enter__(self):
        import astrbot_plugin_hd2_war_report.main as wr
        import astrbot_plugin_hd2_planet_info.main as pi
        import hd2_datasource as ds
        with open(FIXTURE, encoding="utf-8") as f:
            self.doc = json.load(f)
        targets = [
            (ds, "site_doc", lambda force=False: self.doc),
            (ds, "fetch_json", _raise_net),
            (ds, "fetch_official", _raise_net),
            (ds, "recent_index", lambda force=False: None),
            (ds, "recent_planet_frames", lambda idx: []),
            (ds, "recent_region_frames", lambda: []),
            (ds, "recent_assignment_frames", lambda: []),
            (ds, "transnews", lambda force=False: {}),
            (ds, "campaign_zh", lambda force=False: {}),
            (wr, "_fetch_json", _raise_net),
            (wr, "_fetch_official", _raise_net),
            (wr, "_load_warp_routes", lambda: {}),
            (wr, "_load_planet_map", lambda: {}),
            (wr, "_load_planet_table", lambda force=False: ({}, "test")),
        ]
        for obj, name, repl in targets:
            self._saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, repl)
        return self

    def __exit__(self, *exc):
        for obj, name, orig in reversed(self._saved):
            setattr(obj, name, orig)
        return False


class TestWarReportFormatting(unittest.TestCase):
    def setUp(self):
        import astrbot_plugin_hd2_war_report.main as wr
        self.wr = wr

    def test_format_mo_site(self):
        with _NetGuard():
            a = ds.mo_assignment(self.wr._site_doc())
            out = self.wr._format_mo_site(a, {}, None, None)
        self.assertIn("🎖️ 重要指令", out)
        self.assertIn("📋", out)               # 站点现成中文简报
        self.assertIn("📊 任务进度", out)
        self.assertIn("⏳ 剩余", out)           # deadline - serverTime
        self.assertNotIn("None", out)

    def test_format_mo_site_without_forecast_window(self):
        with _NetGuard():
            a = dict(ds.mo_assignment(self.wr._site_doc()))
            a.pop("deadline", None)             # 缺 deadline -> 无预测但不报错
            out = self.wr._format_mo_site(a, {}, None, None)
        self.assertIn("🎖️ 重要指令", out)
        self.assertNotIn("None", out)

    def test_warzone_distribution_site(self):
        with _NetGuard():
            out = self.wr._get_warzone_distribution()
        self.assertIn("战区玩家分布", out)
        self.assertIn("总计", out)
        self.assertIn("光能者", out)

    def test_campaigns_site_liberation_and_defense(self):
        with _NetGuard():
            pm = ds.planet_map(self.wr._site_doc())
            doc = self.wr._site_doc()
            camps = ds.campaigns(doc)
            self.assertEqual(len(camps), 2)
            lib = self.wr._camp_line_site(camps[0], pm, doc)
            dfn = self.wr._camp_line_site(camps[1], pm, doc)
        self.assertIn("解放", lib)
        self.assertIn("防御战", dfn)
        self.assertIn("预测", dfn)
        self.assertIn("我方", dfn)

    def test_campaigns_site_empty_falls_back_official(self):
        with _NetGuard():
            doc = dict(self.wr._site_doc())
            doc["campaigns"] = []
            ds.site_doc = lambda force=False: doc
            try:
                out = self.wr._get_campaigns({})
            finally:
                pass
        # 站点 campaigns 为空时走官方 API（本测试里网络被禁 -> 返回错误提示而非崩溃）
        self.assertTrue(out.startswith("⚠️") or "当前战线" in out or "当前无" in out)

    def test_format_dss_site(self):
        with _NetGuard():
            d = ds.dss(self.wr._site_doc())
            out = self.wr._format_dss_site(d, {})
        self.assertIn("DSS 民主空间站", out)
        self.assertIn("当前停靠", out)
        self.assertIn("战术行动", out)
        self.assertIn("已筹集", out)            # 捐献进度
        self.assertIn("申购点", out)            # 资源限额
        self.assertIn("下一轮投票剩余", out)
        self.assertNotIn("None", out)

    def test_dss_no_station_is_graceful(self):
        with _NetGuard():
            out = self.wr._format_dss_site({"planetIndex": None}, {})
        self.assertIn("DSS", out)

    def test_ownership_changes_render(self):
        with _NetGuard():
            out = self.wr._get_ownership_changes(24)
        self.assertIn("星图归属变化", out)
        self.assertIn("次易主", out)
        self.assertIn("防守成功", out)
        self.assertIn("超级地球", out)
        self.assertNotIn("None", out)

    def test_ownership_changes_empty(self):
        with _NetGuard():
            doc = dict(self.wr._site_doc())
            doc["ownership_history"] = {"events": [], "defenseEvents": []}
            ds.site_doc = lambda force=False: doc
            out = self.wr._get_ownership_changes(24)
        self.assertIn("无阵营变化或防守成功", out)

    def test_strategic_line(self):
        with _NetGuard():
            line = self.wr._strategic_line()
        self.assertTrue(line == "" or line.startswith("🎯 战略机遇"))

    def test_build_report_parts_gap_quality(self):
        with _NetGuard() as g:
            wr = self.wr
            orig_mo = wr._get_major_order
            orig_news = wr._get_news_items
            orig_wz = wr._get_warzone_distribution
            orig_mocache = wr._load_mo_cache
            wr._get_major_order = lambda *a, **k: "🎖️ 重要指令\n📋 当前没有进行中的重要指令。"
            wr._get_news_items = lambda limit=1: []
            wr._get_warzone_distribution = lambda: ""
            wr._load_mo_cache = lambda: None  # 隔离冒烟留下的真实 MO 缓存
            try:
                text, quality = wr._build_report_parts({}, None, "")
            finally:
                wr._get_major_order = orig_mo
                wr._get_news_items = orig_news
                wr._get_warzone_distribution = orig_wz
                wr._load_mo_cache = orig_mocache
        self.assertEqual(quality, "gap")
        self.assertIn("重要指令", text)


class TestPlanetInfoFormatting(unittest.TestCase):
    def setUp(self):
        import astrbot_plugin_hd2_planet_info.main as pi
        self.pi = pi

    def test_translate_planet_info_with_regions(self):
        with _NetGuard():
            info = self.pi._fetch_planet_site(7)
            self.assertIsNotNone(info, "站点星球 7 应能取到详情")
            out = self.pi._translate_planet_info(info)
        self.assertIn("敌方抵抗度", out)
        self.assertIn("行动变量", out)
        self.assertIn("区域", out)
        self.assertIn("新科布", out)     # 区域中文名来自 planet_regions_zh.json
        self.assertNotIn("None", out)

    def test_translate_planet_info_minimal(self):
        info = {"resistance_pct": "2.94%", "resistance_level": "DIRE"}
        out = self.pi._translate_planet_info(info)
        self.assertIn("敌方抵抗度", out)
        self.assertIn("危急", out)

    def test_resistance_levels(self):
        self.assertEqual(self.pi._resistance_level(6.0), "UNBREAKABLE")
        self.assertEqual(self.pi._resistance_level(2.5), "DIRE")
        self.assertEqual(self.pi._resistance_level(0.2), "LOW")
        self.assertEqual(self.pi._resistance_level(0.0), "NONE")

    def test_slug_index_from_site_map(self):
        with _NetGuard():
            idx = self.pi._slug_to_index("zea_rugosia")
        self.assertEqual(idx, 7)


class TestCommonTables(unittest.TestCase):
    def test_effect_id_cn_from_site_table(self):
        """主站 effect_id_cn.json（191 条）可加载且包含旧表没有的 ID"""
        from astrbot_plugin_hd2_common.main import load_effect_id_cn
        m = load_effect_id_cn()
        self.assertGreaterEqual(len(m), 150)
        for k in (87, 1129, 1402):
            self.assertIn(k, m)

    def test_starmap_sectors_loaded(self):
        from astrbot_plugin_hd2_common.main import load_starmap_sectors
        m = load_starmap_sectors()
        self.assertIn("ZEA RUGOSIA", m)
        self.assertEqual(m["ZEA RUGOSIA"], "费里斯")

    def test_planet_name_cn_covers(self):
        from astrbot_plugin_hd2_common.main import PLANET_NAME_CN
        self.assertEqual(PLANET_NAME_CN.get("MALEVELON CREEK"), "麦拉芬蒙河")


if __name__ == "__main__":
    unittest.main()

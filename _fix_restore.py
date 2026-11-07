# -*- coding: utf-8 -*-
"""找回 briefing 卡（3c907ef 版）+ 拆分五卡导入 + 战术行动卡修正"""
import re

# ── 1) report_card.py：从 3c907ef 找回 _banner_file + render_briefing_card ──
old = open(r"/tmp/rc_3c907ef.py", encoding="utf-8").read()
i0 = old.find("def _banner_file(")
i1 = old.find("\ndef ", old.find("def render_briefing_card("))
assert i0 > 0 and i1 > i0, (i0, i1)
brief_seg = old[i0:i1]

p = "plugins/astrbot_plugin_hd2_war_report/report_card.py"
b = open(p, encoding="utf-8").read()
assert "render_briefing_card" not in b, "briefing 意外存在，请人工核对"
anchor = "\nif __name__ == \"__main__\":"
assert anchor in b
b = b.replace(anchor, "\n" + brief_seg + anchor, 1)
open(p, "w", encoding="utf-8").write(b)
print("briefing 段已找回")

# ── 2) main.py：五卡导入拆成每卡独立 try（一张缺不拖垮全部） ──
p2 = "plugins/astrbot_plugin_hd2_war_report/main.py"
m = open(p2, encoding="utf-8").read()
old_import = '''try:
    from astrbot_plugin_hd2_war_report.report_card import (
        render_report_card as _render_card,
        render_dss_card as _render_dss_card,
        render_campaigns_card as _render_campaigns_card,
        render_planet_card as _render_planet_card,
        render_briefing_card as _render_briefing_card,
    )
except Exception:
    try:
        import importlib.util as _ilu
        _rc_spec = _ilu.spec_from_file_location(
            "hd2_report_card",
            _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "report_card.py"))
        _rc_mod = _ilu.module_from_spec(_rc_spec)
        _rc_spec.loader.exec_module(_rc_mod)
        _render_card = _rc_mod.render_report_card
        _render_dss_card = _rc_mod.render_dss_card
        _render_campaigns_card = _rc_mod.render_campaigns_card
        _render_planet_card = _rc_mod.render_planet_card
        _render_briefing_card = _rc_mod.render_briefing_card
    except Exception:
        _render_card = _render_dss_card = _render_campaigns_card = None
        _render_planet_card = _render_briefing_card = None'''
new_import = '''# 图片卡导入：每卡独立 try——一张缺失/损坏不拖垮其他卡（全部回退文本）
try:
    from astrbot_plugin_hd2_war_report.report_card import render_report_card as _render_card
except Exception:
    _render_card = None
try:
    from astrbot_plugin_hd2_war_report.report_card import render_dss_card as _render_dss_card
except Exception:
    _render_dss_card = None
try:
    from astrbot_plugin_hd2_war_report.report_card import render_campaigns_card as _render_campaigns_card
except Exception:
    _render_campaigns_card = None
try:
    from astrbot_plugin_hd2_war_report.report_card import render_planet_card as _render_planet_card
except Exception:
    _render_planet_card = None
try:
    from astrbot_plugin_hd2_war_report.report_card import render_briefing_card as _render_briefing_card
except Exception:
    _render_briefing_card = None'''
assert old_import in m, "main import anchor missing"
m = m.replace(old_import, new_import, 1)
open(p2, "w", encoding="utf-8").write(m)
print("main 导入块已拆分")

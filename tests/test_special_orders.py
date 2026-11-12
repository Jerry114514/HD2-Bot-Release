import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from framework import Event, install

install()

from test_core import ROOT  # noqa: F401  (adds project root and plugins to sys.path)
from PIL import Image, ImageDraw

from astrbot_plugin_hd2_common import commands, orders
from astrbot_plugin_hd2_war_report import main as war, report_card
from astrbot_plugin_hd2_war_report.mo_seen import MoSeenStore


def special_document():
    planets = [
        {"index": 170, "name": "FORI PRIME", "currentOwner": "Humans"},
        {"index": 270, "name": "FRONTERIA", "currentOwner": "Humans"},
        {"index": 78, "name": "CRIMSICA", "currentOwner": "Automaton"},
        {"index": 128, "name": "DARIUS II", "currentOwner": "Humans"},
        {"index": 130, "name": "ACHERNAR SECUNDUS", "currentOwner": "Automaton"},
    ]
    main = {
        "id": 1636665659,
        "title": "MAJOR ORDER",
        "briefing": "Main MO English fallback.",
        "tasks": [
            {"type": 3, "values": [2, 0, 700000000], "valueTypes": [1, 2, 3]},
            {"type": 13, "values": [1, 1, 170], "valueTypes": [3, 11, 12]},
        ],
        "progress": [588373049, 1],
        "startedAt": "2026-11-03T13:00:13Z",
        "deadline": "2026-11-08T13:00:33Z",
    }
    directive = {
        "id": 3504692473,
        "title": "STRATEGIC THREAT",
        "briefing": "Investigate and hold the designated planets.",
        "tasks": [
            {"type": 13, "values": [1, 1, index], "valueTypes": [3, 11, 12]}
            for index in (270, 78, 128, 130)
        ],
        "progress": [0, 0, 0, 0],
        "startedAt": "2026-11-04T12:50:13Z",
        "deadline": "2026-11-08T12:50:29Z",
    }
    directive_zh = (
        "<i=3>新战略指令</i>\n\n"
        "矛盾的是，关于TCS+遭突破的报告仍在继续增加。\n\n"
        "绝地潜兵请注意：展开调查，如有必要，"
        "<i=1>守住边陲、克里姆西卡、大流士II和水委一次星</i>。"
    )
    opportunity_zh = (
        "<i=3>抓住战略机遇</i>\n\n"
        "绝地潜兵已遏制在<i=1>尼维尔43</i>发生的暴乱，"
        "所有企业均可恢复营业。"
    )
    return {
        "fetchedAt": "2026-11-05T00:00:00Z",
        "war": {"warId": 801},
        "assignments": main,
        "assignments_all": [main, directive],
        "major_order": {
            "id32": main["id"],
            "translated_brief": "主重要指令中文简版",
            "tasks": {"items": [
                {"type": 3, "action": "击杀", "faction_name": "终结族",
                 "goal": 700000000, "progress": 588373049},
                {"type": 13, "action": "坚守", "goal": 1, "progress": 1},
            ]},
        },
        "strategic": {
            "active": False,
            "status": "seized",
            "status_cn": "已夺取",
            "dispatch_id": 3971,
        },
        "dispatches": [
            {"id": 3989, "published": "2026-11-04T12:50:30Z",
             "message": "<i=3>NEW STRATEGIC DIRECTIVE</i>\n\nEnglish directive."},
            {"id": 3986, "published": "2026-11-03T13:00:30Z",
             "message": "<i=3>NEW MAJOR ORDER</i>\n\nMain MO."},
            {"id": 3984, "published": "2026-11-02T18:09:01Z",
             "message": "<i=3>STRATEGIC UPDATE</i>\n\nOrbital Laser ended."},
            {"id": 3982, "published": "2026-11-02T12:14:28Z",
             "message": "<i=3>MINOR TERMINID OUTBREAK</i>\n\nFori Prime."},
            {"id": 3971, "published": "2026-11-02T18:57:46Z",
             "message": "<i=3>STRATEGIC OPPORTUNITY SEIZED</i>\n\nSeized."},
            {"id": 3970, "published": "2026-10-28T10:40:06Z",
             "message": "<i=3>NEW STRATEGIC OPPORTUNITY</i>\n\nOpening."},
        ],
        "news": [
            {"id": 3989, "official_message": directive_zh},
            {"id": 3986, "official_message": "<i=3>新重要指令</i>\n\n当期主指令"},
            {"id": 3984, "translated": "战略更新\n\n轨道激光结束"},
            {"id": 3982, "translated": "小规模终结族暴乱\n\n佛里主星"},
        ],
        "official_texts": {
            "dispatches": [
                {"id": 3989, "raw_message": directive_zh, "translated": directive_zh},
                {"id": 3971, "raw_message": opportunity_zh, "translated": opportunity_zh},
                {"id": 3970, "raw_message": "全新战略机遇\n\n开局公告", "translated": "全新战略机遇\n\n开局公告"},
            ],
            "assignments": [
                {"id32": directive["id"], "title": "战略威胁",
                 "translated": directive["briefing"]},
            ],
        },
        "planets": planets,
        "campaigns": [],
        "planet_events": [],
    }


class SpecialOrderTests(unittest.TestCase):
    def test_strategic_directive_uses_current_dispatch_and_four_hold_targets(self):
        models = orders.special_orders(special_document())
        directive = models["directive"]
        self.assertTrue(directive["active"])
        self.assertEqual(directive["dispatch"]["id"], 3989)
        self.assertIn("矛盾的是，关于TCS+遭突破", directive["body"])
        self.assertEqual([row["planet_index"] for row in directive["tasks"]], [270, 78, 128, 130])
        self.assertEqual([row["planet_name"].replace(" ", "") for row in directive["tasks"]],
                         ["边陲", "克里姆西卡", "大流士II", "水委一次星"])
        self.assertEqual([row["state_cn"] for row in directive["tasks"]],
                         ["已守住", "待守", "已守住", "待守"])
        self.assertTrue(all("planet.html?idx=" in row["url"] for row in directive["tasks"]))

    def test_strategic_opportunity_uses_lifecycle_status_and_official_text(self):
        opportunity = orders.special_orders(special_document())["opportunity"]
        self.assertFalse(opportunity["active"])
        self.assertEqual(opportunity["status_cn"], "已夺取")
        self.assertEqual(opportunity["status_en"], "SEIZED")
        self.assertEqual(opportunity["dispatch"]["id"], 3971)
        self.assertIn("所有企业均可恢复营业", opportunity["body"])
        self.assertEqual(opportunity["opening_dispatch"]["id"], 3970)
        self.assertEqual(opportunity["tasks"], [])

    def test_minor_order_fallback_is_explicit_and_uses_fori_prime(self):
        minor = orders.special_orders(special_document())["minor"]
        self.assertTrue(minor["fallback"])
        self.assertEqual(minor["title"], "重要指令附加目标")
        self.assertEqual([(task["planet_index"], task["state_cn"]) for task in minor["tasks"]],
                         [(170, "已守住")])
        text = orders.special_orders_text(special_document())
        self.assertIn("重要指令附加目标（不是官方独立 MINOR ORDER）", text)

    def test_independent_minor_order_takes_precedence(self):
        doc = special_document()
        doc["assignments_all"].append({
            "id": 42,
            "title": "MINOR ORDER",
            "briefing": "Hold FRONTERIA.",
            "tasks": [{"type": 13, "values": [1, 1, 270], "valueTypes": [3, 11, 12]}],
            "progress": [0],
            "deadline": "2026-11-08T00:00:00Z",
        })
        minor = orders.special_orders(doc)["minor"]
        self.assertFalse(minor.get("fallback", False))
        self.assertEqual(minor["assignment"]["id"], 42)

    def test_news_filter_removes_only_dedicated_dispatches(self):
        ids = [item["id"] for item in orders.filtered_news(special_document())]
        self.assertNotIn(3989, ids)
        self.assertNotIn(3986, ids)
        self.assertNotIn(3971, ids)
        self.assertNotIn(3970, ids)
        self.assertIn(3984, ids)
        self.assertIn(3982, ids)

    def test_missing_planet_keeps_stable_target_id_and_link(self):
        doc = special_document()
        doc["planets"] = [row for row in doc["planets"] if row["index"] != 78]
        task = orders.special_orders(doc)["directive"]["tasks"][1]
        self.assertEqual(task["planet_name"], "星球 #78")
        self.assertEqual(task["state_cn"], "待守")
        self.assertIn("planet.html?idx=78", task["url"])

    def test_empty_channels_have_stable_states(self):
        doc = {"fetchedAt": "2026-11-05T00:00:00Z", "assignments_all": [],
               "assignments": {}, "strategic": {}, "dispatches": [], "news": []}
        models = orders.special_orders(doc)
        self.assertTrue(all(models[key]["empty"] for key in ("directive", "opportunity", "minor")))
        text = orders.special_orders_text(doc, models)
        self.assertEqual(text.count("当前无"), 3)

    def test_all_channels_leave_at_exactly_seven_days(self):
        published = "2026-11-01T12:00:00Z"
        doc = {
            "fetchedAt": "2026-11-08T12:00:00Z",
            "assignments_all": [
                {"id": 1, "title": "STRATEGIC DIRECTIVE", "tasks": [{"type": 13}],
                 "deadline": "2026-11-20T00:00:00Z"},
                {"id": 2, "title": "MINOR ORDER", "tasks": [{"type": 13}],
                 "deadline": "2026-11-20T00:00:00Z"},
                {"id": 3, "title": "MAJOR ORDER", "tasks": [{"type": 13}],
                 "deadline": "2026-11-20T00:00:00Z"},
            ],
            "assignments": {"id": 3, "tasks": [{"type": 13}]},
            "strategic": {"active": True, "dispatch_id": 12},
            "dispatches": [
                {"id": 11, "published": published,
                 "message": "NEW STRATEGIC DIRECTIVE\n\nDirective."},
                {"id": 12, "published": published,
                 "message": "NEW STRATEGIC OPPORTUNITY\n\nOpportunity."},
                {"id": 13, "published": published,
                 "message": "MINOR ORDER\n\nMinor."},
            ],
            "news": [],
        }
        models = orders.special_orders(doc)
        self.assertTrue(all(models[key]["empty"] for key in ("directive", "opportunity", "minor")))
        text = orders.special_orders_text(doc, models)
        for label in ("战略指令", "战略机遇", "次要指令"):
            self.assertIn("当前无" + label, text)
        self.assertNotIn("发布满 7 天", text)

    def test_channels_remain_visible_one_second_before_seven_days(self):
        doc = {
            "fetchedAt": "2026-11-08T11:59:59Z",
            "assignments_all": [
                {"id": 1, "title": "STRATEGIC DIRECTIVE", "tasks": [{"type": 13}],
                 "deadline": "2026-11-20T00:00:00Z"},
                {"id": 2, "title": "MINOR ORDER", "tasks": [{"type": 13}],
                 "deadline": "2026-11-20T00:00:00Z"},
            ],
            "assignments": {},
            "strategic": {"active": True, "dispatch_id": 22},
            "dispatches": [
                {"id": 21, "published": "2026-11-01T12:00:00Z",
                 "message": "NEW STRATEGIC DIRECTIVE\n\nDirective."},
                {"id": 22, "published": "2026-11-01T12:00:00Z",
                 "message": "NEW STRATEGIC OPPORTUNITY\n\nOpportunity."},
                {"id": 23, "published": "2026-11-01T12:00:00Z",
                 "message": "MINOR ORDER\n\nMinor."},
            ],
            "news": [],
        }
        models = orders.special_orders(doc)
        self.assertFalse(any(models[key]["empty"] for key in ("directive", "opportunity", "minor")))

    def test_special_order_card_renders_targets_and_fallback_disclaimer(self):
        doc = special_document()
        labels = []
        original = ImageDraw.ImageDraw.text

        def text_hook(draw, xy, value, *args, **kwargs):
            labels.append(str(value))
            return original(draw, xy, value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(report_card, "_site_get", return_value=doc), \
                 patch.object(report_card, "_banner_file", return_value=None), \
                 patch.object(ImageDraw.ImageDraw, "text", text_hook):
                path = report_card.render_major_order_card(temp)
                self.assertTrue(path and Path(path).is_file())
        rendered = "\n".join(labels)
        for value in ("战略指令", "战略机遇", "次要指令", "边陲", "已夺取",
                      "重要指令附加目标", "planet.html?idx=270"):
            self.assertIn(value, rendered)
        self.assertNotIn("<i=", rendered)

    def test_mo_announcement_once_per_user_and_manual_replay_still_works(self):
        doc = special_document()
        mo_view = commands.Result(
            "mo", "MO", doc,
        )
        dss_view = commands.Result(
            "dss", "DSS", doc,
        )

        def result(kind, query="", doc=None):
            return mo_view if kind == "mo" else dss_view

        def render(view):
            return "mo.png" if view.kind == "mo" else "dss.png"

        async def run(plugin, event):
            return [item async for item in plugin.keyword_trigger(event)]

        with tempfile.TemporaryDirectory() as temp:
            plugin = war.Hd2WarReportPlugin(object())
            plugin._mo_seen = MoSeenStore(Path(temp) / "mo_seen.json")
            with patch.object(commands, "result", side_effect=result), \
                 patch.object(commands, "render_result", side_effect=render), \
                 patch.object(war, "_log"):
                first = asyncio.run(run(plugin, Event("/dss")))
                second = asyncio.run(run(plugin, Event("/dss")))
                replay = asyncio.run(run(plugin, Event("/重要指令")))
        self.assertEqual(first, [("image", "mo.png"), ("image", "dss.png")])
        self.assertEqual(second, [("image", "dss.png")])
        self.assertEqual(replay, [("image", "mo.png")])


if __name__ == "__main__":
    unittest.main()

"""Dispatch rich-text parity with the site: <i=1>/<i=3>, bare <i>, <ci=N>, safety."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framework import install

install()

from test_core import ROOT  # noqa: F401  (adds project root and plugins to sys.path)
from PIL import Image, ImageDraw

from astrbot_plugin_hd2_common import commands, data, orders
from astrbot_plugin_hd2_war_report import report_card


def major_order_document():
    return {
        "fetchedAt": "2026-11-05T02:00:00Z",
        "war": {"warId": 801},
        "assignments": {
            "id": 11,
            "startTime": "2026-11-05T00:00:00Z",
            "deadline": "2026-11-08T00:00:00Z",
            "briefing": "Assignment English fallback.",
            "tasks": [{"type": 3, "values": [2, 0, 100], "valueTypes": [1, 2, 3]}],
            "progress": [50],
        },
        "major_order": {"id32": 11},
        "dispatches": [{
            "id": 100,
            "published": "2026-11-05T01:00:00Z",
            "message": "<i=3>NEW MAJOR ORDER</i>\n\nEnglish body.",
        }],
        "news": [{
            "id": 100,
            "official_message": "<i=3>官方标题</i>\n\n官方<i=1>重点</i>",
            "raw_message": "<i=3>RAW</i>\n\nRAW body",
            "message": "<i=3>MESSAGE</i>\n\nEnglish body",
            "translated": "翻译纯文本",
            "message_zh": "旧翻译纯文本",
        }],
    }


class RichTextParserTest(unittest.TestCase):
    def test_plain_text_strips_markup_and_decodes_entities(self):
        text = data._plain_rich_text(
            "<i=1>黄</i><i=3>蓝</i><ci=1>旧黄</ci><b>粗</b>A&amp;B"
        )
        self.assertEqual(text, "黄蓝旧黄粗A&B")
        self.assertNotIn("<", text)

    def test_runs_map_one_three_bare_and_legacy_ci(self):
        runs = data.rich_text_runs(
            "<i=1>黄</i><i=3>蓝</i><i>裸</i>·<ci=1>旧黄</ci><ci=3>旧蓝</ci><i=9>未知</i>"
        )
        self.assertEqual(runs, [
            ("黄", "yellow"),
            ("蓝", "blue"),
            ("裸", "yellow"),
            ("·", None),
            ("旧黄", "yellow"),
            ("旧蓝", "blue"),
            ("未知", None),
        ])

    def test_adjacent_same_tone_runs_are_merged(self):
        self.assertEqual(data.rich_text_runs("<i=1>甲</i><i=1>乙</i>"), [("甲乙", "yellow")])

    def test_split_keeps_title_out_of_body_and_body_tone(self):
        title, body, runs = data.split_rich_text("<i=3>标题</i>\n\n正文<i=1>重点</i>")
        self.assertEqual(title, "标题")
        self.assertEqual(body, "正文重点")
        self.assertEqual(runs, [("正文", None), ("重点", "yellow")])

    def test_script_and_style_blocks_are_removed(self):
        runs = data.rich_text_runs(
            "<script>alert(1)</script><style>p{}</style>正文"
        )
        self.assertEqual(runs, [("正文", None)])

    def test_img_and_unknown_tags_never_leak_markup(self):
        text = data._plain_rich_text('<img src=x onerror="alert(1)">正文<div>换行</div>')
        self.assertEqual(text, "正文换行")
        self.assertNotIn("<", text)
        self.assertNotIn("onerror", text)

    def test_unclosed_tag_is_stripped_not_restored(self):
        self.assertEqual(data._plain_rich_text("<i=3>未闭合"), "未闭合")

    def test_tone_marker_in_source_cannot_break_runs(self):
        self.assertEqual(data.rich_text_runs("\x01Y\x01伪造"), [("伪造", None)])


class RichSourcePriorityTest(unittest.TestCase):
    def test_major_order_prefers_official_then_raw_then_message(self):
        doc = major_order_document()
        content = data.major_order_content(doc)
        self.assertEqual(content["source"], "official_message")
        self.assertEqual(content["text"], "官方标题\n\n官方重点")
        self.assertIn(("重点", "yellow"), content["rich_text"])

        for field, source in (
            ("official_message", "raw_message"),
            ("raw_message", "message"),
            ("message", "translated"),
        ):
            doc["news"][0].pop(field)
            content = data.major_order_content(doc)
            self.assertEqual(content["source"], source)

    def test_major_order_official_outranks_raw_on_another_object(self):
        doc = major_order_document()
        doc["news"][0].pop("official_message")
        doc["major_order"]["official_message"] = (
            "<i=3>官方标题</i>\n\n官方<i=1>重点</i>"
        )
        content = data.major_order_content(doc)
        self.assertEqual(content["source"], "major_order.official_message")
        self.assertEqual(content["text"], "官方标题\n\n官方重点")
        self.assertIn(("重点", "yellow"), content["rich_text"])

    def test_major_order_plain_text_channel_has_no_tags(self):
        doc = major_order_document()
        doc["news"][0]["raw_message"] = "<i=3>RAW</i>\n\nRAW <i=1>body</i>"
        text = commands.order_text(doc)
        self.assertIn("官方标题", text)
        self.assertNotIn("<i=", text)
        self.assertNotIn("<ci=", text)

    def test_resolve_dispatch_prefers_news_then_official_raw(self):
        doc = {
            "fetchedAt": "2026-11-05T00:00:00Z",
            "dispatches": [{
                "id": 7,
                "published": "2026-11-04T00:00:00Z",
                "message": "<i=3>NEW STRATEGIC DIRECTIVE</i>\n\nEnglish <i=1>hold</i>",
            }],
            "news": [{"id": 7, "official_message":
                      "<i=3>战略指令</i>\n\n中文<i=1>守住</i>"}],
            "official_texts": {"dispatches": [{
                "id": 7,
                "raw_message": "<i=3>原始</i>\n\n另一个<i=1>重点</i>",
            }]},
        }
        model = orders.resolve_dispatch(doc, preferred_id=7)
        self.assertEqual(model["title"], "战略指令")
        self.assertEqual(model["body"], "中文守住")
        self.assertIn(("守住", "yellow"), model["rich_text"])

        doc["news"] = []
        model = orders.resolve_dispatch(doc, preferred_id=7)
        self.assertEqual(model["title"], "原始")
        self.assertEqual(model["body"], "另一个重点")
        self.assertIn(("重点", "yellow"), model["rich_text"])

    def test_resolve_dispatch_official_text_outranks_news_raw(self):
        doc = {
            "fetchedAt": "2026-11-05T00:00:00Z",
            "dispatches": [{"id": 8, "published": "2026-11-04T00:00:00Z",
                            "message": "<i=3>NEW STRATEGIC DIRECTIVE</i>\n\nEnglish"}],
            "news": [{"id": 8, "raw_message": "<i=3>RAW</i>\n\nRAW <i=1>body</i>"}],
            "official_texts": {"dispatches": [{
                "id": 8,
                "official_message": "<i=3>官方</i>\n\n官方<i=1>重点</i>",
            }]},
        }
        model = orders.resolve_dispatch(doc, preferred_id=8)
        self.assertEqual(model["title"], "官方")
        self.assertEqual(model["body"], "官方重点")
        self.assertIn(("重点", "yellow"), model["rich_text"])

    def test_special_order_model_keeps_dispatch_runs(self):
        doc = {
            "fetchedAt": "2026-11-05T00:00:00Z",
            "assignments_all": [{
                "id": 1,
                "title": "STRATEGIC THREAT",
                "briefing": "Hold the line.",
                "tasks": [{"type": 13}],
                "startedAt": "2026-11-04T00:00:00Z",
                "deadline": "2026-11-08T00:00:00Z",
            }],
            "dispatches": [{
                "id": 9,
                "published": "2026-11-04T00:10:00Z",
                "message": "<i=3>NEW STRATEGIC DIRECTIVE</i>\n\nEnglish <i=1>hold</i>",
            }],
            "news": [{
                "id": 9,
                "official_message": "<i=3>新战略指令</i>\n\n中文<i=1>守住</i>",
            }],
            "strategic": {"active": False},
        }
        directive = orders.special_orders(doc)["directive"]
        self.assertEqual(directive["body"], "中文守住")
        self.assertIn(("守住", "yellow"), directive["rich_text"])


class ReportCardRichTextTest(unittest.TestCase):
    def test_wrap_measures_highlight_with_bold_face(self):
        image = Image.new("RGB", (400, 100), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = report_card.F(20)
        runs = [("黄", "yellow"), ("蓝", "blue")]
        lines = report_card._wrap_rich_runs(runs, 10 ** 6, font, draw)
        self.assertEqual(lines, [[("黄", "yellow"), ("蓝", "blue")]])
        for text, tone in lines[0]:
            self.assertIs(report_card._rich_font(font, tone), report_card.F(20, True))

    def test_draw_uses_site_colors_and_bold_runs(self):
        image = Image.new("RGB", (400, 100), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = report_card.F(20)
        calls = []
        original = ImageDraw.ImageDraw.text

        def hook(self, xy, value, *args, **kwargs):
            calls.append((str(value), kwargs.get("font"), kwargs.get("fill")))
            return original(self, xy, value, *args, **kwargs)

        lines = [[("黄", "yellow"), ("蓝", "blue"), ("常", None)]]
        with patch.object(ImageDraw.ImageDraw, "text", hook):
            end_y = report_card._draw_rich_lines(draw, 10, 10, lines, font, 30)

        self.assertEqual(end_y, 40)
        self.assertEqual([row[0] for row in calls], ["黄", "蓝", "常"])
        self.assertEqual(calls[0][2], (245, 197, 24))
        self.assertEqual(calls[1][2], (91, 163, 208))
        self.assertEqual(calls[2][2], report_card.DIM)
        self.assertIs(calls[0][1], report_card.F(20, True))
        self.assertIs(calls[1][1], report_card.F(20, True))
        self.assertIs(calls[2][1], font)

    def test_report_card_mo_brief_uses_rich_runs(self):
        doc = {
            "fetchedAt": "2026-11-05T02:00:00Z",
            "assignments": {"deadline": "2026-11-08T00:00:00Z"},
            "major_order": {},
            "war": {"statistics": {}},
            "campaigns": [],
            "news": [],
            "player_distribution": {"total": 1000},
        }
        content = {
            "text": "正文重点",
            "rich_text": [("正文", None), ("重点", "yellow")],
            "title": "重要指令",
        }
        calls = []
        original = ImageDraw.ImageDraw.text

        def hook(self, xy, value, *args, **kwargs):
            calls.append((str(value), kwargs.get("fill")))
            return original(self, xy, value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(report_card, "_site_get", return_value=doc), \
                 patch.object(report_card, "mo_tasks",
                              return_value=[{"name": "任务", "goal": 10, "progress": 5}]), \
                 patch.object(report_card.shared, "major_order_content",
                              return_value=content), \
                 patch.object(ImageDraw.ImageDraw, "text", hook):
                path = report_card.render_report_card(temp)
                self.assertTrue(path and Path(path).is_file())

        self.assertIn(("重点", report_card.RICH_YELLOW), calls)

    def test_report_card_news_uses_rich_runs_and_no_markup(self):
        doc = {
            "fetchedAt": "2026-11-05T02:00:00Z",
            "assignments": {"deadline": "2026-11-08T00:00:00Z"},
            "major_order": {},
            "war": {"statistics": {}},
            "campaigns": [],
            "news": [{"translated": "<i=3>要闻标题</i>\n\n要闻<i=1>重点</i>"}],
            "player_distribution": {"total": 1000},
        }
        calls = []
        original = ImageDraw.ImageDraw.text

        def hook(self, xy, value, *args, **kwargs):
            calls.append((str(value), kwargs.get("fill")))
            return original(self, xy, value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(report_card, "_site_get", return_value=doc), \
                 patch.object(report_card, "mo_tasks", return_value=[]), \
                 patch.object(report_card.shared, "major_order_content",
                              return_value={"text": "", "rich_text": []}), \
                 patch.object(ImageDraw.ImageDraw, "text", hook):
                path = report_card.render_report_card(temp)
                self.assertTrue(path and Path(path).is_file())

        self.assertIn(("重点", report_card.RICH_YELLOW), calls)
        self.assertFalse(any("<i=" in value or "<ci=" in value for value, _ in calls))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framework import install

install()

from test_core import ROOT  # noqa: F401  (adds project root and plugins to sys.path)
from PIL import Image, ImageDraw

from astrbot_plugin_hd2_common import commands, data
from astrbot_plugin_hd2_war_report import report_card


CURRENT_TEXT = (
    "有关TCS+出现故障的煽动性虚假谣言正在平民通信频道中蔓延，导致了毫无根据的经济恐慌。"
    "SEAF情报部门确切表示：公民只要全额购买保险，就完全不会遇到危险。\n\n"
    "为了恢复公众信心，绝地潜兵奉命<i=1>消灭700,000,000只终结族</i>。"
    "为了协助我方迅速展开行动，捍卫联邦的繁荣，最高指挥部已授权并拨款资助，"
    "为整支舰队提供<i=1>INCINERATOR FRV战略配备</i>。"
)


def major_order_document():
    return {
        "fetchedAt": "2026-11-03T22:06:20Z",
        "war": {"warId": 801},
        "assignments": {
            "id": 1636665659,
            "startedAt": "2026-11-03T13:00:14Z",
            "deadline": "2026-11-08T13:00:42Z",
            "briefing": "Assignment English fallback.",
            "tasks": [{"type": 3, "values": [2, 0, 700000000], "valueTypes": [1, 2, 3]}],
            "progress": [179111170],
        },
        "major_order": {
            "id32": 1636665659,
            "title": "重要指令",
            "translated_brief": "跨指令翻译不得使用",
            "tasks": {"items": [{
                "type": 3,
                "action": "击杀",
                "faction_name": "终结族",
                "goal": 700000000,
                "progress": 179111170,
            }]},
        },
        "dispatches": [
            {
                "id": 3986,
                "published": "2026-11-03T12:59:00Z",
                "message": "<i=3>NEW MAJOR ORDER</i>",
            },
            {
                "id": 3980,
                "published": "2026-11-01T08:00:00Z",
                "message": "<i=3>NEW MAJOR ORDER</i> old",
            },
        ],
        "news": [
            {"id": 3986, "official_message": CURRENT_TEXT},
            {"id": 3980, "official_message": "旧指令公告"},
        ],
        "active_campaign": {
            "title": "SERENE GUARDIAN",
            "phases": [{"briefing_cn": "战役阶段正文不得污染 MO"}],
        },
    }


def campaign_document(count=3):
    statuses = [(2, "success"), (3, "failure"), (0, "in_progress")]
    phases = []
    for index in range(count):
        status, key = statuses[index]
        phases.append({
            "id": 700 + index,
            "title": ("ASSAULT", "RETREAT", "ASSURANCE")[index],
            "title_cn": "",
            "briefing": f"English briefing {index}",
            "briefing_cn": "",
            "outcome": "",
            "status": status,
            "status_key": key,
            "reward": {
                "amount": 45 + index,
                "name": "WARBOND MEDAL",
                "name_cn": "战争债券勋章",
            },
            "intro_image_local": f"./assets/campaign/phase_{index}.webp",
            "intro_image": f"https://example.invalid/phase_{index}.webp",
        })
    current = phases[-1]
    return {
        "fetchedAt": "2026-11-03T22:06:20Z",
        "active_campaign": {
            "available": True,
            "id": 3145394140,
            "title": "SERENE GUARDIAN",
            "title_cn": "",
            "description": "Campaign English description.",
            "description_cn": "",
            "race_name": "Terminids",
            "race_cn": "终结族",
            "current_phase_id": current["id"],
            "current_phase_index": count - 1,
            "episodes_running": 1,
            "reward": {"amount": 1, "name": "Stratagem", "name_cn": "战略配备"},
            "banner_image_local": "./assets/campaign/campaign.webp",
            "banner_image": "https://example.invalid/campaign.webp",
            "phases": phases,
        },
    }


def campaign_translations(count=3):
    phases = {
        str(700 + index): {
            "title": ("突袭", "撤退", "保证")[index],
            "briefing": f"第{index + 1}阶段完整中文简报。\n\n第二段内容。",
        }
        for index in range(count)
    }
    return {
        "episodes": {
            "3145394140": {
                "title": "宁静守护者",
                "description": "TCS+设施附近发生的轻微且例行的终结族入侵。",
                "phases": phases,
            }
        }
    }


class MajorOrderTests(unittest.TestCase):
    def test_required_banner_assets_are_bundled(self):
        for name in ("banner_eagle3.webp", "banner_tcsplus6.webp"):
            path = report_card._banner_file(f"./assets/campaign/{name}")
            self.assertTrue(path and Path(path).is_file())
            self.assertGreater(Path(path).stat().st_size, 1000)
        self.assertEqual(commands.parse("/重要指令"), ("mo", "", False))

    def test_current_official_dispatch_and_independent_banner(self):
        doc = major_order_document()
        content = data.major_order_content(doc)
        self.assertEqual(content["dispatch_id"], 3986)
        self.assertEqual(content["source"], "official_message")
        self.assertIn("有关TCS+出现故障", content["text"])
        self.assertIn("700,000,000只终结族", content["text"])
        self.assertNotIn("旧指令公告", content["text"])
        self.assertNotIn("跨指令翻译不得使用", content["text"])
        self.assertNotIn("战役阶段正文", content["text"])
        self.assertNotIn("<i=", content["text"])
        self.assertEqual(content["banner_sources"], [data.MAJOR_ORDER_BANNER])
        self.assertTrue(any(tone == "yellow" and "700,000,000" in text
                            for text, tone in content["rich_text"]))

    def test_old_dispatch_and_mismatched_translation_cannot_fill_current_mo(self):
        doc = major_order_document()
        doc["news"] = [{"id": 3980, "official_message": "旧指令公告"}]
        doc["major_order"]["id32"] = 999
        content = data.major_order_content(doc)
        self.assertEqual(content["text"], "Assignment English fallback.")
        self.assertEqual(content["source"], "assignment.briefing")

    def test_matching_major_order_translation_is_last_content_fallback_before_english(self):
        doc = major_order_document()
        doc["dispatches"] = []
        doc["news"] = []
        doc["major_order"]["translated_brief"] = "当期翻译简版"
        content = data.major_order_content(doc)
        self.assertEqual(content["text"], "当期翻译简版")
        self.assertEqual(content["source"], "major_order.translated_brief")

    def test_text_and_major_order_card_never_expose_html(self):
        doc = major_order_document()
        text = commands.result("mo", doc=doc).text
        self.assertIn("有关TCS+出现故障", text)
        self.assertNotIn("<i=", text)
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
        self.assertIn("有关TCS+出现故障", rendered)
        self.assertIn("700,000,000只终结族", rendered)
        self.assertNotIn("<i=", rendered)


class CampaignArchiveTests(unittest.TestCase):
    def test_all_phases_are_normalized_with_status_report_reward_and_images(self):
        doc = campaign_document(3)
        campaign = data.campaign_view(doc, campaign_translations(3))
        self.assertEqual(campaign["title_cn"], "宁静守护者")
        self.assertEqual(campaign["current_phase_index"], 2)
        self.assertEqual(campaign["phase_count"], 3)
        self.assertEqual([phase["title_cn"] for phase in campaign["phases"]], ["突袭", "撤退", "保证"])
        self.assertEqual(campaign["phases"][0]["outcome"], "该阶段已完成，战术目标达成。")
        self.assertEqual(campaign["phases"][1]["outcome"], "该阶段未能达成全部目标。")
        self.assertEqual(campaign["phases"][2]["outcome"], "进行中，尚未发布最终战报")
        self.assertEqual(campaign["phases"][2]["reward"]["amount"], 47)
        self.assertEqual(campaign["phases"][2]["image_sources"], [
            "./assets/campaign/phase_2.webp",
            "https://example.invalid/phase_2.webp",
            "./assets/campaign/campaign.webp",
            "https://example.invalid/campaign.webp",
        ])

    def test_existing_active_phase_chinese_has_priority_over_campaign_zh(self):
        doc = campaign_document(1)
        doc["active_campaign"]["phases"][0]["title_cn"] = "现有中文"
        doc["active_campaign"]["phases"][0]["briefing_cn"] = "现有完整简报"
        campaign = data.campaign_view(doc, campaign_translations(1))
        phase = campaign["phases"][0]
        self.assertEqual(phase["title_cn"], "现有中文")
        self.assertEqual(phase["briefing_cn"], "现有完整简报")

    def test_complete_active_chinese_does_not_fetch_translation_layer(self):
        doc = campaign_document(1)
        campaign_doc = doc["active_campaign"]
        campaign_doc["title_cn"] = "宁静守护者"
        campaign_doc["description_cn"] = "现有战役说明"
        campaign_doc["phases"][0]["title_cn"] = "保证"
        campaign_doc["phases"][0]["briefing_cn"] = "现有三段完整简报"
        with patch.object(data, "campaign_zh_document") as fetch:
            campaign = data.campaign_view(doc)
        fetch.assert_not_called()
        self.assertEqual(campaign["phases"][0]["briefing_cn"], "现有三段完整简报")

    def test_mobile_text_fallback_lists_every_phase_vertically(self):
        doc = campaign_document(3)
        with patch.object(data, "campaign_zh_document", return_value=campaign_translations(3)):
            text = commands.result("briefing", doc=doc).text
        for label in ("阶段 01", "阶段 02", "阶段 03", "简报：", "战报：", "阶段奖励："):
            self.assertIn(label, text)
        self.assertIn("保证", text)
        self.assertNotIn("<i=", text)

    def test_phase_grid_uses_one_two_or_three_columns(self):
        self.assertEqual(
            [report_card.campaign_grid_columns(count) for count in (0, 1, 2, 3, 4)],
            [1, 1, 2, 3, 3],
        )

    def test_report_wall_renders_all_phases_and_safe_placeholder(self):
        doc = campaign_document(3)
        labels = []
        original = ImageDraw.ImageDraw.text

        def text_hook(draw, xy, value, *args, **kwargs):
            labels.append((str(value), xy))
            return original(draw, xy, value, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(report_card, "_site_get", return_value=doc), \
                 patch.object(data, "campaign_zh_document", return_value=campaign_translations(3)), \
                 patch.object(report_card, "_campaign_image_file", return_value=None), \
                 patch.object(ImageDraw.ImageDraw, "text", text_hook):
                path = report_card.render_briefing_card(temp)
                self.assertTrue(path and Path(path).is_file())
                with Image.open(path) as image:
                    self.assertEqual(image.width, report_card.W)
                    self.assertGreater(image.height, 900)
        rendered = "\n".join(label for label, _xy in labels)
        for value in ("突袭", "撤退", "保证", "简报", "战报", "战争债券勋章"):
            self.assertIn(value, rendered)
        phase_x = [xy[0] for label, xy in labels if label.startswith("阶段 0")]
        self.assertGreaterEqual(len(set(phase_x)), 3)
        self.assertIn("ARCHIVE IMAGE / SIGNAL LOST", rendered)

    def test_campaign_hero_image_is_placed_in_right_column(self):
        doc = campaign_document(1)
        image_path = report_card._banner_file("./assets/campaign/banner_tcsplus6.webp")
        placements = []
        original = Image.Image.paste

        def paste_hook(self, source, box=None, *args, **kwargs):
            if isinstance(box, tuple) and len(box) == 2:
                placements.append(box)
            return original(self, source, box, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(report_card, "_site_get", return_value=doc), \
                 patch.object(data, "campaign_zh_document", return_value=campaign_translations(1)), \
                 patch.object(report_card, "_campaign_image_file", return_value=image_path), \
                 patch.object(Image.Image, "paste", paste_hook):
                path = report_card.render_briefing_card(temp)
                self.assertTrue(path and Path(path).is_file())
        self.assertTrue(any(x > report_card.W // 2 and y < 400 for x, y in placements))


if __name__ == "__main__":
    unittest.main()

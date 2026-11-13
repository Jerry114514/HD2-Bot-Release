import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / "tables"


class EffectCatalogSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = json.loads(
            (TABLES / "effect_id_cn.json").read_text(encoding="utf-8")
        )
        cls.details = json.loads(
            (TABLES / "galactic_effects.json").read_text(encoding="utf-8")
        )
        cls.variables = json.loads(
            (TABLES / "hd2_variables.json").read_text(encoding="utf-8")
        )

    def test_name_mapping_contains_reference_corrections(self):
        self.assertEqual(len(self.names), 235)
        expected = {
            "12": "额外奖励",
            "71": "情报包裹遭泄露",
            "113": "充足治疗剂",
            "121": "市场崩溃",
            "1137": "军械库扩充：RS-422 磁轨炮",
            "1178": "武器试验",
            "1186": "轻度“阴霾”笼罩",
            "1187": "“阴霾”",
            "1188": "重度“阴霾”笼罩",
            "1219": "星球抵抗度调整",
            "1222": "再生覆盖",
            "1303": "爆裂虫族变种",
            "1395": "终结族遏制体系+",
            "1400": "超级地球武装部队驻守",
            "1402": "无票者变种",
        }
        for effect_id, name in expected.items():
            self.assertEqual(self.names[effect_id], name)

    def test_arsenal_augmentation_titles_are_standalone(self):
        for effect_id, chinese in self.names.items():
            english = self.details.get(effect_id, {}).get("name", "")
            if english.upper().startswith("ARSENAL AUGMENTATION:"):
                self.assertTrue(
                    chinese.startswith("军械库扩充："),
                    (effect_id, english, chinese),
                )
                self.assertNotIn(chinese, {"装备试验", "武器试验"})

    def test_corrected_effect_details_are_synchronized(self):
        self.assertEqual(
            self.details["1219"]["name"],
            "Planetary regen adjustment",
        )
        self.assertEqual(self.details["1222"]["name"], "Regen Override")
        self.assertIn("理论抵抗度 -1.00%", self.details["1219"]["description_cn"])
        self.assertEqual(
            self.details["1359"]["description_cn"],
            "机器人已向该星球部署了大量蹂躏者。请做好应对重甲敌军的准备。",
        )
        self.assertIn(
            "终结族的蟑龙变种",
            self.details["1306"]["description_cn"],
        )

    def test_variable_catalog_uses_corrected_names_and_descriptions(self):
        entries = [
            entry
            for category in self.variables["categories"]
            for entry in category.get("entries", [])
        ]

        rupture = next(entry for entry in entries if 1303 in entry["effect_ids"])
        self.assertEqual(rupture["cn"], "爆裂虫族变种")
        self.assertIn("地下破土而出", rupture["desc"])

        devastator = next(
            entry for entry in entries if entry["key"] == "Devastator Surge"
        )
        self.assertEqual(devastator["cn"], "蹂躏者激增")
        self.assertIn("大量蹂躏者", devastator["desc"])

        hulk = next(entry for entry in entries if entry["key"] == "Hulk Surge")
        self.assertEqual(hulk["cn"], "巨型者激增")
        self.assertIn("大量敌方巨型者", hulk["desc"])

        tcs = next(
            entry
            for entry in entries
            if entry["key"] == "TERMINID CONTROL SYSTEM +"
        )
        self.assertEqual(tcs["cn"], "终结族遏制体系+")


if __name__ == "__main__":
    unittest.main()

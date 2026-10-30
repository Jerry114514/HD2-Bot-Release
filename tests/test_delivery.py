import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_core import ROOT,snapshot
from astrbot_plugin_hd2_common import data, commands, diagnostics
import sync_plugins


class DeliveryTests(unittest.TestCase):
    def test_sync_dry_run_and_preserve_runtime_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"plugins"
            planned=sync_plugins.sync(target,dry_run=True)
            self.assertFalse(target.exists())
            self.assertTrue(any("snapshot.json" in f for f in planned))
            sync_plugins.sync(target)
            welcome=target/"astrbot_plugin_hd2_welcome/welcome_config.json"
            welcome.write_text('{"enabled":false}',encoding="utf-8")
            self.assertEqual(sync_plugins.sync(target),[])
            self.assertEqual(json.loads(welcome.read_text())["enabled"],False)
            main=target/"astrbot_plugin_hd2_roll/main.py"
            main.write_text("local customization",encoding="utf-8")
            with self.assertRaises(ValueError):sync_plugins.sync(target)
            sync_plugins.sync(target,update=True)
            backups=list((target/".hd2-sync-backups").rglob("main.py"))
            self.assertEqual(len(backups),1)
            self.assertEqual(backups[0].read_text(),"local customization")

    def test_dss_states_and_current_phase_are_shared(self):
        action={"status":3,"effectIds":[1]}
        self.assertEqual(data.dss_phase({"activeEffectIds":[]},action),"冷却中")
        self.assertEqual(data.dss_phase({"activeEffectIds":[1]},action),"已激活")
        doc=snapshot();doc['active_campaign']={"available":True,"title_cn":"title","current_phase_id":2,"phases":[{"id":1,"briefing_cn":"过去"},{"id":2,"briefing_cn":"当前"}]}
        text=commands.briefing_text(doc)
        self.assertIn("当前",text);self.assertNotIn("过去",text)

    def test_log_rotation_is_bounded_and_preserves_string_identity(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(diagnostics,"LOG_FILE",str(Path(temp)/"log")),patch.object(diagnostics,"MAX_BYTES",1000):
            for i in range(100):diagnostics.hd2_log("done",text="fixture"*10,group="opaque-id")
            current=Path(temp,"log");old=Path(temp,"log.1")
            self.assertLess(current.stat().st_size,2000);self.assertLess(old.stat().st_size,2000)
            row=json.loads(current.read_text().splitlines()[-1])
            self.assertEqual(row["stage"],"generated");self.assertEqual(row["group"],"opaque-id")

    def test_source_configuration_covers_primary_and_history(self):
        def setting(*keys,default=None):
            return "https://mirror.invalid/war" if keys==("data","site_base") else default
        with patch.object(data,"setting",side_effect=setting):
            self.assertEqual(data.site_url(),"https://mirror.invalid/war/data.json")
            self.assertEqual(data.resource_url(data.DEFAULT_SITE+"/data/recent/planet_regions.json"),"https://mirror.invalid/war/data/recent/planet_regions.json")

    def test_backup_snapshot_preserves_source_and_freshness(self):
        doc=snapshot();primary=data.site_url()
        def setting(*keys,default=None):
            return "https://backup.invalid/data.json" if keys==("data","backup_json_url") else default
        def fetch(url,**kwargs):
            if url==primary:raise OSError("primary offline")
            return doc
        with tempfile.TemporaryDirectory() as temp,patch.object(data,"CACHE",Path(temp)),patch.object(data,"setting",side_effect=setting),patch.object(data,"get_json",side_effect=fetch),patch.object(data.time,"time",return_value=data.timestamp(doc["fetchedAt"])+60):
            result=data.site_doc()
            self.assertEqual(result["_hd2"]["source"],"https://backup.invalid/data.json")
            self.assertTrue(result["_hd2"]["fallback_source"]);self.assertFalse(result["_hd2"]["stale"])


if __name__=="__main__":unittest.main()

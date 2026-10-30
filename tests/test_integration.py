import asyncio
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock

from test_core import ROOT, snapshot, load_console
from framework import Event, install
install()
from astrbot_plugin_hd2_common import commands, data, diagnostics
from astrbot_plugin_hd2_war_report import main as war, report_card as card
from astrbot_plugin_hd2_planet_info import main as planet
from astrbot_plugin_hd2_variant_query import main as variant
from astrbot_plugin_hd2_wiki import main as wiki, wiki_data
from astrbot_plugin_hd2_guard.main import Hd2GuardPlugin


async def collect(plugin, text):
    event=Event(text)
    outputs=[v async for v in plugin.keyword_trigger(event)]
    return event,outputs


class IntegrationTests(unittest.TestCase):
    def test_same_snapshot_text_fallback_and_identity(self):
        doc=snapshot()
        with tempfile.TemporaryDirectory() as temp, patch.object(diagnostics,"LOG_FILE",str(Path(temp)/"log.jsonl")), patch.object(data,"site_doc",return_value=doc) as fetch, patch.object(commands,"render_result",side_effect=ImportError("fixture: no image dependency")):
            event,outputs=asyncio.run(collect(war.Hd2WarReportPlugin(None),"/战报"))
            self.assertTrue(event.stopped)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(outputs,[("text",commands.result("report",doc=doc).text)])
            rows=[json.loads(x) for x in Path(temp,"log.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual({x["group"] for x in rows},{"official-group-A"})
            self.assertEqual(len({x["request_id"] for x in rows}),1)
            self.assertEqual(rows[-1]["stage"],"generated")

    def test_analysis_uses_session_config_and_complete_facts(self):
        context=types.SimpleNamespace(get_config=lambda origin:{"provider_settings":{"default_provider_id":"fixture-provider"}},llm_generate=AsyncMock(return_value=types.SimpleNamespace(completion_text="fixture advice")))
        doc=snapshot()
        for i in range(25):
            doc["planets"].append({"index":100+i,"name":f"PLANET-{i}","currentOwner":"Automaton","health":100,"maxHealth":100,"players":1})
            doc["campaigns"].append({"planet":{"index":100+i},"type":0})
        with patch.object(data,"site_doc",return_value=doc):
            result=asyncio.run(war.Hd2WarReportPlugin(context)._get_analysis("session"))
        self.assertEqual(result,"fixture advice")
        arguments=context.llm_generate.call_args.kwargs
        self.assertIn("PLANET-24",arguments["prompt"])
        self.assertIn("防御截止",arguments["prompt"])
        self.assertIn("未知",arguments["prompt"])
        self.assertEqual(arguments["chat_provider_id"],"fixture-provider")

    def test_report_render_uses_snapshot_distribution_and_separate_tasks(self):
        doc=snapshot();doc["player_distribution"]={"total":100,"humans":10,"terminids":20,"automatons":30,"illuminate":40}
        doc["assignments"]={"tasks":[{"type":2,"values":[1000000],"valueTypes":[3]},{"type":13,"values":[1],"valueTypes":[3]}],"progress":[1000000,0]}
        labels=[];original=card.ImageDraw.ImageDraw.text
        def text(draw,xy,label,*args,**kwargs):
            labels.append(str(label));return original(draw,xy,label,*args,**kwargs)
        def source(url,*args,**kwargs):
            self.assertEqual(url,card.DATA_URL)
            return doc
        with tempfile.TemporaryDirectory() as temp,patch.object(card,"_site_get",side_effect=source),patch.object(card.ImageDraw.ImageDraw,"text",text):
            self.assertTrue(Path(card.render_report_card(temp)).exists())
        self.assertIn("100",labels);self.assertIn("1 项",labels);self.assertNotIn("指令总进度",labels)

    def test_route_does_not_capture_wiki_argument(self):
        for cls in (war.Hd2WarReportPlugin,planet.Hd2PlanetInfoPlugin,variant.Hd2VariantQueryPlugin):
            _,output=asyncio.run(collect(cls(None),"/wiki 战报"))
            self.assertEqual(output,[])

    def test_wiki_text_survives_missing_render_dependencies(self):
        value={"status":"ok","title":"词条","url":"https://helldivers.wiki.gg/zh/wiki/test","text":"正文","rev":"1","candidates":[]}
        with tempfile.TemporaryDirectory() as temp,patch.object(diagnostics,"LOG_FILE",str(Path(temp)/"log")),patch.object(wiki,"wiki_lookup",return_value=value) as fetch,patch.object(wiki,"render",side_effect=ImportError("no Pillow")):
            _,output=asyncio.run(collect(wiki.Hd2WikiPlugin(None),"/wiki roll"))
            self.assertIn("正文",output[0][1]);self.assertEqual(fetch.call_count,1)
        with patch.dict(sys.modules,{"bs4":None}):
            with self.assertRaises(ImportError):
                wiki_data._extract_tables("<table><tr><td>test</td></tr></table>")

    def test_guard_blocks_noncommands_and_preserves_enabled_chat(self):
        plugin=Hd2GuardPlugin(None)
        for text in ("123","普通消息","/unknown",""):
            event=Event(text)
            asyncio.run(plugin.guard(event))
            self.assertTrue(event.stopped)
        with patch("astrbot_plugin_hd2_guard.main.setting",side_effect=lambda *k,default=None:True if k[-1]=="allow_chat" else default):
            event=Event("123");asyncio.run(plugin.guard(event));self.assertFalse(event.stopped)

    def test_variant_primary_failure_awaits_fallback_without_nested_run(self):
        def fetch(url,*args,**kwargs):
            return [] if url==variant.PLANETS_URL else {"warStatus":{"campaigns":[]}}
        with patch.object(variant,"_scan_cache",{"data":{},"ts":0}),patch.object(variant,"_load_effect_id_cn",return_value={1:"sample"}),patch.object(variant,"_fetch_official",side_effect=OSError("offline")),patch.object(variant,"_fetch_json",side_effect=fetch):
            scan=asyncio.run(variant._scan_all_variants())
            self.assertEqual(scan["variants"],{k:[] for k in variant.VARIANT_CN})
            self.assertTrue(scan["complete"])
            self.assertEqual(scan["source"],variant.LIVE_API_URL)

    def test_console_javascript_and_untrusted_log_values(self):
        web=load_console()
        probe=r'''const vm=require('vm'),fs=require('fs');
const page=fs.readFileSync(process.argv[1],'utf8');
const script=page.match(/<script>([\s\S]*?)<\/script>/)[1];new Function(script);
const body={innerHTML:''};const ctx={allLogs:[{name:'<b>name</b>',text:'<i>msg</i>',stage:'failed',detail:'<u>detail</u>',ts:"');bad()//"}],logFilter:'all',document:{getElementById:id=>id==='logBody'?body:{value:''}}};
vm.runInNewContext(script.slice(script.indexOf('function statusBadge('),script.indexOf('function toggleLogDetail('))+'\nrenderLogs();',ctx);
if(/<b>name|<i>msg|<u>detail|bad\(\)/.test(body.innerHTML))throw Error('unescaped log value');'''
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp,"page.html");path.write_text(web.PAGE,encoding="utf-8")
            result=subprocess.run(["node","-e",probe,str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_real_pillow_defense_and_pagination(self):
        doc=snapshot()
        labels=[]
        original=card.ImageDraw.ImageDraw.text
        def text(draw,xy,value,*a,**kw):
            labels.append(str(value));return original(draw,xy,value,*a,**kw)
        with tempfile.TemporaryDirectory() as temp, data.snapshot_scope(doc),patch.object(card,"_starmap_cn_map",return_value={}),patch.object(card.ImageDraw.ImageDraw,"text",text):
            path=card.render_campaigns_card(temp)
            self.assertTrue(Path(path).is_file());self.assertTrue(any("防御战役中" in t for t in labels));self.assertIn("25.0%",labels)
            doc["campaigns"]*=65
            doc["fetchedAt"]="long-fixture"
            paths=card.render_campaigns_card(temp)
            self.assertIsInstance(paths,list);self.assertGreater(len(paths),1)
            for image in paths:
                with card.Image.open(image) as img:self.assertLessEqual(img.height,2164)


if __name__=="__main__":unittest.main()

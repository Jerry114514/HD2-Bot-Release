import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_core import snapshot
from framework import Event, install
install()
from astrbot_plugin_hd2_common import commands, data, diagnostics
from astrbot_plugin_hd2_common.search import match_name, osa_limit
from astrbot_plugin_hd2_common.runtime import singleflight, wiki_blocking, blocking
from astrbot_plugin_hd2_wiki import wiki_data as wiki, main as handler, tables, wiki_card
from astrbot_plugin_hd2_variant_query import main as variant


def page(title="AR-23 解放者", pid=1, **extra):
    return {"title":title,"pageid":pid,"fullurl":"https://helldivers.wiki.gg/zh/wiki/"+title,
            "revisions":[{"revid":123}],"extract":"正文摘要",**extra}


def pages(*items):
    return {"query":{"pages":{str(p["pageid"]):p for p in items}}}


EMPTY={"schema":1,"version":"fixture","entries":[]}


class SearchTests(unittest.TestCase):
    def setUp(self):
        wiki._resolutions.clear()

    def test_changed_catalogue_identity_and_conflicting_redirect_do_not_resolve(self):
        known={"schema":1,"version":"fixture","entries":[{"title":"AR-23 解放者","pageid":1,"aliases":["AR23"],"verified":True,"scope":"HD2"}]}
        with patch.object(wiki,"catalogue",return_value=known),patch.object(wiki,"_wiki_get",return_value=pages(page(pid=9))):
            self.assertEqual(wiki.wiki_lookup("AR23",False)["status"],"ambiguous")
        with patch.object(wiki,"catalogue",return_value=EMPTY),patch.object(wiki,"_wiki_get",return_value=pages(page("AX/AR-23 护卫犬"))):
            self.assertEqual(wiki.wiki_lookup("AR23",False)["status"],"ambiguous")

    def test_missing_cached_schema_is_replaced_and_query_resolution_is_scoped(self):
        url='https://fixture.invalid/old-incomplete'
        data._memory[url]=(time.monotonic(),{})
        def validate(v):
            if 'ok' not in v: raise ValueError('missing')
        with patch.object(data,'urlopen',return_value=io.BytesIO(b'{"ok":true}')) as fetch:
            self.assertEqual(data.get_json(url,validate=validate),{'ok':True});self.assertEqual(fetch.call_count,1)
        with patch.object(wiki,'catalogue',return_value=EMPTY),patch.object(wiki,'_wiki_get',return_value=pages(page())) as fetch:
            wiki.wiki_lookup('别名',False)
            second=wiki.wiki_lookup('别名',False)
            self.assertEqual(fetch.call_args.args[0]['titles'],'AR-23 解放者')
            self.assertTrue(second['diagnostics']['resolution_cache'])

    def test_osa_language_limits_and_transposition(self):
        self.assertEqual(osa_limit("机炮"),0)
        self.assertEqual(osa_limit("甲乙丙丁戊己庚辛"),2)
        self.assertEqual(osa_limit("abcdefgh"),1)
        self.assertEqual(match_name("Qausar Cannon","LAS-99 类星体加农炮",["Quasar Cannon"])[0],2)
        self.assertEqual(match_name("解方者","AR-23 解放者",["解放者"])[0],2)
        self.assertIsNone(match_name("机饱","机炮"))

    def test_model_protection_and_conflict(self):
        for title in ("AR-32 解放者","AR-23P 解放者","AX/AR-23 护卫犬"):
            self.assertIsNone(match_name("AR23",title,["AX-AR23 护卫犬"]))
        self.assertEqual(match_name("ＡＲ－２３","AR-23 解放者")[0],0)
        self.assertIsNone(match_name("AR23 护卫犬","AR-23 解放者"))

    def test_command_whitespace_and_planet_scope(self):
        self.assertEqual(commands.parse("／wiki\t战报　文字"),("wiki","战报",True))
        for name in ("X45","Ｘ－４５","X–45"):
            self.assertEqual(commands.find_planet(snapshot(),name)["index"],45)
        self.assertIsNone(commands.parse("/wikifoo"))
        doc=snapshot();doc["planets"].append({"index":99,"name":"X45"})
        with self.assertRaisesRegex(ValueError,"歧义"): commands.find_planet(doc,"x45")

    def test_catalogue_identity_and_optional_dependency(self):
        local=wiki._local("AR23")
        self.assertEqual([r["title"] for r in local if r["tier"]==0],["AR-23 解放者"])
        self.assertEqual([r["title"] for r in wiki._local("Quasar Cannon") if r["tier"]==0],["LAS-99 类星体加农炮"])
        with patch.dict(sys.modules,{"rapidfuzz.distance":None}):
            self.assertEqual(match_name("AR23","AR-23 解放者")[0],0)
            self.assertIsNone(match_name("Qausar Cannon","Quasar Cannon"))

    def test_direct_text_skips_parse_and_preserves_redirect(self):
        with patch.object(wiki,"catalogue",return_value=EMPTY),patch.object(wiki,"_wiki_get",return_value=pages(page())) as fetch:
            value=wiki.wiki_lookup("无后座",include_tables=False)
        self.assertEqual(value["status"],"resolved")
        self.assertEqual(value["table_status"],"not_requested")
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(fetch.call_args.args[0]["ppprop"],"disambiguation")

    def test_disambiguation_excludes_navigation(self):
        def fetch(params):
            if params["action"]=="parse":
                return {"parse":{"revid":123,"text":'<div class="mw-parser-output"><ul><li><a href="/zh/wiki/甲">甲</a></li></ul><div class="navbox"><li><a href="/zh/wiki/乙">乙</a></li></div></div>'}}
            if params["prop"]=="info|pageprops":
                self.assertEqual(params["titles"],"甲"); return pages(page("甲",2))
            return pages(page("护卫犬",pageprops={"disambiguation":""}))
        with patch.object(wiki,"catalogue",return_value=EMPTY),patch.object(wiki,"_wiki_get",side_effect=fetch):
            value=wiki.wiki_lookup("护卫犬",False)
        self.assertEqual(value["status"],"ambiguous")
        self.assertEqual([r["title"] for r in value["candidates"]],["甲"])
        self.assertIn("/wiki 甲",wiki.format_text(value))

    def test_continuation_retains_sixth_candidate_and_no_first_answer(self):
        calls=[]
        def fetch(p):
            calls.append(p)
            if p.get("list")=="search":
                start=20 if p.get("sroffset") else 0
                hits=[{"title":f"候选{i}","snippet":"正文提及"} for i in range(start,start+20)]
                return {"query":{"search":hits},**({"continue":{"sroffset":20,"continue":"-||"}} if start==0 else {})}
            if p["prop"]=="info|pageprops":
                return pages(*(page(t,int(t[2:])+1) for t in p["titles"].split("|")))
            return pages({"title":"关键词","pageid":-1,"missing":""})
        with patch.object(wiki,"catalogue",return_value=EMPTY),patch.object(wiki,"_wiki_get",side_effect=fetch):
            value=wiki.wiki_lookup("关键词",False)
        self.assertEqual(value["status"],"ambiguous")
        self.assertEqual(len(value["candidates"]),40)
        self.assertEqual(value["candidates"][5]["title"],"候选5")
        self.assertIn("另有 35",wiki.format_text(value))

    def test_partial_results_and_failure_are_not_absence(self):
        for error in (OSError("offline"),ValueError("bad schema"),TimeoutError("budget")):
            with patch.object(wiki,"catalogue",return_value=EMPTY),patch.object(wiki,"_wiki_get",side_effect=error):
                value=wiki.wiki_lookup("未收录",False)
            self.assertEqual(value["status"],"unavailable")
            self.assertFalse(value["search_complete"])
        with patch.object(wiki,"_wiki_get") as fetch:
            value=wiki.wiki_lookup("超时",deadline=time.monotonic()-1)
            fetch.assert_not_called()
            self.assertEqual(value["status"],"unavailable")

    def test_missing_tables_dependency_and_parse_failure_are_explicit(self):
        with patch.object(wiki,"catalogue",return_value=EMPTY),patch.object(wiki,"_wiki_get",return_value=pages(page())),patch.object(wiki,"_html",side_effect=OSError("403")):
            value=wiki.wiki_lookup("标题")
        self.assertEqual(value["status"],"resolved")
        self.assertEqual(value["table_status"],"unavailable")
        with patch.object(wiki,"catalogue",return_value=EMPTY),patch.object(wiki,"_wiki_get",return_value=pages(page())),patch.object(wiki,"_html",return_value="<p>无表</p>"):
            self.assertEqual(wiki.wiki_lookup("标题")["table_status"],"none")
            with patch.dict(sys.modules,{"bs4":None}):
                self.assertEqual(wiki.wiki_lookup("标题")["table_status"],"unavailable")

    def test_headers_duplicate_titles_spans_and_unsupported(self):
        html='<h2>数据</h2><table><tr><th>标准伤害</th><th>耐久伤害</th></tr><tr><td>90</td><td>22</td></tr></table>'
        result,bad=tables.extract(html+html.replace('90','80'))
        self.assertEqual(bad,0);self.assertEqual(len(result),2)
        self.assertEqual(result[0]["rows"],[['标准伤害','耐久伤害'],['90','22']])
        result,bad=tables.extract('<table><tr><th rowspan="2">甲</th><td>1</td></tr><tr><td>2</td></tr></table>')
        self.assertEqual(result[0]["rows"],[['甲','1'],['甲','2']])
        self.assertEqual(result[0]["origins"][1][0],[0,0])
        self.assertGreater(tables.extract('<table><tr><td colspan="999999">X</td></tr></table>')[1],0)
        self.assertGreater(tables.extract('<table><tr><td>外<table><tr><td>内</td></tr></table></td></tr></table>')[1],0)

    def test_wiki_card_cache_covers_query_and_recovered_tables(self):
        value={"status":"resolved","title":"测试","url":"https://example.test","rev":"1","text":"摘要","tables":[],"thumb":None,"table_status":"unavailable"}
        with tempfile.TemporaryDirectory() as temp:
            first=wiki_card.render_wiki_card("甲",temp,value)
            second=wiki_card.render_wiki_card("乙",temp,value)
            value.update(table_status="available",tables=[{"section":"数据","title":"同名表","rows":[['标准伤害','耐久伤害']]+[['90','22']]*26,"headers":[[True,True]]+[[False,False]]*26}])
            third=wiki_card.render_wiki_card("甲",temp,value)
            self.assertNotEqual(first,second);self.assertNotEqual(first,third)
            for path in third if isinstance(third,list) else [third]:
                with wiki_card.Image.open(path) as img:self.assertLess(img.height,2165)
            value['tables']=[{'section':'长表','title':'分页','rows':[['重复列头','数值','来源','备注']]+[['长单元格测试'*10]*4 for _ in range(25)],
                              'headers':[[True]*4]+[[False]*4 for _ in range(25)]}]
            labels=[]; original=wiki_card.ImageDraw.ImageDraw.text
            def record(draw,xy,label,*a,**kw):
                labels.append(str(label));return original(draw,xy,label,*a,**kw)
            with patch.object(wiki_card.ImageDraw.ImageDraw,'text',record):
                rendered=wiki_card.render_wiki_card('长表',temp,value)
            self.assertGreaterEqual(labels.count('重复列头'),2)
            self.assertGreater(len(rendered),2)

    def test_singleflight_cancel_one_waiter(self):
        async def probe():
            release=asyncio.Event();calls=[]
            async def producer(): calls.append(1);await release.wait();return 42
            one=asyncio.create_task(singleflight('test',producer))
            two=asyncio.create_task(singleflight('test',producer))
            await asyncio.sleep(.01);one.cancel()
            with self.assertRaises(asyncio.CancelledError): await one
            release.set();self.assertEqual(await two,42);self.assertEqual(calls,[1])
            self.assertEqual(await singleflight('test',producer),42);self.assertEqual(len(calls),2)
        asyncio.run(probe())

    def test_wiki_leaves_capacity_for_other_commands(self):
        async def probe():
            tasks=[asyncio.create_task(wiki_blocking(time.sleep,.15)) for _ in range(3)]
            await asyncio.sleep(.02)
            start=time.monotonic();await blocking(lambda:None)
            self.assertLess(time.monotonic()-start,.1)
            await asyncio.gather(*tasks)
        asyncio.run(probe())

    def test_shared_http_cold_requests_and_error_recovery(self):
        count=[];gate=threading.Barrier(3)
        def fetch(*a,**kw): count.append(1);time.sleep(.04);return io.BytesIO(b'{"ok":true}')
        def query(): gate.wait();return data.get_json('https://fixture.invalid/coalesce',force=True)
        with patch.object(data,"urlopen",side_effect=fetch):
            with ThreadPoolExecutor(3) as pool:values=list(pool.map(lambda _:query(),range(3)))
        self.assertEqual(len(count),1);self.assertEqual(values,[{"ok":True}]*3)
        with patch.object(data,"urlopen",side_effect=[io.BytesIO(b'{"error":"bad"}'),io.BytesIO(b'{"ok":true}')]) as fetch:
            data.get_json('https://fixture.invalid/error')
            self.assertEqual(data.get_json('https://fixture.invalid/error'),{"ok":True})
            self.assertEqual(fetch.call_count,2)

    def test_variant_missing_and_unknown_are_not_empty(self):
        with self.assertRaises(ValueError): variant._validate_status({})
        valid={"campaigns":[],"planetActiveEffects":[]}
        self.assertEqual(variant._validate_status(valid),valid)
        unknown={"campaigns":[{"planetIndex":1}],"planetActiveEffects":[{"index":1,"galacticEffectId":999}]}
        with patch.object(variant,"_scan_cache",{"ts":0,"data":{}}),patch.object(variant,"_load_effect_id_cn",return_value={1:"孢裂变种"}),patch.object(variant,"_fetch_json",return_value=[{"index":1,"name":"X-45"}]),patch.object(variant,"_fetch_official",return_value=unknown):
            result=asyncio.run(variant._scan_all_variants())
        self.assertFalse(result["complete"]);self.assertEqual(result["unknown_effects"],[999])
        self.assertIs(variant.VARIANT_CN,commands.VARIANT_TERMS)

    def test_plain_handler_renders_candidates_and_records_outcome(self):
        value={"status":"ambiguous","title":"词条","url":"https://example.test","text":"","rev":"none","candidates":[{"title":"候选","reason":"近似名称"}],"search_complete":False}
        async def probe():
            event=Event('/wiki 词条 文字')
            return [r async for r in handler.Hd2WikiPlugin(None).keyword_trigger(event)]
        with tempfile.TemporaryDirectory() as temp,patch.object(diagnostics,"LOG_FILE",str(Path(temp)/'log')),patch.object(handler,"wiki_lookup",return_value=value) as fetch,patch.object(handler,"render") as render:
            output=asyncio.run(probe())
            self.assertIn('/wiki 候选',output[0][1]);self.assertIn('尚未完成',output[0][1])
            self.assertFalse(fetch.call_args.kwargs['include_tables']);render.assert_not_called()
            self.assertEqual(json.loads(Path(temp,'log').read_text(encoding='utf-8'))['search']['status'],'ambiguous')


if __name__ == '__main__': unittest.main()

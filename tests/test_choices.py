import asyncio
import unittest
from unittest.mock import patch

from test_core import ROOT  # adds plugin path
from framework import Event, install
install()
from astrbot_plugin_hd2_common.choices import ChoiceStore, choices, owner, reply
from astrbot_plugin_hd2_guard.main import Hd2GuardPlugin
from astrbot_plugin_hd2_wiki import main as handler, wiki_data as wiki


def value(count=7):
    return {"status":"ambiguous", "title":"120MM", "url":"https://example.invalid/search", "rev":"none",
            "text":"", "warnings":[], "search_complete":True,
            "candidates":[{"title":f"词条{i}", "pageid":100+i, "reason":"名称片段"} for i in range(1,count+1)]}


def event(text, **kwargs):
    e = Event(text, **kwargs)
    e.message_obj.message_str = text
    e.message_obj.message_id = text
    e._hd2_choices_allowed = True
    return e


class ChoicesTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.store = ChoiceStore(clock=lambda: self.now, capacity=2)
        choices.clear()

    def ready(self, e=None):
        menu = self.store.begin(e or event("/wiki 120MM"), True)
        original = value()
        self.store.prepare(menu, original)
        self.store.activate(menu, refresh=True)
        return menu, original

    def test_scope_snapshot_and_global_page_numbers(self):
        menu, original = self.ready()
        original["candidates"].reverse()
        self.assertEqual(menu.value["candidates"][0]["pageid"],101)
        for other in (event("1",user="other"), event("1",group="other")):
            self.assertIsNone(self.store.handle(other))
        other=event("1");other.unified_msg_origin="another:GroupMessage:official-group-A"
        self.assertIsNone(self.store.handle(other))
        private=event("1");private.unified_msg_origin="test_adapter:FriendMessage:official-user-B"
        self.assertIsNone(self.store.handle(private))
        self.assertEqual(self.store.handle(event("6")).kind,"range")
        self.assertEqual(self.store.handle(event("下一页")).kind,"page")
        self.store.activate(menu)
        self.assertEqual(self.store.handle(event("选 6")).index,5)
        text=wiki.format_text(menu.value, choice_page=1)
        self.assertIn("6. 词条6",text)
        self.assertNotIn("1. 词条1",text)

    def test_expiry_capacity_cancel_and_superseded_publication(self):
        old,_=self.ready()
        new=self.store.begin(event("/wiki 新搜索"),False)
        self.assertFalse(self.store.prepare(old,value()))
        self.store.discard(old)
        self.assertTrue(self.store.current(new))
        self.store.prepare(new,value());self.store.activate(new)
        self.now=121
        self.assertIsNone(self.store.handle(event("1")))
        for user in ("a","b","c"):
            self.ready(event("/wiki",user=user))
        self.assertEqual(len(self.store.menus),2)
        self.assertIsNone(self.store.handle(event("1",user="a")))
        self.assertIsNone(self.store.handle(event("普通聊天",user="b")))
        self.assertNotIn(owner(event("",user="b")),self.store.menus)
        self.assertEqual(self.store.handle(event("取消",user="c")).kind,"cancelled")

    def test_only_plain_selection_and_duplicate_receipts(self):
        self.ready()
        for text in ("120MM","/2","1 2 3","2个人","","-1"):
            self.assertIsNone(reply(event(text)))
        self.assertEqual(reply(event("选　２")),2)
        other=event("2")
        part=type("At",(),{"qq":"someone-else"})()
        other.get_messages=lambda:[part]
        self.assertIsNone(reply(other))
        part=type("Reply",(),{})()
        self.assertIsNone(reply(other))
        e=event("2");e.message_obj.message_id="receipt"
        action=self.store.handle(e)
        self.assertEqual(action.kind,"select")
        self.assertEqual(self.store.handle(e).kind,"duplicate")
        self.assertEqual(self.store.handle(event("选 2")).kind,"busy")
        self.store.discard(action.menu)
        self.assertEqual(self.store.handle(e).kind,"duplicate")

    def test_selection_near_expiry_can_finish_but_failure_does_not_extend_wait(self):
        menu,_=self.ready()
        self.now=119
        self.assertEqual(self.store.handle(event("2")).kind,"select")
        self.now=125
        self.store.prune()
        self.assertTrue(self.store.current(menu))
        self.store.activate(menu)  # Lookup failed: restore only the original wait window.
        self.assertFalse(self.store.current(menu))
        self.assertFalse(self.store.menus)

    def test_guard_rechecks_access_and_does_not_swallow_new_commands(self):
        settings={}
        with patch("astrbot_plugin_hd2_guard.main.setting",side_effect=lambda *k,default=None:settings.get(k[-1],default)):
            def begin():
                menu=choices.begin(event("/wiki 120MM"),False)
                choices.prepare(menu,value());choices.activate(menu)
                return menu
            async def guard(e):
                await Hd2GuardPlugin(None).guard(e)
            begin()
            e=event("2");asyncio.run(guard(e))
            self.assertEqual(e._hd2_choice.kind,"select")
            self.assertFalse(e.stopped)
            begin()
            e=event("/roll");asyncio.run(guard(e))
            self.assertFalse(e.stopped)
            self.assertIsNone(e._hd2_choice)
            self.assertNotIn(owner(e),choices.menus)
            begin();settings["groups"]=["other"]
            e=event("2");asyncio.run(guard(e))
            self.assertTrue(e.stopped)
            self.assertNotIn(owner(e),choices.menus)
            settings.clear();begin();settings["platform_id"]="other"
            e=event("2");asyncio.run(guard(e))
            self.assertIsNone(e._hd2_choice)
            self.assertFalse(e._hd2_choices_allowed)

    def test_selected_identity_does_not_search_or_pollute_resolution_cache(self):
        def page(pid=102,title="词条2",**kw):
            return {"query":{"pages":{str(pid):{"pageid":pid,"title":title,"extract":"正文",**kw}}}}
        wiki._resolutions.clear()
        with patch.object(wiki,"_wiki_get",return_value=page()) as fetch, \
             patch.object(wiki,"catalogue",side_effect=AssertionError("selection must not consult names")):
            result=wiki.wiki_lookup("词条2",False,selected=value()["candidates"][1])
            self.assertEqual(result["status"],"resolved")
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(fetch.call_args.args[0]["pageids"],102)
            self.assertNotIn("titles",fetch.call_args.args[0])
            self.assertFalse(wiki._resolutions)
        for returned in (page(pid=999),page(pid=102,title="新标题")):
            with patch.object(wiki,"_wiki_get",return_value=returned):
                result=wiki.wiki_lookup("词条2",False,selected=value()["candidates"][1])
                self.assertEqual(result["status"],"ambiguous" if "999" in returned["query"]["pages"] else "resolved")
        with patch.object(wiki,"_wiki_get",return_value=page(title="另一个词条")):
            result=wiki.wiki_lookup("未核实名称",False,selected={"title":"未核实名称","pageid":None})
            self.assertEqual(result["status"],"ambiguous")
        with patch.object(wiki,"_wiki_get",return_value=page(pid=-1,missing="")) as fetch:
            self.assertEqual(wiki.wiki_lookup("词条2",False,selected=value()["candidates"][1])["status"],"not_found")
            self.assertEqual(fetch.call_count,1)

    def test_unload_and_closed_prompt_remove_waiting_state(self):
        async def run():
            plugin=handler.Hd2WikiPlugin(None)
            with patch.object(handler,"wiki_lookup",return_value=value()):
                stream=plugin.keyword_trigger(event("/wiki 120MM"))
                await anext(stream)
                self.assertEqual(len(choices.menus),1)
                await stream.aclose()
                self.assertFalse(choices.menus)
                stream=plugin.keyword_trigger(event("/wiki 120MM"))
                [out async for out in stream]
                self.assertEqual(len(choices.menus),1)
                await plugin.terminate()
                self.assertFalse(choices.menus)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

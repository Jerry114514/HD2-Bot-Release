import asyncio
import unittest
from unittest.mock import patch

from test_core import ROOT  # adds plugin path
from framework import Event, install
install()
from astrbot_plugin_hd2_common.choices import ChoiceStore, choices, owner, reply, quote_diagnostics
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


def quoted(text="2", mid="menu-1", sender="self", **kwargs):
    e = event(text, **kwargs)
    part = type("Reply", (), {"id":mid, "sender_id":sender, "message_str":"old body 7"})()
    plain = type("Plain", (), {"text":text})()
    e.get_messages = lambda: [part, plain]
    e.message_obj.message_id = f"{mid}-{text}-{kwargs}"
    return e


def official(text, tag=None, **kwargs):
    e = quoted(text, mid="", sender="", **kwargs)
    e.get_platform_name = lambda: "qq_official"
    parts = e.get_messages()
    parts[0].message_str = f"📖 焦土 〔菜单 {tag}〕\n1. 候选1\n2. 候选2" if tag else "📖 焦土"
    e.message_obj.message_id += str(tag)
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

    def test_quoted_current_menu_uses_new_body_and_identity(self):
        menu, _ = self.ready()
        self.store.bind(menu, ["menu-1"])
        e = quoted("选　２", sender="")  # Official QQ may only supply the reply ID.
        e.message_obj.message_str = "quoted old body 7; new body 2"
        self.assertEqual(self.store.handle(e).index, 1)
        self.assertEqual(self.store.handle(e).kind, "duplicate")
        self.assertIsNone(reply(quoted("")))
        self.assertIsNone(reply(quoted("/2")))
        self.assertIsNone(reply(quoted("这是什么")))

    def test_quotes_do_not_cross_owner_session_adapter_or_page(self):
        menu, _ = self.ready()
        self.store.bind(menu, ["menu-1"])
        self.assertEqual(self.store.handle(quoted(user="other")).kind, "quote_owner")
        self.assertEqual(self.store.handle(quoted(group="other")).kind, "quote_unknown")
        other = quoted(); other.unified_msg_origin = "other:GroupMessage:x"
        self.assertEqual(self.store.handle(other).kind, "quote_unknown")
        self.assertEqual(self.store.handle(quoted("下一页")).kind, "page")
        self.store.activate(menu); self.store.bind(menu, ["menu-2"])
        self.assertEqual(self.store.handle(quoted("6")).kind, "quote_page")
        self.assertEqual(self.store.handle(quoted("6", mid="menu-2")).index, 5)

    def test_old_unknown_and_expired_quotes_keep_latest_menu(self):
        old, _ = self.ready()
        self.store.bind(old, ["menu-1"])
        new, _ = self.ready()
        self.assertEqual(self.store.handle(quoted()).kind, "quote_expired")
        self.assertTrue(self.store.current(new))
        self.assertEqual(self.store.handle(quoted(mid="missing")).kind, "quote_unknown")
        self.assertTrue(self.store.current(new))
        self.store.bind(new, ["menu-2"])
        self.now = 121
        self.assertEqual(self.store.handle(quoted(mid="menu-2")).kind, "quote_expired")
        self.now = 241; self.store.prune()
        self.assertFalse(self.store.receipts)
        for i in range(20):
            menu, _ = self.ready()
            self.store.bind(menu, [str(i)])
        self.assertEqual(len(self.store.receipts), 8)
        self.store.clear()
        self.assertFalse(self.store.receipts)

    def test_unrelated_quote_is_ordinary_chat_and_extra_components_rejected(self):
        self.ready()
        self.assertIsNone(self.store.handle(quoted(sender="someone-else")))
        self.assertFalse(self.store.menus)
        e = quoted()
        for part in (type("Image", (), {})(), type("At", (), {"qq":"other"})(),
                     type("Reply", (), {"id":"another"})()):
            e.get_messages = lambda p=part: quoted().get_messages() + [p]
            self.assertIsNone(reply(e))

    def test_onebot_missing_original_retains_raw_quote_id(self):
        menu, _ = self.ready()
        self.store.bind(menu, ["menu-1"])
        e = event("2")
        e.message_obj.raw_message = {"message":[{"type":"reply", "data":{"id":"unknown"}},
                                               {"type":"text", "data":{"text":"2"}}]}
        self.assertEqual(self.store.handle(e).kind, "quote_unknown")
        self.assertEqual(menu.state, "ready")

    def test_official_content_only_quote_requires_sent_unique_tag(self):
        menu, _ = self.ready(official("/wiki 焦土"))
        self.assertEqual(self.store.handle(official("2", menu.tag)).kind, "quote_unknown")
        self.store.bind(menu, [], tag=menu.tag)
        e = official("选 2", menu.tag)
        self.assertEqual(self.store.handle(e).index, 1)
        self.assertEqual(self.store.handle(e).kind, "duplicate")

    def test_identical_new_query_old_page_and_other_owner_cannot_reuse_tag(self):
        old, _ = self.ready(official("/wiki 焦土"))
        old_tag = old.tag
        self.store.bind(old, [], tag=old_tag)
        new, _ = self.ready(official("/wiki 焦土"))
        self.store.bind(new, [], tag=new.tag)
        self.assertNotEqual(new.tag, old_tag)
        self.assertEqual(self.store.handle(official("1", old_tag)).kind, "quote_expired")
        self.assertEqual(self.store.handle(official("1", new.tag, user="other")).kind, "quote_owner")
        self.assertEqual(self.store.handle(official("1", new.tag, group="other")).kind, "quote_unknown")
        page1 = new.tag
        self.assertEqual(self.store.handle(official("下一页", page1)).kind, "page")
        self.store.activate(new); self.store.bind(new, [], tag=new.tag)
        self.assertNotEqual(new.tag, page1)
        self.assertEqual(self.store.handle(official("6", page1)).kind, "quote_page")
        self.assertEqual(self.store.handle(official("6", new.tag)).index, 5)

    def test_raw_official_quote_fallback_and_diagnostics_do_not_log_payload(self):
        menu, _ = self.ready(official("/wiki 焦土"))
        self.store.bind(menu, [], tag=menu.tag)
        e = official("2", menu.tag)
        content = e.get_messages()[0].message_str
        e.message_obj.raw_message = {"message_type":103, "msg_elements":[
            {"content":content, "msg_idx":"REFIDX_fixture"}],
            "message_scene":{"ext":["auth_token=private-fixture"]}}
        plain = e.get_messages()[1]
        e.get_messages = lambda: [plain]  # SDK omitted Reply, raw content survives.
        facts = str(quote_diagnostics(e))
        self.assertNotIn(menu.tag, facts)
        self.assertNotIn("private-fixture", facts)
        self.assertNotIn("焦土", facts)
        self.assertEqual(self.store.handle(e).index, 1)

    def test_content_tag_never_uses_new_body_or_guess_between_multiple_menus(self):
        menu, _ = self.ready(official("/wiki 焦土"))
        self.store.bind(menu, [], tag=menu.tag)
        e = official(f"2 〔菜单 {menu.tag}〕")
        self.assertIsNone(reply(e))
        e = official("2", menu.tag)
        e.get_messages()[0].message_str += f"\n〔菜单 {menu.tag}〕"
        self.assertEqual(self.store.handle(e).kind, "quote_unknown")
        self.assertEqual(menu.state, "ready")
        self.now = 121
        self.assertEqual(self.store.handle(official("选 2", menu.tag)).kind, "quote_expired")

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

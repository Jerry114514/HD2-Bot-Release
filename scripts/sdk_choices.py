"""Multi-message Wiki interactions through the real SDK pipeline, offline."""
import asyncio
import gc
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def check_choices(modules, scheduler, config, Event, sandbox):
    from astrbot.api.message_components import At, Plain, Reply, Image
    from astrbot_plugin_hd2_common.choices import choices, owner
    from astrbot.core.star.star import star_map
    from astrbot.core.pipeline.respond import stage as respond

    clock = [0]
    count = 0
    selected_ids = []
    wiki = modules["wiki"]
    data = {"status":"ambiguous", "title":"120MM", "query":"120MM", "url":"https://example.invalid/wiki",
            "rev":1, "warnings":[], "search_complete":True, "text":"", "tables":[],
            "candidates":[{"title":f"词条{i}", "pageid":100+i,"reason":"fixture"} for i in range(1,8)]}

    def lookup(keyword, include_tables=True, deadline=None, selected=None):
        if selected is None:
            return deepcopy(data)
        selected_ids.append(selected["pageid"])
        return {**data,"candidates":[],"status":"resolved","title":selected["title"],"text":"选定正文"}

    def make(text, platform="qq_official", user="user-A", group="group-A", adapter="adapter-A", private=False,
             mention=False, mid=None):
        nonlocal count
        count += 1
        event = Event(text, platform, mention, private)
        event.message_obj.sender.user_id = user
        event.message_obj.group_id = "" if private else group
        event.message_obj.message_id = mid or f"choices-{count}"
        event.session.session_id = user if private else group
        event.session.platform_name = event.session.platform_id = adapter
        event.platform_meta.id = adapter
        return event

    async def send(text, expected=1, contains=None, **kwargs):
        event = make(text, **kwargs)
        await scheduler.execute(event)
        assert len(event.sent) == expected, (text, event.sent)
        if contains:
            assert contains in "\n".join(c.text for chain in event.sent for c in chain if isinstance(c, Plain)), event.sent
        assert event.is_stopped(), text
        return event

    async def menu(**kwargs):
        event = await send("/wiki 120MM t", contains="1. 词条1", **kwargs)
        assert choices.menus[owner(event)].state == "ready"
        return event

    choices.clear()
    with patch.object(choices, "clock", side_effect=lambda: clock[0]), \
         patch.object(wiki,"wiki_lookup",side_effect=lookup) as queried, \
         patch.object(wiki,"render",return_value=str(sandbox / "fixture.png")) as render:
        for platform in ("qq_official", "qq_official_webhook", "aiocqhttp"):
            for private in (False, True):
                await menu(platform=platform,private=private)
                await send("2",contains="选定正文",platform=platform,private=private)
                assert selected_ids[-1] == 102
                assert not choices.menus
        render.assert_not_called()  # The original /wiki ... t preference survives selection.

        await send("/wiki 120MM")
        image = await send("2",mention=True)
        assert any(isinstance(part,Image) for part in image.sent[0])

        first = await menu()
        second = await menu(user="user-B")
        await send("1",expected=0,group="other-group")
        await send("1",expected=0,adapter="other-adapter")
        await send("1",expected=0,private=True)
        await send("选 2",user="user-B",contains="选定正文")
        assert owner(first) in choices.menus and owner(second) not in choices.menus
        await send("6",contains="1–5")
        await send("下一页",contains="6. 词条6")
        await send("下一页",contains="边界")
        await send("上一页",contains="1. 词条1")
        await send("下一页",contains="6. 词条6")
        await send("6",contains="选定正文",mid="selected-once")
        before=queried.call_count
        await send("6",expected=0,mid="selected-once")
        assert queried.call_count == before and selected_ids[-1] == 106

        for interruption in ("/roll t", "普通文字", "/unknown"):
            initial=await menu()
            await send(interruption,expected=int(interruption=="/roll t"))
            assert owner(initial) not in choices.menus
            await send("2",expected=0)
        await menu()
        await send("取消",contains="已取消")
        await send("2",expected=0)
        await menu()
        clock[0] += 121
        await send("2",expected=0)
        assert not choices.menus

        # Mentions to others and quoted numbers must never select a candidate.
        for component in (At(qq="somebody-else"), Reply(id="old-menu", sender_id="fixture-bot")):
            await menu()
            event=make("2")
            event.message_obj.message=[component,Plain("2")]
            await scheduler.execute(event)
            assert not event.sent and not choices.menus

        initial=await menu()
        with patch.object(modules["guard"],"setting",side_effect=lambda *k,default=None:
                          {"platform":"qq_official","groups":["not-this-group"]}.get(k[-1],default)):
            await send("2",expected=0)
            assert owner(initial) not in choices.menus
        await menu()
        wl=scheduler.stages[1]
        with patch.object(wl,"enable_whitelist_check",True),patch.object(wl,"whitelist",["other"]):
            before=queried.call_count
            await send("2",expected=0)
            assert queried.call_count == before
        choices.clear()

        # Per-session disabling of Wiki must not leave a number admitted to LLM.
        from astrbot.core.star.session_plugin_manager import SessionPluginManager
        await menu()
        async def without_wiki(event, handlers):
            return [h for h in handlers if h.handler_module_path != wiki.__name__]
        with patch.object(SessionPluginManager,"filter_handlers_by_session",side_effect=without_wiki):
            await send("2",expected=0)
            assert not choices.menus

        # A second number must not delete the in-flight selection's menu.
        await menu()
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(fn,*args,**kwargs):
            if kwargs.get("selected") or args[0] == "slow":
                started.set()
                await release.wait()
            return fn(*args,**kwargs)
        with patch.object(wiki,"wiki_blocking",side_effect=delayed):
            active=make("2")
            task=asyncio.create_task(scheduler.execute(active))
            try:
                await asyncio.wait_for(started.wait(),3)
                await send("3",contains="正在查询")
                assert choices.menus[owner(active)].state == "loading"
            finally:
                release.set()
            await task
            assert len(active.sent)==1 and not choices.menus
            # Slow prior search cannot overwrite a newer menu.
            started.clear();release.clear()
            stale=make("/wiki slow")
            task=asyncio.create_task(scheduler.execute(stale))
            try:
                await asyncio.wait_for(started.wait(),3)
                current=await menu()
                latest=choices.menus[owner(current)]
            finally:
                release.set()
            await task
            assert not stale.sent and choices.menus[owner(current)] is latest

        # Lookup failure allows an explicit retry; it does not rerun automatically.
        with patch.object(wiki,"wiki_lookup",side_effect=TimeoutError("fixture")):
            await send("2",contains="查询超时")
        await send("选 2",contains="选定正文")

        # The waiting window bounds admission, not an already admitted lookup.
        await menu()
        clock[0] += 119
        def across_expiry(*args,**kwargs):
            clock[0] += 5
            return lookup(*args,**kwargs)
        with patch.object(wiki,"wiki_lookup",side_effect=across_expiry):
            await send("2",contains="选定正文")
        assert not choices.menus

        # Failed prompts and cancelled sends cannot leave invisible active menus.
        broken=make("/wiki 120MM")
        async def fail(_):
            raise RuntimeError("fixture choice send failure")
        broken.send=fail
        with patch.object(respond.logger,"error"):
            await scheduler.execute(broken)
        assert not broken._has_send_oper and not choices.menus

        from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry
        async def stop(event):
            event.clear_result();event.stop_event()
        hook=StarHandlerMetadata(EventType.OnDecoratingResultEvent,"choice_stop","stop",modules["monitor"].__name__,stop,[])
        star_handlers_registry.append(hook)
        try:
            await send("/wiki 120MM",expected=0)
            assert all(m.state != "ready" for m in choices.menus.values())
            gc.collect()
            # Closing each nested SDK async generator schedules the next close.
            for _ in range(20):
                if not choices.menus:
                    break
                await asyncio.sleep(0)
            assert not choices.menus, [(m.state,m.value["title"] if m.value else None) for m in choices.menus.values()]
        finally:
            star_handlers_registry.remove(hook)
        await menu()
        await star_map[wiki.__name__].star_cls.terminate()
        assert not choices.menus and not choices.seen

        # Actual adapter sends: the chosen result replies to the selection event,
        # not to the expired original search message or another member's session.
        from botpy.message import GroupMessage, C2CMessage
        from aiocqhttp import Event as OneBotEvent
        from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import QQOfficialMessageEvent
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
        from astrbot.core.utils.metrics import Metric
        for platform in ("qq_official", "aiocqhttp"):
            for private in (False, True):
                for image in (False, True):
                    requests=[]
                    async def http(route,json):
                        requests.append((route.url,json))
                        return {"id":"response-id","file_info":"media-info","file_uuid":"uuid","ttl":300}
                    api=SimpleNamespace(_http=SimpleNamespace(request=AsyncMock(side_effect=http)),
                                        post_group_message=AsyncMock(return_value={"id":"response-id"}))
                    bot=SimpleNamespace(api=api,send_group_msg=AsyncMock(),send_private_msg=AsyncMock())
                    def native(text):
                        base=make(text,platform=platform,private=private)
                        message=base.message_obj
                        if platform=="qq_official":
                            raw={"id":message.message_id,"content":text,"group_openid":message.group_id,
                                 "author":{"user_openid":message.sender.user_id,"member_openid":message.sender.user_id}}
                            message.raw_message=(C2CMessage if private else GroupMessage)(api,"fixture-event",raw)
                            cls=QQOfficialMessageEvent
                        else:
                            message.sender.user_id="20001"
                            message.group_id="" if private else "10001"
                            message.raw_message=OneBotEvent({"post_type":"message","self_id":90001,"user_id":20001,"group_id":10001})
                            cls=AiocqhttpMessageEvent
                        return cls(text,message,base.platform_meta,base.session_id,bot)
                    with patch.object(Metric,"upload",new=AsyncMock()):
                        initial=native("/wiki 120MM"+ ("" if image else " t"))
                        await scheduler.execute(initial)
                        assert choices.menus[owner(initial)].state=="ready"
                        chosen=native("2")
                        await scheduler.execute(chosen)
                    assert chosen._has_send_oper and not choices.menus
                    if platform=="qq_official":
                        payload=requests[-1][1] if private else api.post_group_message.call_args.kwargs
                        assert payload["msg_id"]==chosen.message_obj.message_id != initial.message_obj.message_id
                        assert payload["msg_type"]==(7 if image else 0)
                        if not private:
                            assert payload["group_openid"]==chosen.message_obj.group_id
                        if image:
                            assert payload["media"]["file_info"]=="media-info"
                    else:
                        send_api=bot.send_private_msg if private else bot.send_group_msg
                        assert send_api.await_count==2
                        payload=send_api.call_args.kwargs
                        assert payload["user_id" if private else "group_id"]==(20001 if private else 10001)
                        assert payload["message"][0]["type"]==("image" if image else "text")
    return count

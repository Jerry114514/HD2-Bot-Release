"""Quoted Wiki choices through real ingress, dispatch and send; no network."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def check_quoted_choices(modules, scheduler, Event, sandbox):
    from aiocqhttp import Event as OneBotEvent
    from botpy.message import GroupMessage, C2CMessage
    from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import QQOfficialPlatformAdapter
    from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import QQOfficialMessageEvent
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from astrbot.core.utils.metrics import Metric
    from astrbot_plugin_hd2_common.choices import choices, owner

    wiki = modules["wiki"]
    data = {"status":"ambiguous", "title":"焦土", "query":"焦土", "url":"https://example.invalid/wiki",
            "rev":1, "warnings":[], "search_complete":True, "text":"", "tables":[],
            "candidates":[{"title":f"词条{i}", "pageid":100+i,"reason":"fixture"} for i in range(1,8)]}
    selected_ids = []
    def lookup(keyword, include_tables=True, deadline=None, selected=None):
        if selected is None:
            return deepcopy(data)
        selected_ids.append(selected["pageid"])
        return {**data, "candidates":[], "status":"resolved", "title":selected["title"], "text":"选定正文"}

    count = 0
    clock = [0]
    with patch.object(wiki, "wiki_lookup", side_effect=lookup), \
         patch.object(wiki, "render", return_value=str(sandbox / "fixture.png")), \
         patch.object(choices, "clock", side_effect=lambda: clock[0]), \
         patch.object(modules["guard"], "setting", side_effect=lambda *k, default=None:
                      True if k[-1] == "allow_chat" else default), \
         patch.object(scheduler.stages[3], "reply_prefix", "fixture prefix: "), \
         patch.object(scheduler.stages[3], "reply_with_mention", True), \
         patch.object(scheduler.stages[3], "reply_with_quote", True), \
         patch.object(Metric, "upload", new=AsyncMock()):
        for platform in ("qq_official", "qq_official_webhook", "aiocqhttp"):
            for private in (False, True):
                choices.clear()
                outbox = {}
                seq = [7000]
                incoming = [0]
                def record(payload):
                    seq[0] += 1
                    outbox[str(seq[0])] = deepcopy(payload)
                    return {"id":str(seq[0]), "message_id":seq[0]}
                async def official_send(**payload):
                    return record(payload)
                async def http(route, json):
                    return record(json)
                async def onebot_send(**payload):
                    return record(payload)
                async def call_action(action, message_id=None, **kwargs):
                    assert action == "get_msg", action
                    return {"message_id":message_id, "message_type":"private" if private else "group",
                            "self_id":90001, "user_id":90001, "group_id":10001,
                            "sender":{"user_id":90001}, "message":outbox[str(message_id)]["message"]}
                api = SimpleNamespace(post_group_message=AsyncMock(side_effect=official_send),
                                      _http=SimpleNamespace(request=AsyncMock(side_effect=http)))
                bot = SimpleNamespace(api=api, send_group_msg=AsyncMock(side_effect=onebot_send),
                                      send_private_msg=AsyncMock(side_effect=onebot_send),
                                      call_action=AsyncMock(side_effect=call_action))
                adapter = object.__new__(AiocqhttpAdapter)
                adapter.bot = bot

                async def make(text, quote=None, user="20001", group="10001", adapter_id="adapter-A"):
                    incoming[0] += 1
                    base = Event(text, platform, True, private)
                    base.platform_meta.id = adapter_id
                    if platform.startswith("qq_official"):
                        raw = {"id":f"incoming-{incoming[0]}", "content":text, "group_openid":group,
                               "author":{"user_openid":user, "member_openid":user}}
                        if quote is not None:
                            raw["message_reference"] = {"message_id":quote}
                            # Quote body is deliberately absent: official QQ can supply only the ID.
                        message = (C2CMessage if private else GroupMessage)(api, "fixture-event", raw)
                        abm = await QQOfficialPlatformAdapter._parse_from_qqofficial(
                            message, base.message_obj.type, force_group_mention=True)
                        cls = QQOfficialMessageEvent
                    else:
                        parts = ([{"type":"reply", "data":{"id":quote}}] if quote is not None else [])
                        parts.append({"type":"text", "data":{"text":text}})
                        raw = OneBotEvent({"post_type":"message", "message_type":"private" if private else "group",
                            "self_id":90001, "user_id":int(user), "group_id":int(group),
                            "message_id":incoming[0], "sender":{"user_id":int(user)}, "message":parts})
                        abm = await adapter.convert_message(raw)
                        cls = AiocqhttpMessageEvent
                    return cls(abm.message_str, abm, base.platform_meta, user if private else group, bot)

                async def send(text, quote=None, contains=None, **kwargs):
                    nonlocal count
                    event = await make(text, quote, **kwargs)
                    before = len(outbox)
                    await scheduler.execute(event)
                    count += 1
                    assert event.is_stopped() and len(outbox) == before + 1, (platform, private, text, outbox)
                    payload = outbox[str(seq[0])]
                    body = payload.get("content") or "".join(
                        p["data"].get("text", "") for p in payload.get("message", []))
                    if contains:
                        assert contains in body, (platform, private, text, body)
                    assert event.bot is bot and "send" not in vars(event) and "_post_send" not in vars(event)
                    return event, str(seq[0])

                initial, mid = await send("/wiki 焦土 t", contains="1. 词条1")
                assert (*owner(initial)[:3], mid) in choices.receipts
                await send("选 ２", mid, contains="选定正文")
                assert selected_ids[-1] == 102 and not choices.menus
                await send("2", mid, contains="已失效")

                initial, old = await send("/wiki 焦土 t")
                _, latest = await send("/wiki 焦土 t")
                await send("1", old, contains="已失效")
                assert choices.menus[owner(initial)].state == "ready"
                await send("1", latest, user="20002", contains="其他人" if not private else "无法确认")
                if not private:
                    await send("1", latest, group="10002", contains="无法确认")
                await send("1", latest, adapter_id="adapter-B", contains="无法确认")
                _, page2 = await send("下一页", latest, contains="6. 词条6")
                await send("6", latest, contains="旧一页")
                await send("6", page2, contains="选定正文")
                assert selected_ids[-1] == 106

                _, mid = await send("/wiki 焦土 t")
                clock[0] += 121
                await send("1", mid, contains="已失效")
                _, mid = await send("/wiki 焦土 t")
                await send("取消", mid, contains="已取消")
                await send("/wiki 焦土 t")
                if platform == "aiocqhttp":
                    # The SDK cannot fetch a deleted original and omits Reply.
                    # Its raw reply ID must still prevent selecting the new menu.
                    with patch.object(bot, "call_action", new=AsyncMock(side_effect=RuntimeError("deleted quote"))):
                        await send("2", "999999", contains="无法确认")
                    assert len(choices.menus) == 1
                await send("2", contains="选定正文")  # Original unquoted interaction remains valid.
    choices.clear()
    return count

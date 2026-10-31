"""Exercise real adapter sends with only their network API replaced."""
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def check_transports(modules, scheduler, Event, sandbox):
    from botpy.message import GroupMessage, C2CMessage
    from aiocqhttp import Event as OneBotEvent
    from astrbot.core.platform.sources.qqofficial.qqofficial_message_event import QQOfficialMessageEvent
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    from astrbot.core.utils.metrics import Metric
    from astrbot.core.pipeline.respond import stage as respond

    count = 0
    image = sandbox / "fixture.png"
    for platform in ("qq_official", "aiocqhttp"):
        for private in (False, True):
            for as_image in (False, True):
                base = Event("/roll" if as_image else "/roll t", platform, True, private)
                message = base.message_obj
                api_calls = []
                async def http(route, json):
                    api_calls.append((route.url, json))
                    return {"id": "fixture-id", "file_uuid": "fixture-uuid", "file_info": "fixture-info", "ttl": 300}
                api = SimpleNamespace(_http=SimpleNamespace(request=AsyncMock(side_effect=http)),
                                      post_group_message=AsyncMock(return_value={"id": "fixture-id"}))
                bot = SimpleNamespace(api=api, send_group_msg=AsyncMock(), send_private_msg=AsyncMock())
                if platform == "qq_official":
                    raw = {"id": message.message_id, "content": message.message_str, "group_openid": message.group_id,
                           "author": {"user_openid": message.sender.user_id, "member_openid": message.sender.user_id}}
                    message.raw_message = (C2CMessage if private else GroupMessage)(api, "fixture-event", raw)
                    cls = QQOfficialMessageEvent
                else:
                    message.sender.user_id = "20001"
                    message.group_id = "" if private else "10001"
                    message.raw_message = OneBotEvent({"post_type": "message", "self_id": 90001,
                                                       "user_id": 20001, "group_id": 10001})
                    cls = AiocqhttpMessageEvent
                event = cls(base.message_str, message, base.platform_meta, "fixture-session", bot)
                with patch.object(Metric, "upload", new=AsyncMock()), \
                     patch.object(modules["roll"], "render", return_value=str(image)):
                    await scheduler.execute(event)
                    await asyncio.sleep(0)
                assert event._has_send_oper and event.is_stopped(), (platform, private, as_image)
                if platform == "qq_official":
                    payload = api_calls[-1][1] if private else api.post_group_message.call_args.kwargs
                    assert payload["msg_id"] == message.message_id
                    assert payload["msg_type"] == (7 if as_image else 0), payload
                    if not private:
                        assert payload["group_openid"] == message.group_id
                        api.post_group_message.assert_awaited_once()
                    if as_image:
                        upload = next(body for url, body in api_calls if url.endswith("/files"))
                        assert base64.b64decode(upload["file_data"]) == image.read_bytes()
                        assert payload["media"]["file_info"] == "fixture-info"
                    else:
                        assert payload["content"] == "fixture seven slots" and not payload.get("markdown")
                else:
                    send = bot.send_private_msg if private else bot.send_group_msg
                    send.assert_awaited_once()
                    payload = send.call_args.kwargs
                    assert payload["user_id" if private else "group_id"] == (20001 if private else 10001)
                    assert payload["self_id"] == 90001
                    component = payload["message"][0]
                    assert component["type"] == ("image" if as_image else "text")
                    if as_image:
                        assert base64.b64decode(component["data"]["file"].removeprefix("base64://")) == image.read_bytes()
                count += 1

    # A transport failure must not rerun generation or fall through to a model.
    base = Event("/roll", "aiocqhttp", False)
    base.message_obj.group_id = "10001"
    bot = SimpleNamespace(send_group_msg=AsyncMock(side_effect=RuntimeError("fixture send failure")))
    event = AiocqhttpMessageEvent(base.message_str, base.message_obj, base.platform_meta, "10001", bot)
    with patch.object(modules["roll"], "draw", return_value={}) as draw, patch.object(respond.logger, "error") as logged:
        await scheduler.execute(event)
        draw.assert_called_once()
        bot.send_group_msg.assert_awaited_once()
        assert event.is_stopped() and not event._has_send_oper
        assert any("fixture send failure" in str(call) for call in logged.call_args_list)
    return count + 1

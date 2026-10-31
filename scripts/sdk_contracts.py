"""Additional real-SDK behavior checks; called only by sdk_pipeline.py."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def audit(modules, scheduler, config, Event, sandbox, check, llm_attempts):
    from astrbot.core.pipeline.result_decorate import stage as decorate
    failures = []
    count = 0

    async def case(name, fn):
        nonlocal count
        try:
            await fn()
        except AssertionError as error:
            failures.append(f"{name}: {error}")
        count += 1

    async def help_once():
        await check("/help")
    await case("HD2 help must reply once with built-in help loaded", help_once)

    async def welcome():
        event = Event("", "aiocqhttp", False)
        event.message_obj.raw_message = {"post_type": "notice", "notice_type": "group_increase",
                                         "group_id": "fixture-group", "user_id": "new-user"}
        await scheduler.execute(event)
        assert len(event.sent) == 1, event.sent
    await case("welcome notice survives command guard", welcome)

    async def plain_stays_plain():
        from astrbot_plugin_hd2_common import commands
        config["t2i"] = True
        try:
            with patch.object(commands, "result", return_value=SimpleNamespace(text="正文" * 300, document={})), \
                 patch.object(decorate.html_renderer, "render_t2i", new=AsyncMock(return_value=str(sandbox / "fixture.png"))) as render:
                await check("/战报 t")
                render.assert_not_awaited()
        finally:
            config["t2i"] = False
    await case("explicit text must bypass global t2i", plain_stays_plain)

    from astrbot.core.star.star import star_map
    from astrbot_plugin_hd2_common import commands
    wiki_value = {"status": "resolved", "query": "AR23", "title": "AR-23", "url": "https://example.invalid/wiki",
                  "rev": 1, "text": "fixture wiki", "candidates": [], "tables": [], "warnings": []}
    scan = {"variants": {key: ["X-45"] for key in modules["variant_query"].VARIANT_CN},
            "complete": True, "source": "fixture", "source_time": None, "fetched_at": "fixture time",
            "unknown_effects": [], "warnings": []}
    with patch.object(modules["wiki"], "wiki_lookup", return_value=wiki_value), \
         patch.object(modules["wiki"], "render", side_effect=ImportError("fixture")), \
         patch.object(modules["variant_query"], "_scan_all_variants", new=AsyncMock(return_value=scan)):
        for command in ("/战线", "/dss", "/战役", "/银河星图", "/查询 X45", "/X45信息", "/wiki AR23", "/变种 孢裂变种", "/帮助 t", "/help t", "/roll 纯随机 t"):
            await case("command " + command, lambda command=command: check(command))
        for module, method, command in ((modules["wiki"], "render", "/wiki AR23"), (modules["lookup"], "_render_help_card", "/帮助")):
            async def image(module=module, method=method, command=command):
                with patch.object(module, method, return_value=str(sandbox / "fixture.png")):
                    await check(command, image_pages=1)
            await case("image " + command, image)
        async def planet_pages():
            with patch.object(commands, "render_result", return_value=[str(sandbox / "fixture.png")] * 2):
                await check("/查询 X45", image_pages=2)
        await case("planet pages", planet_pages)

    # Explicit analysis uses the requested SDK signature but no real model.
    war = star_map[modules["war_report"].__name__].star_cls
    from astrbot.api.star import Context
    import inspect
    async def generate(**kwargs):
        inspect.signature(Context.llm_generate).bind(None, **kwargs)
        return SimpleNamespace(completion_text="fixture analysis")
    with patch.object(war, "context", SimpleNamespace(get_config=lambda umo: {"provider_settings": {"default_provider_id": "fixture"}},
                                                     llm_generate=AsyncMock(side_effect=generate))):
        await case("explicit analysis", lambda: check("/分析"))

    async def error_replies():
        for command, module, name in (("/战报", commands, "result"), ("/查询 X45", commands, "result"),
                                     ("/roll", modules["roll"], "draw"), ("/wiki failure", modules["wiki"], "wiki_lookup"),
                                     ("/变种 孢裂变种", modules["variant_query"], "_scan_all_variants"),
                                     ("/帮助", modules["lookup"], "_build_help_text")):
            with patch.object(module, name, side_effect=RuntimeError("fixture failure")) as failed:
                event = Event(command, "qq_official", True)
                await scheduler.execute(event)
                assert len(event.sent) == 1 and event.sent[0][0].text.startswith("⚠️"), command
                assert event.is_stopped() and failed.call_count == 1, command
        for command in ("/roll unknown", "/战报 extra", "/help extra"):
            event = Event(command, "qq_official", True)
            await scheduler.execute(event)
            assert len(event.sent) == 1 and event.is_stopped(), command
    await case("errors and usage replies stop once", error_replies)

    async def notice_limits():
        plugin = star_map[modules["welcome"].__name__].star_cls
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
        from aiocqhttp import Event as OneBotEvent
        async def notice(uid, expected, notice_type="group_increase"):
            raw = OneBotEvent({"post_type": "notice", "notice_type": notice_type,
                               "group_id": 10001, "user_id": uid, "self_id": 90001})
            message = await AiocqhttpAdapter._convert_handle_notice_event(None, raw)
            event = Event("", "aiocqhttp", False)
            event.message_obj = message
            await scheduler.execute(event)
            assert len(event.sent) == expected, (uid, event.sent)
        await notice(20001, 1)
        await notice(20001, 0)  # duplicate
        await notice(90001, 0)  # the bot itself
        await notice(20002, 0, "group_decrease")
        with patch.object(plugin, "_cfg", {"enabled": False}):
            await notice(20003, 0)
        with patch.object(plugin, "_cfg", {"groups": [10002]}):
            await notice(20004, 0)
        wl = scheduler.stages[1]
        with patch.object(wl, "enable_whitelist_check", True), patch.object(wl, "whitelist", ["10002"]):
            await notice(20005, 0)
    await case("real OneBot notice conversion and restrictions", notice_limits)

    async def lifecycle():
        entered = asyncio.Event()
        async def warm():
            entered.set()
            await asyncio.Future()
        with patch.object(war, "_warm_loop", warm):
            await war.initialize()
            await entered.wait()
            task = war._warm_task
            await war.terminate()
            assert task.cancelled() and war._warm_task is None
            await war.terminate()
    await case("war warm task unload", lifecycle)

    async def cancelled_query():
        import threading
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        def draw(mode):
            loop.call_soon_threadsafe(entered.set)
            release.wait(3)
            return {}
        event = Event("/roll", "qq_official", True)
        with patch.object(modules["roll"], "draw", side_effect=draw):
            task = asyncio.create_task(scheduler.execute(event))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done(), "Cancelled thread still owns its capacity"
            finally:
                release.set()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert not event.sent and event.is_stopped()
    await case("cancelled blocking command emits no late reply", cancelled_query)

    async def stopped_by_hook():
        import gc
        from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry
        from astrbot_plugin_hd2_common.runtime import request_id
        async def stop(event):
            event.clear_result()
            event.stop_event()
        hook = StarHandlerMetadata(EventType.OnDecoratingResultEvent, "offline_stop", "stop",
                                   modules["monitor"].__name__, stop, [])
        loop = asyncio.get_running_loop()
        errors = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, error: errors.append(error))
        before = request_id.get()
        star_handlers_registry.append(hook)
        try:
            event = Event("/roll", "qq_official", True)
            await scheduler.execute(event)
            assert not event.sent, ("send hook was not applied", event.sent)
            gc.collect()
            for _ in range(6):
                await asyncio.sleep(0)
            assert not errors, errors
            assert request_id.get() == before, "Request context leaked after a stopped send"
        finally:
            star_handlers_registry.remove(hook)
            loop.set_exception_handler(previous)
    await case("send hook may stop without leaking request context", stopped_by_hook)

    from sdk_transports import check_transports
    count += await check_transports(modules, scheduler, Event, sandbox)
    assert not llm_attempts, f"Unexpected default LLM requests: {llm_attempts}"
    assert not failures, "\n".join(failures)
    return count

"""Offline regression through real AstrBot wake, dispatch and response stages.

Run separately from tests/framework.py, which installs SDK doubles. Network and
transport are blocked; data/rendering are fixtures, not live platform acceptance.
"""
import asyncio
import importlib
import importlib.metadata
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "plugins")]


def main():
    sandbox = ROOT / ".qa-artifacts/sdk-pipeline"
    sandbox.mkdir(parents=True, exist_ok=True)
    os.chdir(sandbox)
    os.environ["ASTRBOT_ROOT"] = str(sandbox)
    os.environ["HD2_PROJECT_DIR"] = str(ROOT)
    config = sandbox / "offline-config.json"
    config.write_text("{}", encoding="utf-8")
    os.environ["HD2_CONFIG"] = str(config)

    # Windows creates a local socket pair for the event loop. Create it before
    # denying connects; subsequent imports/handlers cannot open network sockets.
    loop = asyncio.new_event_loop()
    def offline(*args, **kwargs):
        raise OSError("SDK pipeline regression is offline")
    socket.socket.connect = offline
    socket.create_connection = offline

    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.pipeline.context import PipelineContext
    from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
    from astrbot.core.pipeline.process_stage.method.star_request import StarRequestSubStage
    from astrbot.core.pipeline.respond.stage import RespondStage
    from astrbot.core.pipeline.scheduler import PipelineScheduler
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import star_handlers_registry
    from astrbot.core.star.session_plugin_manager import SessionPluginManager
    from astrbot_plugin_hd2_common import commands, diagnostics
    diagnostics.LOG_FILE = str(sandbox / "commands.jsonl")

    modules = {}
    for name in ("monitor", "guard", "war_report", "roll", "wiki", "planet_info", "variant_query", "lookup"):
        module = importlib.import_module("astrbot_plugin_hd2_" + name + ".main")
        modules[name] = module
        metadata = star_map[module.__name__]
        metadata.name = "hd2_" + name
        metadata.star_cls = metadata.star_cls_type(None)
        for handler in star_handlers_registry.get_handlers_by_module_name(module.__name__):
            handler.handler = getattr(metadata.star_cls, handler.handler_name)

    class CaptureEvent(AstrMessageEvent):
        def __init__(self, text, platform, mention, private=False):
            message = AstrBotMessage()
            message.type = MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE
            message.self_id = "fixture-bot"
            message.sender = MessageMember("fixture-user", "tester")
            message.group_id = "" if private else "fixture-group"
            message.message_id = "fixture-message"
            message.message_str = text
            message.raw_message = {}
            message.message = ([At(qq=message.self_id)] if mention else []) + [Plain(text)]
            super().__init__(text, message, PlatformMetadata(platform, "offline", "fixture-adapter"), "fixture-session")
            self.sent = []

        async def send(self, message):
            self.sent.append(list(message.chain))
            self._has_send_oper = True

    async def run():
        config = {
            "admins_id": [], "wake_prefix": ["/"], "plugin_set": ["*"],
            "provider_settings": {"prompt_prefix": "", "identifier": False, "enable": False},
            "platform_settings": {"reply_with_mention": False, "reply_with_quote": False,
                                  "segmented_reply": {"enable": False, "only_llm_result": False,
                                                      "interval_method": "random", "log_base": 2.6}},
        }
        ctx = PipelineContext(config, None, "offline")
        scheduler = object.__new__(PipelineScheduler)
        scheduler.ctx = ctx
        scheduler.stages = [WakingCheckStage(), StarRequestSubStage(), RespondStage()]
        for stage in scheduler.stages:
            await stage.initialize(ctx)
        count = 0

        async def check(text, platform="qq_official", mention=True, private=False, expected=True, image_pages=0):
            nonlocal count
            event = CaptureEvent(text, platform, mention, private)
            await scheduler.execute(event)
            if text.startswith("/") and "/" in config["wake_prefix"]:
                assert event.message_str == text[1:].strip(), "Real waking stage did not strip the prefix"
            assert len(event.sent) == (image_pages or int(expected)), (text, platform, event.message_str, event.sent)
            if expected:
                for chain in event.sent:
                    assert any(isinstance(c, Image) if image_pages else isinstance(c, Plain) and c.text.strip() for c in chain), text
                    assert not any(isinstance(c, Plain) and c.text.startswith(("⚠️", ":(")) for c in chain), chain
            assert event.is_stopped(), text
            count += 1

        # Session storage is isolated. Real handlers, ordering and stop/yield behavior stay intact.
        with patch.object(SessionPluginManager, "filter_handlers_by_session", new=AsyncMock(side_effect=lambda event, handlers: handlers)), \
             patch.object(commands, "result", side_effect=lambda kind, *a, **kw: SimpleNamespace(text="fixture " + kind, document={})), \
             patch.object(commands, "render_result", side_effect=ImportError("text fallback fixture")), \
             patch.object(modules["roll"], "draw", return_value={}), \
             patch.object(modules["roll"], "format_result", return_value="fixture seven slots"), \
             patch.object(modules["roll"], "render", side_effect=ImportError("text fallback fixture")), \
             patch.object(modules["lookup"], "_render_help_card", None), \
             patch.object(modules["lookup"], "_build_help_text", return_value="fixture help"):
            for platform in ("qq_official", "aiocqhttp"):
                for mention in (False, True):
                    for text in ("/roll", "/api", "/战报", "/战报 t", "/wiki", "/星球", "/变种", "/帮助"):
                        await check(text, platform, mention)
                for text in ("roll", "api", "战报", "普通消息", "/unknown"):
                    await check(text, platform, expected=False)
                await check("/roll", platform, mention=False, private=True)
                await check("roll", platform, mention=False, private=True, expected=False)
            # Receiving an @ with no configured slash wake prefix also works.
            config["wake_prefix"] = ["!"]
            await check("/roll", expected=True)
            config["wake_prefix"] = ["/"]
            settings = {"groups": [12345], "platform": "napcat"}
            with patch.object(modules["guard"], "setting", side_effect=lambda *k, default=None: settings.get(k[-1], default)):
                # Legacy NapCat IDs must not block official OpenIDs, while the
                # NapCat list and an explicitly configured official list still block.
                await check("/roll", "qq_official")
                await check("/roll", "qq_official_webhook")
                await check("/roll", "aiocqhttp", expected=False)
                settings["platform"] = "qq_official"
                await check("/roll", "qq_official", expected=False)
                await check("/roll", "qq_official_webhook", expected=False)
                settings["groups"] = ["fixture-group"]
                await check("/roll", "qq_official")
                await check("/roll", "qq_official_webhook")
            from PIL import Image as PillowImage
            fixture_image = sandbox / "fixture.png"
            PillowImage.new("RGB", (2, 2), "black").save(fixture_image)
            with patch.object(modules["roll"], "render", return_value=str(fixture_image)):
                await check("/roll", image_pages=1)
            with patch.object(commands, "render_result", return_value=[str(fixture_image)] * 2):
                await check("/战报", image_pages=2)
        print(f"AstrBot {importlib.metadata.version('AstrBot')}: {count} pipeline cases passed; no real sends")

    with asyncio.Runner(loop_factory=lambda: loop) as runner:
        runner.run(run())


if __name__ == "__main__":
    main()

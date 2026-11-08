import asyncio
import unittest
from types import SimpleNamespace

from test_core import ROOT
from astrbot_plugin_hd2_common.menu_receipts import capture_menu_receipts, message_id


def chain(text):
    return SimpleNamespace(chain=[type("Plain", (), {"text":text})()])


class ReceiptsTests(unittest.TestCase):
    def test_public_receipts_segments_and_unrelated_sends(self):
        async def run():
            class Event:
                get_platform_name = lambda self: "other"
                async def send(self, message):
                    return {"message_id":message.chain[0].text}
            event = Event()
            first, second = chain("first"), chain("second")
            result = SimpleNamespace(chain=first.chain + second.chain)
            with capture_menu_receipts(event, result) as ids:
                first.chain[0].text = "prefix: first"
                await event.send(first)
                await event.send(second)
                await event.send(chain("unrelated notification"))
                await event.send(chain("second"))  # Same text is not the menu's component.
            self.assertEqual(ids, ["prefix: first", "second"])
            self.assertNotIn("send", vars(event))
        asyncio.run(run())

    def test_onebot_proxy_is_event_local_and_restored_on_failure_or_cancel(self):
        async def run():
            class Client:
                async def send_group_msg(self, **kwargs):
                    return {"message_id":123}
            shared = Client()
            class Event:
                get_platform_name = lambda self: "aiocqhttp"
                async def send(self, message):
                    await self.bot.send_group_msg(message=message)
                    if self.error:
                        raise self.error
            event = Event(); event.bot = shared
            other = Event(); other.bot = shared
            for error in (None, RuntimeError("send failed"), asyncio.CancelledError()):
                event.error = error
                result = chain("menu")
                with capture_menu_receipts(event, result) as ids:
                    try:
                        await event.send(result)
                    except (RuntimeError, asyncio.CancelledError):
                        pass
                    self.assertIs(event.bot, shared)
                    self.assertIs(other.bot, shared)
                self.assertEqual(ids, [] if error else ["123"])
                self.assertNotIn("send", vars(event))
            self.assertNotIn("send_group_msg", vars(shared))
        asyncio.run(run())

    def test_official_private_send_observation_restored(self):
        async def run():
            class Event:
                get_platform_name = lambda self: "qq_official"
                async def _post_send(self):
                    return SimpleNamespace(id="qq-menu")
                async def send(self, message):
                    await self._post_send()  # Public send intentionally returns None.
            event = Event()
            result = chain("menu")
            with capture_menu_receipts(event, result) as ids:
                await event.send(result)
            self.assertEqual(ids, ["qq-menu"])
            self.assertFalse(vars(event))
            self.assertIsNone(message_id(None))
            self.assertIsNone(message_id(SimpleNamespace(id=object())))
        asyncio.run(run())

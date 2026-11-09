"""Observe menu receipts without bypassing AstrBot's decorate/respond pipeline.

AstrBot 4.29.3's official QQ and OneBot event.send discard transport receipts.
These adapters are observed only on the current event during its menu send;
shared clients, SDK classes and unrelated sends are never patched.
"""
from contextlib import contextmanager


def message_id(response):
    if isinstance(response, dict):
        value = response.get("message_id") or response.get("id")
    else:
        value = getattr(response, "message_id", None) or getattr(response, "id", None)
    return str(value) if isinstance(value, (str, int)) and value else None


@contextmanager
def _replace(obj, name, value):
    missing = object()
    previous = vars(obj).get(name, missing)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if previous is missing:
            delattr(obj, name)
        else:
            setattr(obj, name, previous)


class _OneBotReceipts:
    def __init__(self, client, record):
        self.client = client
        self.record = record

    def __getattr__(self, name):
        method = getattr(self.client, name)
        if name not in ("send_group_msg", "send_private_msg", "send"):
            return method

        async def observed(*args, **kwargs):
            result = await method(*args, **kwargs)
            self.record(result)
            return result
        return observed


class MenuDelivery(list):
    sent = False


@contextmanager
def capture_menu_receipts(event, menu_result):
    """Yield IDs from successful sends of this prompt (including text segments).

Unsupported adapters still send normally. A completed send can bind the menu's
content tag even without a receipt ID. All temporary event attributes are restored
on failure, cancellation or an interrupted generator.
    """
    receipts = MenuDelivery()
    original_send = event.send

    async def send(message):
        parts = [part for part in getattr(message, "chain", ()) if type(part).__name__ == "Plain"]
        # Respond derives new chains but keeps the decorated result components.
        # Identity survives prefixes/segmentation and excludes unrelated sends,
        # even when an after-send hook emits identical text.
        candidates = getattr(menu_result, "chain", ())
        if not parts or not all(any(part is candidate for candidate in candidates) for part in parts):
            return await original_send(message)
        pending = []

        def record(result):
            mid = message_id(result)
            if mid and mid not in pending:
                pending.append(mid)

        platform = event.get_platform_name()
        post_send = getattr(event, "_post_send", None)
        if platform in ("qq_official", "qq_official_webhook") and callable(post_send):
            async def observed(*args, **kwargs):
                result = await post_send(*args, **kwargs)
                record(result)
                return result
            with _replace(event, "_post_send", observed):
                result = await original_send(message)
        elif platform == "aiocqhttp" and getattr(event, "bot", None) is not None:
            with _replace(event, "bot", _OneBotReceipts(event.bot, record)):
                result = await original_send(message)
        else:
            result = await original_send(message)
        record(result)  # Adapters that expose a public receipt need no shim.
        receipts.extend(mid for mid in pending if mid not in receipts)
        receipts.sent = bool(getattr(event, "_has_send_oper", True))
        return result

    with _replace(event, "send", send):
        yield receipts

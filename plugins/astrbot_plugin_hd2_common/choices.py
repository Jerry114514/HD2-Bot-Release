"""Short-lived Wiki choices, scoped to one sender and conversation.

All operations run on the message event loop. No conversations or credentials
are persisted, and a superseded request cannot publish a new menu.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import re
import time
import unicodedata
from types import SimpleNamespace


PAGE_SIZE = 5
TTL = 120


def owner(event):
    message = event.message_obj
    sender = str(event.get_sender_id() or "")
    origin = str(getattr(event, "unified_msg_origin", "") or "")
    if not sender or not origin:
        return None
    return (event.get_platform_name(), origin, str(getattr(message, "group_id", "") or ""), sender)


def quoted_messages(event):
    quotes = [p for p in event.get_messages() if type(p).__name__ == "Reply"]
    # OneBot drops Reply when fetching the original message fails. Retain its
    # raw ID so a deleted/unavailable quote cannot turn into a plain selection.
    if not quotes and event.get_platform_name() == "aiocqhttp":
        raw = getattr(event.message_obj, "raw_message", None)
        parts = raw.get("message", []) if isinstance(raw, dict) else []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "reply":
                    data = part.get("data")
                    data = data if isinstance(data, dict) else {}
                    quotes.append(SimpleNamespace(id=data.get("id", ""), sender_id=""))
    return quotes


def reply(event):
    # Read only the new body, never numbers inside quoted text or attachments.
    # Never use the wake-prefix-stripped event.message_str for this test.
    parts = event.get_messages()
    if len(quoted_messages(event)) > 1:
        return None
    body = []
    for part in parts:
        name = type(part).__name__
        if name == "At":
            target = str(getattr(part, "qq", ""))
            official_marker = (target == "qq_official" and event.get_platform_name() in
                               ("qq_official", "qq_official_webhook"))
            if target == str(event.get_self_id()) or official_marker:
                continue
        if name == "Reply":
            continue
        if name != "Plain":
            return None
        body.append(getattr(part, "text", ""))
    original = "".join(body) if parts else getattr(event.message_obj, "message_str", event.message_str)
    text = unicodedata.normalize("NFKC", original or "").strip()
    bot = re.escape(str(event.get_self_id()))
    text = re.sub(r"^(?:<@!?" + bot + r">|@" + bot + r"\s+)\s*", "", text).strip()
    if text in ("下一页", "上一页", "取消"):
        return text
    match = re.fullmatch(r"(?:选\s*)?([0-9]{1,3})", text)
    return int(match[1]) if match else None


@dataclass
class Menu:
    key: tuple
    plain: bool
    expires: float
    value: dict | None = None
    page: int = 0
    state: str = "loading"
    publication: int = 0


@dataclass
class MenuReceipt:
    menu: Menu
    publication: int
    expires: float


@dataclass
class Action:
    kind: str
    menu: Menu | None = None
    index: int | None = None


class ChoiceStore:
    def __init__(self, clock=time.monotonic, capacity=256):
        self.clock = clock
        self.capacity = capacity
        self.menus = OrderedDict()
        self.seen = OrderedDict()
        self.receipts = OrderedDict()

    def prune(self):
        now = self.clock()
        for records in (self.menus, self.seen, self.receipts):
            for key, item in list(records.items()):
                if isinstance(item, Menu) and item.state == "loading":
                    continue  # Admitted lookups have their own bounded query budget.
                expiry = item.expires if isinstance(item, (Menu, MenuReceipt)) else item
                if expiry <= now:
                    records.pop(key, None)

    def clear(self):
        self.menus.clear()
        self.seen.clear()
        self.receipts.clear()

    def cancel(self, event):
        self.menus.pop(owner(event), None)

    def begin(self, event, plain):
        self.prune()
        key = owner(event)
        if key is None:
            return None
        menu = Menu(key, plain, self.clock() + TTL)
        self.menus[key] = menu
        self.menus.move_to_end(key)
        while len(self.menus) > self.capacity:
            self.menus.popitem(last=False)
        return menu

    def current(self, menu):
        return bool(menu and self.menus.get(menu.key) is menu and
                    (menu.state == "loading" or menu.expires > self.clock()))

    def discard(self, menu):
        if menu and self.menus.get(menu.key) is menu:
            self.menus.pop(menu.key)

    def prepare(self, menu, value):
        if not self.current(menu):
            return False
        menu.value = deepcopy(value)
        menu.page = 0
        menu.state = "publishing"
        menu.publication += 1
        menu.expires = self.clock() + TTL
        return True

    def activate(self, menu, refresh=False):
        if self.current(menu):
            menu.state = "ready"
            if refresh:
                menu.expires = self.clock() + TTL
            elif menu.expires <= self.clock():
                self.discard(menu)

    def bind(self, menu, message_ids):
        if not self.current(menu):
            return
        for mid in message_ids:
            key = (*menu.key[:3], str(mid))
            # Keep short-lived tombstones so old/other-user menus get an error.
            self.receipts[key] = MenuReceipt(menu, menu.publication, self.clock() + TTL * 2)
            self.receipts.move_to_end(key)
        while len(self.receipts) > self.capacity * 4:
            self.receipts.popitem(last=False)

    def quoted_action(self, event, menu):
        quotes = quoted_messages(event)
        if not quotes:
            return None
        quote = quotes[0]
        key = owner(event)
        mid = str(getattr(quote, "id", "") or "")
        record = self.receipts.get((*key[:3], mid)) if key and mid else None
        sender = str(getattr(quote, "sender_id", "") or "")
        if sender and sender != str(event.get_self_id()):
            return Action("quote_unrelated")
        if record is None:
            return Action("quote_unknown")
        if record.menu.key != key:
            return Action("quote_owner")
        if not self.current(record.menu) or record.menu is not menu:
            return Action("quote_expired")
        if record.publication != menu.publication:
            return Action("quote_page")
        return None

    def handle(self, event):
        self.prune()
        key = owner(event)
        value = reply(event)
        menu = self.menus.get(key)
        if value is None:
            self.cancel(event)
            return None
        message_id = str(getattr(event.message_obj, "message_id", "") or "")
        receipt = (key, message_id)
        if message_id and receipt in self.seen:
            return Action("duplicate")
        quote_action = self.quoted_action(event, menu)
        if quote_action is not None and quote_action.kind == "quote_unrelated":
            self.cancel(event)
            return None
        if menu is None and quote_action is None:
            return None
        if message_id:
            self.seen[receipt] = self.clock() + TTL
            while len(self.seen) > self.capacity * 4:
                self.seen.popitem(last=False)
        if quote_action is not None:
            return quote_action
        if value == "取消":
            self.discard(menu)
            return Action("cancelled", menu)
        if menu.state != "ready":
            return Action("busy", menu)
        total = len(menu.value["candidates"])
        if value in ("下一页", "上一页"):
            target = menu.page + (1 if value == "下一页" else -1)
            if target < 0 or target * PAGE_SIZE >= total:
                return Action("boundary", menu)
            menu.page = target
            menu.state = "publishing"
            menu.publication += 1
            return Action("page", menu)
        start = menu.page * PAGE_SIZE
        if not start < value <= min(start + PAGE_SIZE, total):
            return Action("range", menu)
        menu.state = "loading"
        return Action("select", menu, value - 1)


choices = ChoiceStore()

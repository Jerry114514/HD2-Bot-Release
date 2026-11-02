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


PAGE_SIZE = 5
TTL = 120


def owner(event):
    message = event.message_obj
    sender = str(event.get_sender_id() or "")
    origin = str(getattr(event, "unified_msg_origin", "") or "")
    if not sender or not origin:
        return None
    return (event.get_platform_name(), origin, str(getattr(message, "group_id", "") or ""), sender)


def reply(event):
    # A number embedded in an attachment, a quote or a message to somebody else
    # is not a selection. Never use the wake-prefix-stripped text for this test.
    for part in event.get_messages():
        name = type(part).__name__
        if name == "At" and str(getattr(part, "qq", "")) == str(event.get_self_id()):
            continue
        if name != "Plain":
            return None
    original = getattr(event.message_obj, "message_str", event.message_str)
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

    def prune(self):
        now = self.clock()
        for records in (self.menus, self.seen):
            for key, item in list(records.items()):
                if isinstance(item, Menu) and item.state == "loading":
                    continue  # Admitted lookups have their own bounded query budget.
                expiry = item.expires if isinstance(item, Menu) else item
                if expiry <= now:
                    records.pop(key, None)

    def clear(self):
        self.menus.clear()
        self.seen.clear()

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
        menu.expires = self.clock() + TTL
        return True

    def activate(self, menu, refresh=False):
        if self.current(menu):
            menu.state = "ready"
            if refresh:
                menu.expires = self.clock() + TTL
            elif menu.expires <= self.clock():
                self.discard(menu)

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
        if menu is None:
            return None
        if message_id:
            self.seen[receipt] = self.clock() + TTL
            while len(self.seen) > self.capacity * 4:
                self.seen.popitem(last=False)
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
            return Action("page", menu)
        start = menu.page * PAGE_SIZE
        if not start < value <= min(start + PAGE_SIZE, total):
            return Action("range", menu)
        menu.state = "loading"
        return Action("select", menu, value - 1)


choices = ChoiceStore()

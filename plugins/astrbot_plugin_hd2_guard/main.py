import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class Hd2GuardPlugin(Star):
    """指令守卫：非 / 开头的消息一律拦截（关闭 LLM 聊天，只响应 / 指令）"""

    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def guard(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        if not text:
            return
        # 被 @ 唤醒且不是 / 开头的消息 -> 拦截，不进入 LLM 聊天
        if event.is_wake_up() and not text.startswith("/"):
            # 只拦截含文本的消息，不拦截纯图片/表情等
            if re.search(r"[\u4e00-\u9fffA-Za-z]", text):
                logger.info(f"[HD2-Guard] 拦截非指令消息: {text[:50]}")
                event.stop_event()
                return

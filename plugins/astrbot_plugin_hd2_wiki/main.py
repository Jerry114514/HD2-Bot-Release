"""Wiki queries retain their text result when optional image dependencies are unavailable."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Star
from astrbot_plugin_hd2_common.commands import parse
from astrbot_plugin_hd2_common.runtime import blocking, new_request, request_id
from astrbot_plugin_hd2_monitor.main import hd2_log
from astrbot_plugin_hd2_wiki.wiki_data import wiki_lookup


def render(keyword, value):
    from astrbot_plugin_hd2_wiki.wiki_card import render_wiki_card
    return render_wiki_card(keyword, data=value)


class Hd2WikiPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event):
        parsed = parse(event.message_str)
        if not parsed or parsed[0] != "wiki":
            return
        _, keyword, plain = parsed
        if not keyword:
            yield event.plain_result("用法：/wiki 关键词")
            event.stop_event()
            return
        token = new_request(event)
        try:
            value = await blocking(wiki_lookup, keyword)
            text = "📖 " + value["title"] + "\n" + value["url"] + "\n\n" + (value["text"] or "未找到该词条。")
            images = None
            if not plain and value["status"] != "not_found":
                try:
                    images = await blocking(render, keyword, value)
                except Exception as error:
                    logger.warning(f"[HD2] Wiki图片不可用：{type(error).__name__}")
            if images:
                for image in images if isinstance(images,list) else [images]:
                    yield event.image_result(image)
            else:
                yield event.plain_result(text)
            hd2_log("generated", plugin="wiki", group=getattr(event.message_obj,"group_id",""),
                    source=value["url"],data_version=str(value["rev"]),
                    user=event.get_sender_id(), platform=getattr(event,"unified_msg_origin",""),
                    output_mode="image" if images else "text", detail="Wiki revision " + str(value["rev"]))
        except Exception as error:
            yield event.plain_result("⚠️ Wiki查询失败：" + str(error))
        finally:
            request_id.reset(token)
            event.stop_event()

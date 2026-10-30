"""Seven-slot result-only draw for every presentation path."""
import os
import sys
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Star
from astrbot_plugin_hd2_common.commands import parse
from astrbot_plugin_hd2_common.runtime import blocking,new_request,request_id
from astrbot_plugin_hd2_common.diagnostics import hd2_log
from astrbot_plugin_hd2_roll.loadout import draw,format_result


def render(result):
    from astrbot_plugin_hd2_roll.roll_card import render_roll_card
    return render_roll_card(result=result)


class Hd2RollPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self,event):
        parsed=parse(event.message_str)
        if not parsed or parsed[0]!="roll":
            return
        _,args,plain=parsed
        if args not in ("","纯随机","random","pure"):
            yield event.plain_result("用法：/roll 或 /roll 纯随机；加 t / 文字查看纯文本。")
            event.stop_event()
            return
        token=new_request(event)
        try:
            result=await blocking(draw,"pure" if args else "assessment")
            text=format_result(result)
            image=None
            if not plain:
                try:
                    image=await blocking(render,result)
                except Exception as error:
                    logger.warning(f"[HD2] 配装图片不可用：{type(error).__name__}")
            yield event.image_result(image) if image else event.plain_result(text)
            hd2_log("generated",plugin="roll",group=getattr(event.message_obj,"group_id",""),
                    user=event.get_sender_id(),platform=getattr(event,"unified_msg_origin",""),
                    output_mode="image" if image else "text")
        except Exception as error:
            hd2_log("failed",plugin="roll",detail=type(error).__name__)
            yield event.plain_result("⚠️ "+str(error))
        finally:
            request_id.reset(token)
            event.stop_event()

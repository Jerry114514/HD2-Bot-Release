# -*- coding: utf-8 -*-
"""astrbot_plugin_hd2_lookup —— /帮助 作战手册（原 /查表 功能已移除）

现在本插件只负责 /帮助 /help：输出黑金 HUD 作战手册图片卡
（渲染器复用 astrbot_plugin_hd2_war_report.report_card 的 HELP_SECTIONS 同源数据）。
渲染失败回退顺序：代码同源纯文本 -> tables/群使用说明.txt。

路径来源：hd2_config（项目根 config.json），可用环境变量 HD2_PROJECT_DIR 指定项目根。
"""
import io
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# 路径解析：hd2_config（项目根 config.json），可用环境变量 HD2_PROJECT_DIR 指定项目根
import os as _os
import sys as _sys

_ROOT = _os.environ.get("HD2_PROJECT_DIR")
if _ROOT and _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
try:
    import hd2_config as _cfg
    _cfg_ok = True
except Exception:
    _cfg_ok = False

# /帮助 图片卡（渲染器在 war_report 插件的 report_card.py，文案代码化同源）
_PLUGINS_PARENT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PLUGINS_PARENT and _PLUGINS_PARENT not in _sys.path:
    _sys.path.insert(0, _PLUGINS_PARENT)
try:
    from astrbot_plugin_hd2_war_report.report_card import (
        render_help_card as _render_help_card,
        build_help_text as _build_help_text,
    )
except Exception:
    _render_help_card = _build_help_text = None


def _tables_dir() -> str:
    """tables 目录：优先 hd2_config.paths 解析；无配置时回退 插件目录 tables 副本 -> 项目根 tables"""
    if _cfg_ok:
        try:
            p = _cfg.get("paths", "var_table", default="tables/HD2行动变量对照表.md")
            return _os.path.dirname(_cfg.resolve(p))
        except Exception:
            pass
    here = _os.path.dirname(_os.path.abspath(__file__))
    for cand in (_os.path.join(here, "tables"), _os.path.join(here, "..", "..", "tables")):
        cand = _os.path.abspath(cand)
        if _os.path.isdir(cand):
            return cand
    return _os.environ.get("HD2_TABLES_DIR", ".")


# 监控埋点（hd2_monitor）：记录执行状态
_MON_PARENT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _MON_PARENT not in _sys.path:
    _sys.path.insert(0, _MON_PARENT)
try:
    from astrbot_plugin_hd2_common.diagnostics import hd2_log as _log
except Exception:
    def _log(*a, **k):
        pass


def _gid(event):
    return str(getattr(event.message_obj, "group_id", "") or "")


def _uid(event):
    return str(event.get_sender_id() or "")


def _uname(event):
    try:
        s = event.get_sender()
        return (getattr(s, "card", "") or getattr(s, "nickname", "") or "")
    except Exception:
        return ""


from astrbot_plugin_hd2_common.runtime import blocking, text_reply, request_scope
from astrbot_plugin_hd2_common.commands import parse_event

class Hd2LookupPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    @request_scope
    async def keyword_trigger(self, event: AstrMessageEvent):
        """帮助触发：/帮助 或 /help"""
        parsed = parse_event(event)
        if not parsed or parsed[0] != "help":
            return
        _, args, plain = parsed
        text = getattr(event.message_obj, "message_str", event.message_str)
        # 过滤机器人自己发出的消息（同号多端登录时避免自我触发）
        try:
            if str(event.get_sender_id()) == str(event.get_self_id()):
                return
        except Exception:
            pass
        if args:
            yield text_reply(event, "用法：/帮助 或 /help；加 t / 文字查看纯文本。")
            event.stop_event()
            return
        _t0 = time.time()
        _log("matched", plugin="lookup", text=text, group=_gid(event), user=_uid(event), name=_uname(event))
        # 作战手册：图片卡优先；文本回退：代码同源文案 -> 群使用说明.txt
        img_path = None
        if _render_help_card and not plain:
            try:
                img_path = await blocking(_render_help_card)
            except Exception as e:
                logger.warning(f"[HD2] 帮助卡渲染失败，回退文本: {e}")
                img_path = None
        if img_path:
            yield event.image_result(img_path)
            _log("done", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail="help-card")
            event.stop_event()
            return
        try:
            guide = _build_help_text() if _build_help_text else ""
            if not guide:
                gpath = _os.path.join(_tables_dir(), "群使用说明.txt")
                with io.open(gpath, encoding="utf-8") as f:
                    guide = f.read().strip()
            yield text_reply(event, guide or "📖 暂无使用说明内容。")
            _log("done", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail="help-text")
        except Exception as e:
            _log("failed", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail=str(e)[:150])
            yield text_reply(event, f"⚠️ 读取使用说明失败：{e}")
        event.stop_event()
        return

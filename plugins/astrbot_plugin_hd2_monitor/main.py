# -*- coding: utf-8 -*-
"""
HD2 指令执行流水监控插件
========================
功能：记录所有群消息事件的接收状态与执行步骤，写入 JSONL 日志，
     供 Web 控制台「指令流水」界面实时展示。

日志文件：<项目根>/temp/指令日志.jsonl（环境变量 HD2_PROJECT_DIR 定位项目根）
数据保留：最近 500 条，自动轮转。

其他插件可通过 `from main import hd2_log` 调用记录执行步骤
（需把本插件目录加入 sys.path）。
"""
import json
import os
import time
import threading

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

_ROOT = os.environ.get("HD2_PROJECT_DIR")
if not _ROOT:
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(_ROOT, "temp", "指令日志.jsonl")
MAX_LINES = 500
_lock = threading.Lock()


def hd2_log(stage: str, plugin: str = "", text: str = "", group=0, user=0,
            name: str = "", detail: str = "", cost=None, wake: bool = False):
    """写入一条执行记录。

    stage: received(接收) / blocked(白名单拦截) / matched(命中插件) /
           done(完成) / failed(失败) / ignored(忽略)
    """
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        rec = {
            "ts": time.time(),
            "time": time.strftime("%m-%d %H:%M:%S"),
            "stage": stage,
            "plugin": plugin or "",
            "text": (text or "")[:80],
            "group": int(group or 0),
            "user": int(user or 0),
            "name": (name or "")[:20],
            "detail": (detail or "")[:300],
            "cost": round(cost, 1) if isinstance(cost, (int, float)) else None,
            "wake": bool(wake),
        }
        with _lock:
            rows = []
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, encoding="utf-8") as f:
                    rows = f.readlines()
            rows.append(json.dumps(rec, ensure_ascii=False) + "\n")
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(rows[-MAX_LINES:])
    except Exception:
        pass


def _sender_name(event: AstrMessageEvent) -> str:
    try:
        s = event.get_sender()
        if s:
            return (getattr(s, "card", "") or getattr(s, "nickname", "") or "")
    except Exception:
        pass
    return ""


def _group_id(event: AstrMessageEvent):
    try:
        obj = event.message_obj
        return int(getattr(obj, "group_id", 0) or 0)
    except Exception:
        return 0


def _user_id(event: AstrMessageEvent):
    try:
        return int(event.get_sender_id() or 0)
    except Exception:
        return 0


class Hd2MonitorPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def monitor(self, event: AstrMessageEvent):
        """记录所有收到的消息事件"""
        try:
            text = (event.message_str or "").strip()
            hd2_log(
                "received",
                text=text,
                group=_group_id(event),
                user=_user_id(event),
                name=_sender_name(event),
                wake=event.is_wake_up(),
            )
        except Exception:
            pass

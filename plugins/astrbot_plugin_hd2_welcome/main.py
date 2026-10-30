# -*- coding: utf-8 -*-
"""HD2 进群欢迎（astrbot_plugin_hd2_welcome）

监听 OneBot v11 群成员增加通知（post_type=notice 且 notice_type=group_increase），
@新成员并播报超级地球风格的欢迎文案 + 机器人全部指令速查。

事件链路：aiocqhttp 适配器把 notice 也转成 GROUP_MESSAGE 进管道，
原始 OneBot 字段保留在 event.message_obj.raw_message（dict 风格），据此识别进群。
群白名单由管道 whitelist_check 统一拦截，本插件不重复过滤；
机器人自己的进群通知会被 waking_check 当作自身消息拦掉，自然到不了这里。

可选配置（本目录 welcome_config.json，缺省即全群生效）：
{
  "enabled": true,          # false 时插件静默
  "groups": [123456789],    # 只在这些群播报；留空/缺省 = 所有白名单群
  "extra_text": "自定义附加段落"
}
"""
import json
import os
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Plain
from astrbot.api.star import Context, Star, register

WELCOME_TEXT = (
    "欢迎来到超级地球第 801 舰队，士兵！🫡\n\n"
    "为民主与自由而战！我是部署在本群的《绝地潜兵2》银河战争播报终端，指令速查：\n\n"
    "📊 /战报 — 银河战争战况速报\n"
    "🗺️ /战线 — 三大阵营战线实时形势\n"
    "🛰️ /dss — 民主空间站状态与捐献进度\n"
    "🎖️ /战役 — 当前重要指令与战役简报\n"
    "🪐 /查询 星球名 — 星球指挥台（例：/查询 克芬达尔）\n"
    "🌌 /银河星图 — 线上中文星图（快捷指令直达）\n"
    "🎯 /roll — 随机抽取一套七格战术配装\n"
    "📡 /api — 游戏 API 变动监测\n"
    "📖 /wiki 关键词 — Wiki 词条查询（helldivers.wiki.gg 中文站）\n\n"
    "指令支持战术 HUD 图片与文字回退；数据时间见输出。\n"
    "民主需要你，士兵——上去给他们点颜色看看！✊"
)

CONF_FILE = "welcome_config.json"
_DEDUPE_TTL = 120  # 秒：同群同员的去重窗口（防 NapCat 重推）


@register("astrbot_plugin_hd2_welcome", "Jerry114514", "HD2 进群欢迎", "1.0.0")
class Hd2WelcomePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._seen = {}  # (gid, uid) -> 上次播报时间戳
        self._cfg = self._load_cfg()

    def _load_cfg(self):
        try:
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONF_FILE)
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception as e:
            logger.warning(f"[HD2欢迎] 配置读取失败，按默认行为运行: {e}")
        return {}

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """群消息统一入口：快速过滤，只处理进群通知。"""
        from astrbot_plugin_hd2_common.data import setting
        if setting("bot", "platform", default="napcat") != "napcat":
            return
        raw = getattr(event.message_obj, "raw_message", None)
        if raw is None:
            return
        get = getattr(raw, "get", None)
        if not callable(get):
            return  # 普通消息/其他平台形态，直接放行
        try:
            if get("post_type") != "notice" or get("notice_type") != "group_increase":
                return
            gid = str(get("group_id") or "")
            uid = str(get("user_id") or "")
        except Exception:
            return
        if not gid or not uid or uid == str(event.get_self_id()):
            return
        if self._cfg.get("enabled") is False:
            return
        allow = self._cfg.get("groups") or []
        if allow and gid not in {str(g) for g in allow}:
            return
        now = time.time()
        key = (gid, uid)
        if now - self._seen.get(key, 0) < _DEDUPE_TTL:
            return
        self._seen[key] = now
        if len(self._seen) > 256:
            self._seen = {k: v for k, v in self._seen.items() if now - v < _DEDUPE_TTL}

        text = WELCOME_TEXT
        extra = (self._cfg.get("extra_text") or "").strip()
        if extra:
            text += "\n\n" + extra
        logger.info(f"[HD2欢迎] 群 {gid} 新成员 {uid}，播报欢迎")
        yield event.chain_result([At(qq=uid), Plain("\n" + text)])

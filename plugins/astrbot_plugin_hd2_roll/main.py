import io
import random
import re
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# 监控埋点（hd2_monitor）：记录执行状态
import os as _os, sys as _sys
_MON_PARENT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _MON_PARENT not in _sys.path:
    _sys.path.insert(0, _MON_PARENT)
try:
    from astrbot_plugin_hd2_monitor.main import hd2_log as _log
except Exception:
    def _log(*a, **k):
        pass


def _gid(event):
    try:
        return int(getattr(event.message_obj, "group_id", 0) or 0)
    except Exception:
        return 0


def _uid(event):
    try:
        return int(event.get_sender_id() or 0)
    except Exception:
        return 0


def _uname(event):
    try:
        s = event.get_sender()
        return (getattr(s, "card", "") or getattr(s, "nickname", "") or "")
    except Exception:
        return ""

# ============ 随机战备 ============
STRATAGEM_POOL = {
    "飞鹰": [
        ("飞鹰扫射", "Eagle Strafing Run"),
        ("飞鹰空袭", "Eagle Airstrike"),
        ("飞鹰集束炸弹", "Eagle Cluster Bomb"),
        ("飞鹰凝固汽油弹空袭", "Eagle Napalm Airstrike"),
        ("飞鹰 500kg 炸弹", "Eagle 500kg Bomb"),
        ("飞鹰烟雾打击", "Eagle Smoke Strike"),
    ],
    "轨道": [
        ("轨道加特林弹幕", "Orbital Gatling Barrage"),
        ("轨道空爆打击", "Orbital Airburst Strike"),
        ("轨道 120mm 高爆弹幕", "Orbital 120mm HE Barrage"),
        ("轨道 380mm 高爆弹幕", "Orbital 380mm HE Barrage"),
        ("轨道行进弹幕", "Orbital Walking Barrage"),
        ("轨道激光", "Orbital Laser"),
        ("轨道磁轨炮打击", "Orbital Railcannon Strike"),
        ("轨道精确打击", "Orbital Precision Strike"),
        ("轨道毒气打击", "Orbital Gas Strike"),
        ("轨道电磁打击", "Orbital EMS Strike"),
        ("轨道烟雾打击", "Orbital Smoke Strike"),
        ("轨道凝固汽油弹弹幕", "Orbital Napalm Barrage"),
    ],
    "背包": [
        ("补给背包", "B-1 Supply Pack"),
        ("弹道盾背包", "SH-20 Ballistic Shield Backpack"),
        ("护盾生成器背包", "SH-32 Shield Generator Pack"),
        ("跳跃背包", "LIFT-850 Jump Pack"),
        ("侦察犬", "AX/AR-23 Guard Dog"),
        ("侦察犬·漫游者", "AX/LAS-5 Guard Dog Rover"),
    ],
    "三号位": [
        ("机枪", "MG-43 Machine Gun"),
        ("反器材步枪", "APW-1 Anti-Materiel Rifle"),
        ("坚定机枪", "M-105 Stalwart"),
        ("一次性反坦克", "EAT-17 Expendable Anti-Tank"),
        ("火焰喷射器", "FLAM-40 Flamethrower"),
        ("重机枪", "MG-206 Heavy Machine Gun"),
        ("激光炮", "LAS-98 Laser Cannon"),
        ("类星体加农炮", "LAS-99 Quasar Cannon"),
        ("磁轨枪", "RS-422 Railgun"),
        ("榴弹发射器", "GL-21 Grenade Launcher"),
        ("电弧投掷器", "ARC-3 Arc Thrower"),
        ("空爆火箭筒", "RL-77 Airburst Rocket Launcher"),
        ("突击导弹", "Commando"),
    ],
    "三号+背包位": [
        ("无后坐力炮", "GR-8 Recoilless Rifle"),
        ("长矛导弹", "SPEAR"),
        ("自动加农炮", "AC-8 Autocannon"),
        ("超重机枪", "MG-1000 HMG"),
        ("弹链榴弹发射器", "Belt-Fed Grenade Launcher"),
        ("焚燃者", "Flamer"),
    ],
    "炮台": [
        ("加特林炮台", "Gatling Sentry"),
        ("迫击炮台", "Mortar Sentry"),
        ("自动加农炮台", "Autocannon Sentry"),
        ("火箭炮台", "Rocket Sentry"),
        ("特斯拉塔", "Tesla Tower"),
        ("哨戒机枪", "Machine Gun Sentry"),
        ("火焰炮台", "Flame Sentry"),
        ("电磁迫击哨戒炮", "EMS Mortar Sentry"),
        ("毒气迫击哨戒炮", "Gas Mortar Sentry"),
    ],
    "地雷": [
        ("反步兵地雷", "Anti-Personnel Mines"),
        ("燃烧地雷", "Incendiary Mines"),
        ("反坦克地雷", "Anti-Tank Mines"),
        ("毒气地雷", "Gas Mines"),
    ],
    "可部署物": [
        ("重机枪阵地", "HMG Emplacement"),
        ("反坦克阵地", "AT Emplacement"),
        ("护盾发生器", "Shield Generator Relay"),
    ],
}

# 同类型>=3 时的评价池（50 字以内）
ROLL_COMMENTS = [
    "真理部建议：战备过于单一，恐遭民主认证驳回，请调整配比。",
    "这套战备的多样性令人堪忧，超级地球评估委员会深表遗憾。",
    "检测到严重偏科，忠诚官已记录在案，请重新抽取。",
    "全部押注一种战术？勇气可嘉，但自由需要更全面的火力。",
    "该组合过于极端，真理部温馨提示：多样化才是民主之道。",
    "这种配装让后勤部血压飙升，建议三思而后行。",
]

def _roll_stratagems(n: int = 4) -> str:
    """从全类型池随机抽 n 个战备；同类型>=3 个时附带 50 字内评价"""
    import random
    from collections import Counter

    flat = []
    for cat, items in STRATAGEM_POOL.items():
        for cn, en in items:
            flat.append((cat, cn, en))
    picked = random.sample(flat, min(n, len(flat)))

    cats = [p[0] for p in picked]
    max_same = max(Counter(cats).values())

    lines = ["🎲 随机战备 Roll 结果："]
    for i, (cat, cn, en) in enumerate(picked, 1):
        lines.append(f"{i}. [{cat}] {cn}（{en}）")
    if max_same >= 3:
        lines.append("📝 " + random.choice(ROLL_COMMENTS))
    return "\n".join(lines)

# ============ 随机战备 END ============


class Hd2RollPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    def _is_at_me(self, event: AstrMessageEvent) -> bool:
        """检查消息中是否 @ 了机器人自己（或引用了机器人）"""
        try:
            from astrbot.core.message.components import At, Reply

            self_id = str(event.get_self_id())
            for m in event.get_messages():
                if isinstance(m, At) and str(getattr(m, "qq", "")) == self_id:
                    return True
                if isinstance(m, Reply) and str(getattr(m, "sender_id", "")) == self_id:
                    return True
        except Exception:
            pass
        # 兜底：文本里包含 @自己 的 CQ 码
        text = event.message_str or ""
        if f"at,qq={self_id}" in text or f"at,qq={self_id}]" in text:
            return True
        return False
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event: AstrMessageEvent):
        """随机战备触发：@ + roll / 随机战备"""
        text = (event.message_str or "").strip().lower()
        if not text:
            return
        # 过滤机器人自己发出的消息（同号多端避免自我触发）
        try:
            if str(event.get_sender_id()) == str(event.get_self_id()):
                return
        except Exception:
            pass
        # 仅响应 / 指令
        if not text.startswith("/"):
            return
        text = text[1:].strip()
        if not any(kw in text for kw in ["roll", "随机战备", "roll战备"]):
            return
        _t0 = time.time()
        _log("matched", plugin="roll", text=text, group=_gid(event), user=_uid(event), name=_uname(event))
        result = _roll_stratagems(4)
        yield event.plain_result(result)
        _log("done", plugin="roll", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0)
        event.stop_event()

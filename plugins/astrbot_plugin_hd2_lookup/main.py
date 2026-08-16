import io
import re
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# ============ /查表 功能 ============
# 数据来源：变量对照表 + 星区对照表（动态读取，用户更新表格后无需重启插件）
# 路径来源：hd2_config（项目根 config.json），可用环境变量 HD2_PROJECT_DIR 指定项目根
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


def _tables_dir() -> str:
    """对照表所在目录：优先 hd2_config.paths 解析；无配置时回退 插件目录 tables 副本 -> 项目根 tables"""
    if _cfg_ok:
        try:
            p = _cfg.get("paths", "var_table", default="tables/HD2行动变量对照表.md")
            return _os.path.dirname(_cfg.resolve(p))
        except Exception:
            pass
    here = _os.path.dirname(_os.path.abspath(__file__))
    for cand in (os.path.join(here, "tables"), os.path.join(here, "..", "..", "tables")):
        cand = os.path.abspath(cand)
        if os.path.isdir(cand):
            return cand
    return _os.environ.get("HD2_TABLES_DIR", ".")


VAR_TABLE_FILE = _os.path.join(_tables_dir(), "HD2行动变量对照表.md")
STARMAP_FILE = _os.path.join(_tables_dir(), "星图对照表_修正版.md")

# 监控埋点（hd2_monitor）：记录执行状态
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

NOT_FOUND_MSG = "未查询到该参数，请输入「/查表」 或 「/查表 星系」查看参数。"


def _parse_var_table() -> list:
    """解析变量对照表 -> [(分类, [(原文, 译文, 描述), ...]), ...]（保持文件顺序）"""
    result = []
    cur = None
    try:
        with io.open(VAR_TABLE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("## "):
                    cur = (line[3:].strip(), [])
                    result.append(cur)
                elif line.startswith("|") and cur is not None:
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if len(cells) < 3:
                        continue
                    en, cn, desc = cells[0], cells[1], cells[2]
                    if en in ("原文/参数", "---", "") or cn in ("---", "") or en.startswith("（"):
                        continue
                    cur[1].append((en, cn, desc))
    except Exception as e:
        logger.warning(f"[HD2-Lookup] 读取变量对照表失败: {e}")
    return result


def _parse_starmap() -> tuple:
    """解析星区对照表 -> (有序星区名列表, {星区名: [(英文, 中文), ...]})"""
    order = []
    sectors = {}
    try:
        with io.open(STARMAP_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("|"):
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) < 3:
                    continue
                sector, en, cn = cells[0], cells[1], cells[2]
                if sector in ("星区", "---") or en in ("英文", "---") or not en:
                    continue
                if sector not in sectors:
                    sectors[sector] = []
                    order.append(sector)
                sectors[sector].append((en, cn))
    except Exception as e:
        logger.warning(f"[HD2-Lookup] 读取星区对照表失败: {e}")
    return order, sectors


def _query_param(q: str):
    """按参数名查询（中英文均可）：精确匹配优先，再包含匹配；返回 (分类, 原文, 译文, 描述) 列表"""
    q_l = q.strip().lower()
    if not q_l:
        return []
    cats = _parse_var_table()
    exact = []
    for cat, items in cats:
        for en, cn, desc in items:
            if en.lower() == q_l or cn == q.strip():
                exact.append((cat, en, cn, desc))
    if exact:
        return exact
    # 包含匹配（长度>=2 防单字误命中）
    if len(q_l) >= 2:
        fuzzy = []
        for cat, items in cats:
            for en, cn, desc in items:
                if q_l in en.lower() or (cn and q_l in cn.lower()):
                    fuzzy.append((cat, en, cn, desc))
        return fuzzy
    return []


def _norm_q(s: str) -> str:
    """规范化查询串：去 emoji/变体选择符/空格，小写（保留括号内容以便匹配英文分类名）"""
    s = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\ufe0f]", "", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def _query_category(q: str):
    """按分类名查询（中英文均可）：规范化后精确优先，再包含；返回 (分类名, [(原文, 译文, 描述), ...]) 或 None"""
    q_n = _norm_q(q)
    if not q_n:
        return None
    cats = _parse_var_table()
    for cat, items in cats:
        if _norm_q(cat) == q_n:
            return (cat, items)
    if len(q_n) >= 2:
        for cat, items in cats:
            if q_n in _norm_q(cat):
                return (cat, items)
    return None


def _find_sector(q: str, order: list, sectors: dict):
    """匹配星区名（中英文均可）：精确优先，再包含；返回匹配的星区显示名或 None"""
    q_l = q.strip().lower()
    if not q_l:
        return None
    for s in order:
        if s.lower() == q_l:
            return s
    for s in order:
        if q_l in s.lower():
            return s
    return None


class Hd2LookupPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event: AstrMessageEvent):
        """查表触发：/查表（分类列表）/查表 参数名/查表 星区/查表 星区 星区名"""
        text = (event.message_str or "").strip()
        if not text:
            return
        # 过滤机器人自己发出的消息（同号多端登录时避免自我触发）
        try:
            if str(event.get_sender_id()) == str(event.get_self_id()):
                return
        except Exception:
            pass
        # 仅响应 / 指令（无需 @）
        if not text.startswith("/"):
            return
        cmd = text[1:].strip()
        _t0 = time.time()
        # /帮助 /help -> 发送群使用说明（读 tables/群使用说明.txt，动态加载）
        if cmd.lower() in ("帮助", "help"):
            _log("matched", plugin="lookup", text=text, group=_gid(event), user=_uid(event), name=_uname(event))
            try:
                gpath = _os.path.join(_tables_dir(), "群使用说明.txt")
                with io.open(gpath, encoding="utf-8") as f:
                    guide = f.read().strip()
                yield event.plain_result(guide or "📖 暂无使用说明内容。")
                _log("done", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0)
            except Exception as e:
                _log("failed", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0, detail=str(e)[:150])
                yield event.plain_result(f"⚠️ 读取使用说明失败：{e}")
            event.stop_event()
            return
        if not (cmd.startswith("查表") or cmd.startswith("查星系")):
            return
        rest = re.sub(r"^(查表|查星系)\s*", "", cmd).strip()
        _log("matched", plugin="lookup", text=text, group=_gid(event), user=_uid(event), name=_uname(event))

        # 1) /查表 -> 变量对照表所有分类
        if not rest:
            cats = _parse_var_table()
            lines = ["📚 变量对照表分类："]
            for i, (cat, items) in enumerate(cats, 1):
                lines.append(f"{i}. {cat}")
            lines.append("")
            lines.append("发送「/查表 <参数名>」查看参数详情（中英文均可）")
            lines.append("发送「/查表 星区」查看星区列表")
            yield event.plain_result("\n".join(lines))
            _log("done", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0)
            event.stop_event()
            return

        # 2) /查表 星区 [星区名]
        m = re.match(r"^(星区|星系)\s*(.*)$", rest)
        if m:
            sector_q = m.group(2).strip()
            order, sectors = _parse_starmap()
            if not sector_q:
                # 星区列表
                lines = ["🗺️ 星区列表："]
                for i, s in enumerate(order, 1):
                    lines.append(f"{i}. {s}")
                lines.append("")
                lines.append("发送「/查表 星区 <星区名>」查看该星区下所有星球")
                yield event.plain_result("\n".join(lines))
            else:
                key = _find_sector(sector_q, order, sectors)
                if key is None:
                    yield event.plain_result(NOT_FOUND_MSG)
                else:
                    lines = [f"🗺️ {key} 星区星球："]
                    for en, cn in sectors[key]:
                        if cn:
                            lines.append(f"• {cn}（{en}）")
                        else:
                            lines.append(f"• {en}")
                    yield event.plain_result("\n".join(lines))
            _log("done", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0)
            event.stop_event()
            return

        # 3) /查表 <参数名或分类名> -> 参数详情（译文+描述）；分类名则输出该分类全部子条目
        hits = _query_param(rest)
        if hits:
            # 去重（同一参数只输出一次，按原文去重）
            seen = set()
            lines = []
            for cat, en, cn, desc in hits:
                key = en.upper()
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"📖 {cn}（{en}）")
                lines.append(f"分类：{cat}")
                if desc:
                    lines.append(f"描述：{desc}")
                lines.append("")
            yield event.plain_result("\n".join(lines).rstrip())
            _log("done", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0)
            event.stop_event()
            return
        # 分类名查询：输出该分类下全部参数
        cat_hit = _query_category(rest)
        if cat_hit:
            cat, items = cat_hit
            lines = [f"📂 {cat}（共 {len(items)} 个参数）："]
            for en, cn, desc in items:
                lines.append(f"• {cn}（{en}）")
                if desc:
                    lines.append(f"  {desc}")
            yield event.plain_result("\n".join(lines))
            _log("done", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0)
            event.stop_event()
            return
        yield event.plain_result(NOT_FOUND_MSG)
        _log("done", plugin="lookup", text=text, group=_gid(event), user=_uid(event), cost=time.time() - _t0)
        event.stop_event()
        return

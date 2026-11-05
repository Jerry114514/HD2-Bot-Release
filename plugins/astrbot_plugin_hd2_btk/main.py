# -*- coding: utf-8 -*-
"""BTK calculator backed by the supplied hd2-btk-sdk v0.12.2."""
import json
import os
from functools import lru_cache
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Star

HERE = Path(__file__).resolve().parent
SDK = HERE / "sdk"
BRIDGE = HERE / "bridge.js"

# AstrBot may load plugins alphabetically; btk can be imported before common.
# Expose the physical plugin parent so the shared namespace is importable now.
_PLUGINS_PARENT = str(HERE.parent)
if _PLUGINS_PARENT not in sys.path:
    sys.path.insert(0, _PLUGINS_PARENT)

from astrbot_plugin_hd2_common.commands import parse_event  # noqa: E402
from astrbot_plugin_hd2_common import errors  # noqa: E402
from astrbot_plugin_hd2_common.data import setting  # noqa: E402
from astrbot_plugin_hd2_common.diagnostics import hd2_log  # noqa: E402
from astrbot_plugin_hd2_common.runtime import blocking, request_scope, text_reply  # noqa: E402

ANGLE_NAMES = ("正面命中", "小角度命中", "大角度命中", "极端角度命中")

HELP_TEXT = """🎯 BTK 武器伤害测试

单个对比：
/btk <武器> <敌人> <部位>
/btk AR-23 武斗虫 头部
/btk AR-23 | 武斗虫 | 头部
/btk DOT-FIRE 强袭虫

可选参数：
距离=20 角度=1 破片=3 波及=头部,主体
角度可用 0–3 或 正面/小角度/大角度/极端角度。

卡阈值：
/btk 卡阈值 AR-23
/btk 卡阈值 AR-23 全部

查询资料：
/btk 武器
/btk 武器 突击步枪
/btk 敌人
/btk 敌人 终结族
/btk 敌人 武斗虫

伤害对比、DoT 与卡阈值结果输出战术 HUD 图片卡；资料菜单保持文字。
裸敌人名默认低难；高难请写「（高难）」。默认距离 0m、正面命中。
名称不确定时，可先用 /wiki <关键词> 查询中文 Wiki 的标准名称和资料。
引擎版本：hd2-btk-sdk v0.14.1。"""

BTK_REFERENCE_HINT = "名称不确定时，可先用 /wiki <关键词> 查询中文 Wiki 的标准名称和资料。"


def _norm(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _base_part(value):
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")).strip()


@lru_cache(maxsize=1)
def _data():
    weapons = json.loads((SDK / "data" / "weapons.json").read_text(encoding="utf-8"))
    enemies = json.loads((SDK / "data" / "enemies.json").read_text(encoding="utf-8"))
    return list(weapons), list(enemies)


def _weapon_aliases(row):
    name = str(row.get("name") or "")
    aliases = {str(row.get("id") or ""), name}
    if " " in name:
        aliases.add(name.split(" ", 1)[1])
    return {alias for alias in aliases if alias}


def _enemy_aliases(row):
    name = str(row.get("enemy") or "")
    aliases = {name}
    # v0.12 splits several enemies into low/high-difficulty records.
    # A bare name preserves the old command and selects the low-difficulty one.
    if name.endswith("（低难）"):
        aliases.add(name[:-4])
    return {alias for alias in aliases if alias}


def _part_aliases(row):
    return {str(row.get("part") or ""), _base_part(row.get("part"))}


def _unique_match(rows, query, aliases, label):
    wanted = _norm(query)
    exact = [row for row in rows if any(_norm(alias) == wanted for alias in aliases(row))]
    unique = {}
    for row in exact:
        unique[json.dumps(row, ensure_ascii=False, sort_keys=True)] = row
    exact = list(unique.values())
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        names = "、".join(str(row.get("id") or row.get("enemy") or row.get("part")) for row in exact[:8])
        raise ValueError(f"{label}有歧义：{names}；请使用完整名称或 ID")
    raise ValueError(f"未找到{label}「{query}」")


def _resolve_weapon(query):
    weapons, _ = _data()
    query = str(query or "").strip()
    try:
        return _unique_match(weapons, query, _weapon_aliases, "武器")
    except ValueError as error:
        target = f"「{query}」" if query else ""
        raise ValueError(
            f"{error}。请发送 /btk 武器 查看武器 ID。{BTK_REFERENCE_HINT}"
            + (f"\n建议查询：/wiki {query}" if target else "")
        ) from error


def _resolve_enemy(query):
    _, enemies = _data()
    query = str(query or "").strip()
    try:
        return _unique_match(enemies, query, _enemy_aliases, "敌人")
    except ValueError as error:
        raise ValueError(
            f"{error}。请发送 /btk 敌人 <关键词> 查看标准敌人名。{BTK_REFERENCE_HINT}"
            + (f"\n建议查询：/wiki {query}" if query else "")
        ) from error


def _resolve_part(enemy, query):
    query = str(query or "").strip()
    enemy_name = str(enemy.get("enemy") or "该敌人")
    wiki_name = re.sub(r"\s*[（(](?:低难|高难)[）)]\s*$", "", enemy_name).strip() or enemy_name
    if not query:
        raise ValueError(
            f"缺少部位。请按 /btk <武器> {enemy_name} <部位> 输入。"
            f"可发送 /btk 敌人 {enemy_name} 查看可用部位；"
            f"也可发送 /wiki {wiki_name} 查询该敌人的部位资料。"
        )
    try:
        return _unique_match(enemy.get("parts") or [], query, _part_aliases, "部位")
    except ValueError as error:
        available = "、".join(str(part.get("part")) for part in (enemy.get("parts") or [])[:10])
        suffix = "……" if len(enemy.get("parts") or []) > 10 else ""
        raise ValueError(
            f"{error}。可用部位：{available}{suffix}。"
            f"可发送 /btk 敌人 {enemy_name} 查看完整列表；"
            f"也可发送 /wiki {wiki_name} 查询部位资料。"
        ) from error


def _match_prefix(rows, text, aliases):
    wanted = _norm(text)
    matches = []
    for row in rows:
        for alias in aliases(row):
            key = _norm(alias)
            if key and wanted.startswith(key):
                matches.append((len(key), alias, row))
    if not matches:
        return None, wanted
    best = max(length for length, _alias, _row in matches)
    selected = []
    for length, alias, row in matches:
        if length == best and row not in selected:
            selected.append(row)
    if len(selected) != 1:
        names = "、".join(str(row.get("id") or row.get("enemy")) for row in selected[:8])
        raise ValueError("名称前缀有歧义：" + names)
    raw_rest = wanted[best:]
    normalized_prefix = ""
    for index, char in enumerate(text):
        normalized_prefix += _norm(char)
        if len(normalized_prefix) >= best:
            raw_rest = text[index + 1:].strip()
            break
    return selected[0], raw_rest


def _parse_angle(value):
    text = str(value or "").strip()
    if text in ANGLE_NAMES:
        return ANGLE_NAMES.index(text)
    names = {
        "正面": 0, "front": 0, "小角度": 1, "slight": 1,
        "大角度": 2, "large": 2, "极端": 3, "extreme": 3,
    }
    if text.casefold() in names:
        return names[text.casefold()]
    try:
        angle = int(text)
    except ValueError as error:
        raise ValueError("角度需为 0–3 或 正面/小角度/大角度/极端角度") from error
    if not 0 <= angle <= 3:
        raise ValueError("角度需为 0–3")
    return angle


def _extract_options(text):
    options = {"distance": 0.0, "angle": 0, "shrapnelHits": 0, "blastParts": []}

    def take(pattern, key, convert):
        nonlocal text
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return
        options[key] = convert(match.group(1))
        text = (text[:match.start()] + " " + text[match.end():]).strip()

    def number(value):
        parsed = float(value)
        if not 0 <= parsed <= 1000:
            raise ValueError("距离需在 0–1000 米之间")
        return parsed

    def shrapnel(value):
        parsed = int(value)
        if not 0 <= parsed <= 10000:
            raise ValueError("破片数需在 0–10000 之间")
        return parsed

    take(r"(?<!\S)(?:距离|distance|d)\s*[=:]\s*(\d+(?:\.\d+)?)", "distance", number)
    take(r"(?<!\S)(?:角度|angle|a)\s*[=:]\s*([^\s]+)", "angle", _parse_angle)
    take(r"(?<!\S)(?:破片|shrapnelHits|shrapnel|s)\s*[=:]\s*(\d+)", "shrapnelHits", shrapnel)
    blast = None
    take(r"(?<!\S)(?:波及|blastParts|blast|b)\s*[=:]\s*([^\s]+)", "blastParts", lambda value: value)
    blast = options["blastParts"] or None
    options["blastParts"] = blast
    return " ".join(text.split()), options


def _target(text):
    weapons, enemies = _data()
    if "|" in text:
        fields = [field.strip() for field in text.split("|")]
        if len(fields) not in (2, 3) or not all(fields):
            raise ValueError("管道格式应为：武器 | 敌人 [| 部位]")
        weapon = _resolve_weapon(fields[0])
        enemy = _resolve_enemy(fields[1])
        if weapon.get("type") == "dot":
            part = None
        elif len(fields) != 3:
            raise ValueError("普通武器需要指定部位：武器 | 敌人 | 部位")
        else:
            part = _resolve_part(enemy, fields[2])
        return weapon, enemy, part
    weapon, rest = _match_prefix(weapons, text, _weapon_aliases)
    if weapon is None:
        raise ValueError(
            "未识别武器。请发送 /btk 武器 查看武器 ID。"
            + BTK_REFERENCE_HINT
        )
    if not rest:
        raise ValueError(
            f"缺少敌人和部位。用法：/btk {weapon['id']} <敌人> <部位>。"
            "请发送 /btk 敌人 <关键词> 查看敌人名称，"
            f"或先用 /wiki {weapon['id']} 查询武器资料。"
        )
    enemy_text = rest
    enemy, rest = _match_prefix(enemies, rest, _enemy_aliases)
    if enemy is None:
        enemy_hint = enemy_text.strip()
        if len(enemy_hint.split()) > 1:
            enemy_hint = enemy_hint.rsplit(None, 1)[0]
        raise ValueError(
            f"未识别敌人「{enemy_hint}」。请发送 /btk 敌人 <关键词> 查看标准名称。"
            f"\n建议查询：/wiki {enemy_hint}"
        )
    if weapon.get("type") == "dot":
        return weapon, enemy, None
    part = _resolve_part(enemy, rest)
    return weapon, enemy, part


def _run_bridge(action, payload):
    node = str(setting("runtime", "node", default="node") or "node")
    try:
        timeout = float(setting("runtime", "btk_timeout_seconds", default=15))
    except (TypeError, ValueError):
        timeout = 15
    timeout = min(60, max(1, timeout))
    try:
        process = subprocess.run(
            [node, str(BRIDGE), action, json.dumps(payload, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
    except FileNotFoundError as error:
        raise RuntimeError("BTK 引擎不可用：未找到 Node 运行时") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("BTK 计算超时") from error
    try:
        response = json.loads((process.stdout or "").strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("BTK 引擎返回异常") from error
    if not response.get("ok"):
        raise ValueError(str(response.get("error") or "BTK 计算失败"))
    return response.get("result")


def _fmt(value, digits=2):
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _format_stuck(stuck):
    if not stuck:
        return None
    kind = stuck.get("kind")
    if kind == "one":
        ratio = stuck.get("ratio")
        coverage = 100 / ratio if ratio else 0
        return f"1 发伤害覆盖 {coverage:.1f}%，溢出 {_fmt(stuck.get('overflow'))}"
    btk = stuck.get("btk")
    ratio = stuck.get("ratio")
    missing = stuck.get("missing")
    return (f"前 {max(0, int(btk or 1) - 1)} 发完成 {float(ratio or 0) * 100:.1f}%，"
            f"差 {_fmt(missing)} 伤害")


def _format_compute(result, options, weapon, enemy, part):
    if not result.get("ok"):
        message = result.get("message") or result.get("reason")
        if not message and result.get("type") == "dot":
            message = "该元素无法穿透目标主体"
        return "🎯 BTK 测试\n" + str(message or "无法计算")
    if result.get("type") == "dot":
        return "\n".join([
            "🎯 BTK 持续伤害测试",
            f"武器：{result.get('weapon') or weapon['name']}（{weapon['id']}）",
            f"目标：{result.get('enemy') or enemy['enemy']} · 主体",
            f"角度：{ANGLE_NAMES[int(options['angle'])]}",
            f"每跳伤害：{_fmt(result.get('perTrigger'))}",
            f"触发次数：{result.get('triggers')}",
            f"总持续时间：{_fmt(result.get('seconds'), 1)} 秒",
            f"元素倍率：×{_fmt(result.get('mult'))}",
            f"穿甲：AP {result.get('ap')} / AV {result.get('av')} · 系数 {_fmt(result.get('factor'))}",
            "DoT 只作用于主体，不参与普通 BTK 卡阈值。",
        ])
    lines = [
        "🎯 BTK 武器伤害测试",
        f"武器：{result.get('weapon') or weapon['name']}（{weapon['id']}）",
        f"目标：{result.get('enemy') or enemy['enemy']} · {result.get('part') or (part['part'] if part else '主体')}",
        (f"条件：距离 {_fmt(options['distance'])} m · "
         f"{ANGLE_NAMES[int(options['angle'])]} · 破片 {options['shrapnelHits']}"),
    ]
    if options["blastParts"]:
        lines.append("波及部位：" + "、".join(options["blastParts"]))
    if result.get("beamSeconds") is not None:
        lines.append(f"累计照射：{_fmt(result['beamSeconds'], 1)} 秒")
    else:
        lines.append(f"BTK：{result.get('btk')} 发")
        if result.get("ttk") is not None:
            lines.append(f"TTK：{_fmt(result['ttk'])} 秒")
    lines.extend([
        f"击杀路径：{result.get('label') or result.get('path') or '—'}",
        f"单次命中伤害：{_fmt(result.get('perShotDamage'))}",
    ])
    if int(result.get("pelletsPerShot") or 1) > 1:
        lines.append(f"弹丸：每发 {result['pelletsPerShot']} 枚，需命中 {result.get('pelletsNeeded')} 枚")
    if result.get("cripple"):
        lines.append(f"打残：{result['cripple'].get('shots')} 发")
    if result.get("full"):
        lines.append(f"致死：{result['full'].get('shots')} 发")
    if float(result.get("bleedSec") or 0) > 0:
        lines.append(f"致死流血时间：{_fmt(result['bleedSec'], 1)} 秒")
    stuck_line = _format_stuck(result.get("stuck"))
    lines.append("卡阈值：" + (stuck_line or "未卡阈值"))
    return "\n".join(lines)


def _format_scan(rows, weapon, fatal_only):
    scope = "致死部位" if fatal_only else "全部部位"
    lines = [f"🔎 {weapon['id']} · {weapon['name']}", f"卡阈值扫描：{scope}，命中 {len(rows)} 个组合"]
    if not rows:
        return "\n".join(lines + ["未发现 BTK≤3 且达到 90% 的阈值组合。"])
    for index, row in enumerate(rows[:12], 1):
        detail = _format_stuck({**row, "btk": row.get("btk")})
        lines.append(f"{index}. {row.get('enemy')} · {row.get('part')}｜BTK {row.get('btk')}｜{detail}")
    if len(rows) > 12:
        lines.append(f"另有 {len(rows) - 12} 个阈值组合未显示。")
    lines.append("口径：距离 0m、正面命中；卡阈值定义为前序命中达到 90%。")
    return "\n".join(lines)


def _weapon_menu(query):
    weapons, _ = _data()
    categories = {}
    for weapon in weapons:
        categories.setdefault(weapon.get("cat") or "未分类", []).append(weapon)
    if not query:
        lines = [f"🔫 BTK 武器库（{len(weapons)} 件）"]
        lines.extend(f"• {name}（{len(rows)}）" for name, rows in sorted(categories.items()))
        lines.append("用法：/btk 武器 <分类>")
        return "\n".join(lines)
    matched = next((name for name in categories if _norm(name) == _norm(query)), None)
    if not matched:
        return "未找到该分类。可用分类：" + "、".join(sorted(categories))
    rows = categories[matched]
    lines = [f"🔫 {matched}（{len(rows)} 件）"]
    lines.extend(f"• {weapon['id']}｜{weapon['name']}" for weapon in rows[:60])
    if len(rows) > 60:
        lines.append(f"另有 {len(rows) - 60} 件未显示。")
    return "\n".join(lines)


def _enemy_menu(query):
    _, enemies = _data()
    if not query:
        factions = {}
        for enemy in enemies:
            factions.setdefault(enemy.get("faction") or "未知", []).append(enemy)
        lines = [f"👾 BTK 敌人库（{len(enemies)} 种）"]
        lines.extend(f"• {name}（{len(rows)}）" for name, rows in sorted(factions.items()))
        lines.append("用法：/btk 敌人 <阵营> 或 /btk 敌人 <敌人名>")
        return "\n".join(lines)
    try:
        enemy = _resolve_enemy(query)
    except ValueError:
        enemy = None
    if enemy:
        lines = [f"👾 {enemy['enemy']}｜{enemy.get('faction')}｜主体 HP {enemy.get('mainHp')}"]
        for part in enemy.get("parts") or []:
            lines.append(
                f"• {part.get('part')}｜HP {part.get('hp') if part.get('hp') is not None else '—'}"
                f"｜AV {part.get('av')}｜{'致死' if part.get('fatal') else '非致死'}"
            )
        lines.append("用法：/btk <武器> " + enemy["enemy"] + " <部位>")
        return "\n".join(lines)
    faction = next(
        (name for name in {str(row.get("faction") or "") for row in enemies}
         if _norm(name) == _norm(query)),
        None,
    )
    if faction:
        rows = [row for row in enemies if row.get("faction") == faction]
        lines = [f"👾 {faction}（{len(rows)} 种）"]
        lines.extend(f"• {row['enemy']}" for row in rows[:80])
        if len(rows) > 80:
            lines.append(f"另有 {len(rows) - 80} 种未显示。")
        return "\n".join(lines)
    return "未找到该敌人或阵营。可先用 /btk 敌人 查看阵营。"


def build_view(args):
    """Build a structured result used by both image cards and text fallback."""
    text = (args or "").strip()
    if not text or text.casefold() in ("帮助", "help", "用法"):
        return {"kind": "info", "text": HELP_TEXT}
    clean, options = _extract_options(text)
    if clean.startswith("卡阈值"):
        rest = clean[len("卡阈值"):].strip()
        fatal_only = not rest.endswith("全部")
        if not fatal_only:
            rest = rest[:-2].strip()
        weapon = _resolve_weapon(rest)
        if weapon.get("type") == "dot":
            return {"kind": "info", "text": (
                "DoT 武器只作用于主体，不参与普通 BTK 卡阈值。\n"
                f"用法：/btk {weapon['id']} <敌人>"
            )}
        rows = _run_bridge("scanStuck", {"weapon": weapon["id"], "fatalOnly": fatal_only})
        return {
            "kind": "scan", "weapon": weapon, "rows": rows,
            "fatalOnly": fatal_only, "text": _format_scan(rows, weapon, fatal_only),
        }
    if clean == "武器" or clean.startswith("武器 "):
        return {"kind": "info", "text": _weapon_menu(clean[len("武器"):].strip())}
    if clean == "敌人" or clean.startswith("敌人 "):
        return {"kind": "info", "text": _enemy_menu(clean[len("敌人"):].strip())}
    weapon, enemy, part = _target(clean)
    blast_names = []
    raw_blast = options.get("blastParts")
    if raw_blast:
        for value in re.split(r"[,，、]", str(raw_blast)):
            if value.strip():
                blast_names.append(_resolve_part(enemy, value.strip())["part"])
    payload = {
        "weapon": weapon["id"],
        "enemy": enemy["enemy"],
        "part": part["part"] if part else "",
        "distance": options["distance"],
        "angle": options["angle"],
        "shrapnelHits": options["shrapnelHits"],
        "blastParts": blast_names,
    }
    result = _run_bridge("compute", payload)
    options["blastParts"] = blast_names
    return {
        "kind": "compute", "result": result, "options": options,
        "weapon": weapon, "enemy": enemy, "part": part,
        "text": _format_compute(result, options, weapon, enemy, part),
    }


def format_view(view):
    return str(view.get("text") or "")


def handle_command(args):
    """Synchronous text representation; retained for menus and fallback."""
    return format_view(build_view(args))


def render(view):
    from astrbot_plugin_hd2_btk.btk_card import render_btk_card
    return render_btk_card(view)


class Hd2BtkPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL)
    @request_scope
    async def keyword_trigger(self, event):
        parsed = parse_event(event)
        if not parsed or parsed[0] != "btk":
            return
        _, args, _plain = parsed
        started = time.monotonic()
        try:
            hd2_log("matched", plugin="btk", text=getattr(event, "message_str", ""),
                    group=getattr(event.message_obj, "group_id", ""), user=event.get_sender_id())
            view = await blocking(build_view, args)
            image = None
            if view.get("kind") in ("compute", "scan"):
                try:
                    image = await blocking(render, view)
                except Exception as render_error:
                    logger.warning(f"[HD2-BTK] 图片不可用，回退文字：{type(render_error).__name__}")
            if image:
                for path in image if isinstance(image, list) else [image]:
                    yield event.image_result(path)
            else:
                yield text_reply(event, format_view(view))
            hd2_log("done", plugin="btk", group=getattr(event.message_obj, "group_id", ""),
                    user=event.get_sender_id(), cost=time.monotonic() - started,
                    output_mode="image" if image else "text")
        except Exception as error:
            logger.warning(f"[HD2-BTK] {type(error).__name__}: {error}")
            hd2_log("failed", plugin="btk", detail=type(error).__name__,
                    group=getattr(event.message_obj, "group_id", ""), user=event.get_sender_id())
            yield text_reply(event, errors.friendly("btk", error))
        finally:
            event.stop_event()

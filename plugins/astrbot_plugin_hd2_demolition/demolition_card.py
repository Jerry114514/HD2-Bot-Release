# -*- coding: utf-8 -*-
"""HUD card for direct-hit and explosive demolition results."""
import hashlib
import json
import os

from astrbot_plugin_hd2_war_report.report_card import (
    W, PAD, BG, PANEL, LINE, LINE2, TEXT, DIM, MUTE, GOLD, GREEN, RED,
    F, _hud_head, _render_cached, wrap,
)
from astrbot_plugin_hd2_common.paths import plugin_cache_dir

# 成品卡缓存落插件数据目录，不写插件安装目录。
CACHE_DIR = str(plugin_cache_dir("astrbot_plugin_hd2_demolition", create=False))


def _result_text(result):
    verdict = result.get("verdict") or {}
    if verdict.get("ok"):
        distance = float(verdict.get("maxDistance") or 0)
        result_text = "可拆毁（命中即可）" if distance <= 0 else f"可拆毁，最大距离 {distance:.2f} m"
        color = GREEN
    elif verdict.get("reason") == "badr":
        result_text = "不可：目标要求爆炸拆毁"
        color = RED
    else:
        result_text = "不可：拆毁值不足"
        if verdict.get("hp"):
            result_text += "（可消耗本体 HP）"
        color = RED
    if result.get("distanceDf") is not None:
        result_text += f"｜距离处 DF {result['distanceDf']}"
    return result_text, color


def _comparison_path_text(path, mode):
    if not path.get("available"):
        return "未核实", MUTE
    verdict = path.get("verdict") or {}
    if verdict.get("ok"):
        distance = float(verdict.get("maxDistance") or 0)
        if mode == "direct":
            return "可直击", GREEN
        return f"可 ≤{distance:.2f} m", GREEN
    if verdict.get("reason") == "badr":
        return "需爆炸", RED
    if verdict.get("reason") == "demolition-insufficient-has-hp":
        return "不足 · 可打 HP", RED
    need = verdict.get("need")
    df = path.get("df")
    if need is not None and df is not None:
        return f"不足 · 差 {max(0, need - df)}", RED
    return "拆毁不足", RED


def _render_common_comparison(view, out_dir):
    source = view["source"]
    mode = view.get("mode")
    digest_source = {
        "mode": mode,
        "source": source["name"],
        "comparisons": view["comparisons"],
    }
    digest = hashlib.sha256(
        json.dumps(digest_source, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    out_path = os.path.join(out_dir, f"demolition_compare_{digest[:16]}.png")
    meta_path = out_path + ".meta"

    def draw(dr, img):
        y = _hud_head(dr, img, "拆毁值常见目标", "DEMOLITION COMPARISON")
        panel_h = 132
        dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
        dr.rectangle([PAD, y, PAD + 4, y + panel_h], fill=GOLD)
        dr.text((PAD + 20, y + 14), source["name"], font=F(30, True), fill=TEXT)
        direct = " / ".join(
            f"{hit.get('label') or '直击'} DF {hit.get('df')}"
            for hit in source.get("direct") or ()
        )
        explosions = " / ".join(
            f"{blast['name']} DF {blast.get('df')}"
            for blast in source.get("blasts") or ()
        )
        yy = y + 58
        if mode != "explosion":
            dr.text(
                (PAD + 20, yy),
                "直击路径：" + (direct or "暂无已核实数据"),
                font=F(16),
                fill=DIM,
            )
            yy += 28
        if mode != "direct":
            for line in wrap("爆炸路径：" + (explosions or "无"), 58)[:2]:
                dr.text((PAD + 20, yy), line, font=F(15), fill=MUTE)
                yy += 22
        y += panel_h + 18

        head_h = 48
        dr.rectangle([PAD, y, W - PAD, y + head_h], fill=(25, 25, 25), outline=LINE2)
        dr.text((PAD + 18, y + 12), "常见拆毁目标", font=F(16, True), fill=GOLD)
        dr.text((PAD + 388, y + 12), "门槛", font=F(16, True), fill=GOLD)
        dr.text((PAD + 522, y + 12), "直击", font=F(16, True), fill=GOLD)
        dr.text((PAD + 732, y + 12), "爆炸", font=F(16, True), fill=GOLD)
        y += head_h

        for row in view["comparisons"]:
            row_h = 76
            dr.rectangle([PAD, y, W - PAD, y + row_h], fill=PANEL, outline=LINE)
            dr.text((PAD + 18, y + 13), row["label"], font=F(21, True), fill=TEXT)
            dr.text((PAD + 18, y + 45), row["group"], font=F(13), fill=MUTE)
            requirement = f"DF {row['df']}"
            if row.get("badr"):
                requirement += " · 需爆"
            dr.text((PAD + 388, y + 24), requirement, font=F(18, True), fill=TEXT)
            if mode != "explosion":
                value, color = _comparison_path_text(row["direct"], "direct")
                dr.text((PAD + 522, y + 25), value, font=F(17, True), fill=color)
            else:
                dr.text((PAD + 522, y + 25), "已隐藏", font=F(15), fill=MUTE)
            if mode != "direct":
                value, color = _comparison_path_text(row["explosion"], "explosion")
                dr.text((PAD + 732, y + 25), value, font=F(17, True), fill=color)
            else:
                dr.text((PAD + 732, y + 25), "已隐藏", font=F(15), fill=MUTE)
            y += row_h + 6

        y += 12
        dr.line([(PAD, y), (W - PAD, y)], fill=LINE2)
        dr.text(
            (PAD, y + 12),
            "直击无距离衰减；爆炸按内圈全额与距离曲线计算。两者独立判定，不跨路径合并 DF。",
            font=F(14),
            fill=MUTE,
        )
        return y + 44

    return _render_cached(out_path, meta_path, "demolition-compare:" + digest, draw)


def render_demolition_card(view, out_dir=None):
    out_dir = out_dir or CACHE_DIR
    os.makedirs(out_dir, exist_ok=True)
    if view.get("view_mode") == "common_comparison":
        return _render_common_comparison(view, out_dir)
    source, structure = view["source"], view["structure"]
    distance = view.get("distance")
    digest_source = {
        "source": source["name"],
        "structure": structure["name"],
        "distance": distance,
        "results": [
            {
                "mode": result["mode"],
                "df": result["df"],
                "distanceDf": result.get("distanceDf"),
                "verdict": result.get("verdict"),
            }
            for result in view["results"]
        ],
    }
    digest = hashlib.sha256(json.dumps(digest_source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    out_path = os.path.join(out_dir, f"demolition_{digest[:16]}.png")
    meta_path = out_path + ".meta"

    def draw(dr, img):
        y = _hud_head(dr, img, "拆毁值评估", "DEMOLITION FORCE")
        panel_h = 116
        dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
        dr.rectangle([PAD, y, PAD + 4, y + panel_h], fill=GOLD)
        dr.text((PAD + 20, y + 14), source["name"], font=F(31, True), fill=TEXT)
        direct = "、".join(f"{hit['label']} DF {hit['df']}" for hit in source.get("direct") or [])
        blasts = "、".join(f"{blast['name']} DF {blast['df']}" for blast in source.get("blasts") or [])
        dr.text((PAD + 20, y + 56), "直击：" + (direct or "无已核实直击 DF"), font=F(16), fill=DIM)
        sub = "爆炸：" + (blasts or "无")
        for line in wrap(sub, 54)[:2]:
            dr.text((PAD + 20, y + 80), line, font=F(15), fill=MUTE)
            break
        y += panel_h + 16

        dr.rectangle([PAD, y, W - PAD, y + 102], fill=PANEL, outline=LINE)
        dr.text((PAD + 20, y + 12), "目标建筑", font=F(17), fill=GOLD)
        dr.text((PAD + 20, y + 38), structure["name"], font=F(28, True), fill=TEXT)
        requirement = f"需求 DF {structure.get('df')}"
        requirement += " · 要求爆炸拆毁" if structure.get("badr") else " · 允许直击拆毁"
        if structure.get("hp"):
            requirement += f" · HP {structure['hp'][0]} / AV {structure['hp'][1]}"
        dr.text((PAD + 20, y + 74), requirement, font=F(16), fill=DIM)
        y += 118

        if distance is not None:
            dr.text((PAD, y), f"距离曲线：{distance:g} m", font=F(20, True), fill=GOLD)
            y += 34
        for index, result in enumerate(view["results"], 1):
            label, color = _result_text(result)
            row_h = 72
            dr.rectangle([PAD, y, W - PAD, y + row_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, PAD + 5, y + row_h], fill=color)
            mode = result["mode"]
            dr.text((PAD + 20, y + 10), mode, font=F(19, True), fill=TEXT)
            value = f"DF {result['df']}"
            if result.get("distanceDf") is not None:
                value += f" → {result['distanceDf']}"
            dr.text((PAD + 20, y + 38), value + "｜" + label, font=F(16), fill=color)
            y += row_h + 8

        conclusion = "至少一种路径达到拆毁门槛" if any(
            (result.get("verdict") or {}).get("ok") for result in view["results"]
        ) else "当前列出的路径均不能拆毁目标"
        dr.text((PAD, y + 4), "结论：" + conclusion, font=F(19, True),
                fill=GREEN if "达到" in conclusion else RED)
        y += 38
        dr.line([(PAD, y), (W - PAD, y)], fill=LINE2)
        dr.text((PAD, y + 12), "直击无距离衰减；爆炸按内圈全额、普通 1−√x、巨型 1−x²，截尾取整。",
                font=F(14), fill=MUTE)
        return y + 42

    return _render_cached(out_path, meta_path, "demolition:" + digest, draw)

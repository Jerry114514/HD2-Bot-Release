# -*- coding: utf-8 -*-
"""report_card.py 补丁 v2：DSS 卡照主站重写（状态三态 + 裁切修复）+ 星球词条去重"""
p = "report_card.py"
b = open(p, encoding="utf-8").read()

# ── 1) 星球卡词条按 cn 名去重（主站：同名效果合并）──
old_chips = '            chips = [emap.get(i) or ("#" + str(i)) for i in eids]'
new_chips = '            chips = list(dict.fromkeys(emap.get(i) or ("#" + str(i)) for i in eids))'
assert old_chips in b, "chips anchor"
b = b.replace(old_chips, new_chips, 1)

# ── 2) DSS 卡 draw 整体重写（render_dss_card 的 def draw 段）──
i0 = b.find("def render_dss_card(")
i1 = b.find("def render_planet_card(", i0)
assert i0 > 0 and i1 > i0, (i0, i1)
seg = b[i0:i1]
d0 = seg.find("        def draw(dr, img):")
d1 = seg.find("        return _render_cached(out_path, meta_path, rev, draw)")
assert d0 > 0 and d1 > d0, (d0, d1)

new_draw = '''        def draw(dr, img):
            planets = {p.get("index"): p for p in (data.get("planets") or []) if p.get("index") is not None}
            pl = planets.get(dss.get("planetIndex")) or {}
            en = (pl.get("name") or "").upper()
            smcn = _starmap_cn_map()
            cn = smcn.get(en) or pl.get("name") or "—"
            emap = _effect_cn_map()
            active_ids = set(dss.get("activeEffectIds") or [])
            cur_war = dss.get("currentWarTime") or 0
            y = _hud_head(dr, img, "民主空间站", "DEMOCRACY SPACE STATION")

            # 停靠星球
            panel_h = 100
            dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
            dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
            section_label(dr, PAD + 18, y + 13, "当前停靠", F(25, True))
            dr.text((PAD + 18, y + 50), cn, font=F(34, True), fill=TEXT)
            sub = en + "  ·  " + (pl.get("sector") or "")
            dr.text((PAD + 18 + dr.textlength(cn, font=F(34, True)) + 18, y + 60),
                    sub, font=F(16), fill=MUTE)
            y += panel_h + 18

            # 被动增益（effect 1238 · 行动支持，口径同主站 tactical_action 分类）
            passive = None
            try:
                v = _site_get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/hd2_variables.json")
                for cat in (v.get("categories") or []):
                    if cat.get("key") != "tactical_action":
                        continue
                    for e in cat.get("entries") or []:
                        if 1238 in (e.get("effect_ids") or []):
                            passive = e.get("cn") or e.get("name") or passive
            except Exception:
                pass

            # 战术行动（照主站三态：已激活 / 冷却中 / 募捐中）
            acts = dss.get("tacticalActions") or []
            if acts:
                blocks = []
                for act in acts:
                    nm_en = act.get("name") or ""
                    nm = DSS_ACTION_SHORT.get(nm_en, nm_en)
                    cost = (act.get("cost") or [{}])[0]
                    cur, tgt = cost.get("current") or 0, cost.get("target") or 0
                    res = ITEM_MIX.get(cost.get("itemMixId"), "资源")
                    md = cost.get("maxDonation")
                    mps = cost.get("maxDonationPeriodSec")
                    quota = ""
                    if isinstance(md, (int, float)) and md > 0 and mps:
                        quota = "每人每 " + str(int(mps // 3600)) + " 小时限捐 " + fmt(md) + " " + res
                    ids = set(act.get("effectIds") or [])
                    is_active = bool(ids & active_ids)
                    is_cool = (act.get("status") == 3) and not is_active
                    exp = act.get("expireAtWarTime") or 0
                    remain_ms = max(0, (exp - cur_war) * 1000) if (exp and cur_war) else None
                    if is_active:
                        phase = "已激活"
                        bar = min(100.0, max(0.0, remain_ms / (24 * 3600 * 1000) * 100)) if remain_ms is not None else 100.0
                    elif is_cool:
                        phase = "冷却中"
                        bar = min(100.0, max(0.0, remain_ms / (24 * 3600 * 1000) * 100)) if remain_ms is not None else 100.0
                    else:
                        phase = "募捐中"
                        bar = (cur / tgt * 100.0) if tgt else 0.0
                    blocks.append((nm, phase, bar, cur, tgt, res, quota, is_active, is_cool))
                panel_h = 54 + sum(124 for _ in blocks) + 10
                dr.rectangle([PAD, y, W - PAD, y + panel_h], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
                section_label(dr, PAD + 18, y + 13, "战术行动", F(25, True))
                yy = y + 54
                for nm, phase, bar, cur, tgt, res, quota, is_active, is_cool in blocks:
                    tone = GREEN if is_active else (BLUE if is_cool else GOLD)
                    dr.text((PAD + 18, yy), nm, font=F(23, True), fill=TEXT)
                    lw = dr.textlength(phase, font=F(19, True))
                    dr.text((W - PAD - 18 - lw, yy + 2), phase, font=F(19, True), fill=tone)
                    yy += 38
                    progress_bar(dr, PAD + 18, yy, W - PAD * 2 - 36, 14, bar / 100.0,
                                 fill=GREEN if is_active else (BLUE if is_cool else GOLD))
                    yy += 24
                    if is_active:
                        line = "效果生效中 · 进度条为剩余时间占比"
                    elif is_cool:
                        line = "冷却中 · 结束后重新进入募捐"
                    else:
                        line = "已筹集 " + fmt(cur) + " / 目标 " + fmt(tgt) + " " + res
                        if quota:
                            line += "  ·  " + quota
                    dr.text((PAD + 18, yy), line, font=F(15), fill=DIM)
                    yy += 62
                y += panel_h + 18
            else:
                dr.rectangle([PAD, y, W - PAD, y + 80], fill=PANEL, outline=LINE)
                dr.text((PAD + 18, y + 26), "当前无进行中的战术行动", font=F(19), fill=MUTE)
                y += 98

            # 被动增益
            if passive:
                dr.rectangle([PAD, y, W - PAD, y + 64], fill=PANEL, outline=LINE)
                dr.rectangle([PAD, y, W - PAD, y + 3], fill=GOLD)
                dr.text((PAD + 18, y + 14), "被动增益", font=F(19, True), fill=MUTE)
                dr.text((PAD + 18, y + 38), passive, font=F(21, True), fill=TEXT)
                y += 82
            # 未收录效果 id（照实显示，口径与主站一致）
            unknown = sorted(i for i in active_ids if i not in emap and i != 1238)
            if unknown:
                note = "未收录效果 ID：" + "、".join("#" + str(i) for i in unknown) + "（照实显示）"
                nw = dr.textlength(note, font=F(13))
                dr.text((W - PAD - 18 - nw, y - 12), note, font=F(13), fill=(80, 80, 80))
            return y + 10
'''

seg = seg[:d0] + new_draw + seg[d1:]
b = b[:i0] + seg + b[i1:]

# ── 3) 被动增益需要原始 variables ──
old_fn = '''def _effect_cn_map():
    """hd2_variables.json → {effect_id: 中文词条名}"""
    def build(v):'''
new_fn = '''def _effect_cn_map_source():
    """hd2_variables.json 原始对象（被动增益分类查找用）"""
    return _site_get("https://jerry114514.github.io/HD2-Galatic_war-Map/tables/hd2_variables.json")


def _effect_cn_map():
    """hd2_variables.json → {effect_id: 中文词条名}"""
    def build(v):'''
assert old_fn in b, "effect fn anchor"
b = b.replace(old_fn, new_fn, 1)

open(p, "w", encoding="utf-8").write(b)
print("dss card rewritten (v2) + planet dedup")

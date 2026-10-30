"""Activity-scoped variant queries with explicit source completeness."""
import asyncio
from datetime import datetime, timezone
import os
import sys
import time
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from astrbot.api.event import filter
from astrbot.api.star import Star
from astrbot_plugin_hd2_common import commands
from astrbot_plugin_hd2_common.search import normalize
from astrbot_plugin_hd2_common.runtime import blocking, singleflight, new_request, request_id
from astrbot_plugin_hd2_common.main import (
    fetch_json as _fetch_json, fetch_official as _fetch_official,
    load_effect_id_cn as _load_effect_id_cn, OFFICIAL_API_URL, LIVE_API_URL, OFFICIAL_WAR_ID, PLANETS_URL,
)
from astrbot_plugin_hd2_monitor.main import hd2_log

VARIANT_CN = commands.VARIANT_TERMS
_scan_cache = {"ts":0,"data":{}}


def _load_planet_cn_map():
    return commands.names()


def _validate_status(status, effects=True):
    if not isinstance(status,dict) or not isinstance(status.get("campaigns"),list):
        raise ValueError("变种源缺少活动战役列表")
    if any(not isinstance(c,dict) or not isinstance(c.get("planetIndex"),int) for c in status["campaigns"]):
        raise ValueError("活动战役星球字段无效")
    if effects:
        if not isinstance(status.get("planetActiveEffects"),list):
            raise ValueError("变种源缺少星球效果列表")
        if any(not isinstance(e,dict) or not isinstance(e.get("index"),int) or not isinstance(e.get("galacticEffectId"),int)
               for e in status["planetActiveEffects"]):
            raise ValueError("星球效果字段无效")
    return status


def _remaining(deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0: raise TimeoutError("变种查询预算已用尽")
    return min(8,remaining)


async def _scan_all_variants(deadline=None):
    deadline=deadline or time.monotonic()+20
    if _scan_cache["data"] and time.monotonic()-_scan_cache["ts"]<300:
        return _scan_cache["data"]
    mapping=await blocking(_load_effect_id_cn)
    if not mapping: raise ValueError("变种效果映射不可用")
    rows={key:[] for key in VARIANT_CN}
    unknown=set(); warnings=[]
    source=OFFICIAL_API_URL
    planets=await blocking(_fetch_json,PLANETS_URL,timeout=_remaining(deadline))
    if not isinstance(planets,list) or any(not isinstance(p,dict) or not isinstance(p.get("index"),int) or not isinstance(p.get("name"),str) for p in planets):
        raise ValueError("星球名称数据不完整")
    names={p["index"]:p["name"] for p in planets}
    try:
        status=_validate_status(await blocking(_fetch_official,f"/api/WarSeason/{OFFICIAL_WAR_ID}/Status",timeout=_remaining(deadline)))
    except (OSError,ValueError,RuntimeError):
        source=LIVE_API_URL
        live=await blocking(_fetch_json,LIVE_API_URL,timeout=_remaining(deadline))
        status=_validate_status(live.get("warStatus") if isinstance(live,dict) else None,effects=False)
    indices={c["planetIndex"] for c in status["campaigns"]}
    if "planetActiveEffects" in status:
        _validate_status(status)
        known={normalize(cn):key for key,cn in VARIANT_CN.items()}
        known.update({normalize(key):key for key in VARIANT_CN})
        for effect in status["planetActiveEffects"]:
            if effect["index"] not in indices: continue
            effect_id=effect["galacticEffectId"]
            label=mapping.get(effect_id)
            if label is None:
                unknown.add(effect_id);continue
            key=known.get(normalize(label))
            if key:
                name=names.get(effect["index"],str(effect["index"]))
                if name not in rows[key]: rows[key].append(name)
    elif indices:
        from playwright.async_api import async_playwright
        sem=asyncio.Semaphore(2)
        source="https://helldiverscompanion.com"
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(headless=True,timeout=_remaining(deadline)*1000)
            try:
                async def check(idx):
                    async with sem:
                        page=None
                        try:
                            _remaining(deadline)
                            if idx not in names: raise ValueError("缺少星球名称")
                            page=await browser.new_page()
                            slug=names[idx].lower().replace(" ","_")
                            await page.goto(source+"/#hellpad/planets/"+slug,timeout=_remaining(deadline)*1000)
                            await page.wait_for_function("() => document.body.innerText.includes('OPERATIONAL PARAMETERS')",timeout=_remaining(deadline)*1000)
                            body=(await page.inner_text("body",timeout=_remaining(deadline)*1000)).upper()
                            if "POINTS OF INTEREST" not in body: raise ValueError("备用页面区块不完整")
                            section=body.split("OPERATIONAL PARAMETERS",1)[1].split("POINTS OF INTEREST",1)[0]
                            for key in rows:
                                if key in section: rows[key].append(names[idx])
                        except Exception as error:
                            warnings.append(f"星球 {idx} 未完成：{type(error).__name__}")
                        finally:
                            if page is not None: await page.close()
                await asyncio.gather(*(check(idx) for idx in indices))
            finally:
                await browser.close()
    complete=not unknown and not warnings
    result={"variants":rows,"source":source,"source_time":status.get("time"),
            "fetched_at":datetime.now(timezone.utc).isoformat(),"complete":complete,
            "unknown_effects":sorted(unknown),"warnings":warnings,"scope":"活动战役"}
    if complete: _scan_cache.update(ts=time.monotonic(),data=result)
    return result


class Hd2VariantQueryPlugin(Star):
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self,event):
        parsed=commands.parse(event.message_str)
        if not parsed or parsed[0]!="variant": return
        query=normalize(parsed[1])
        key=next((k for k,cn in VARIANT_CN.items() if query in (normalize(k),normalize(cn))),None)
        if key is None:
            yield event.plain_result("请使用 /变种 名称，支持："+"、".join(VARIANT_CN.values()))
            event.stop_event();return
        token=new_request(event)
        try:
            deadline=time.monotonic()+20
            scan=await asyncio.wait_for(singleflight(("variants",),lambda:_scan_all_variants(deadline)),20)
            names=_load_planet_cn_map()
            found=scan["variants"][key]
            lines=[names.get(p.upper(),p)+"——"+VARIANT_CN[key] for p in found]
            if not found:
                lines.append("在当前已支持的效果映射内，未发现该变种的活动战役。" if scan["complete"] else "数据覆盖不完整，尚不能判断是否存在该变种的活动战役。")
            if not scan["complete"]: lines.append("部分星球/效果尚未确认；请核对原始数据。")
            lines.extend(["范围：活动战役；支持 "+str(len(VARIANT_CN))+" 类变种", "来源："+scan["source"],"采集时间："+scan["fetched_at"]])
            yield event.plain_result("\n".join(lines))
            hd2_log("generated",plugin="variant",source=scan["source"],source_time=scan["source_time"],
                    detail={"complete":scan["complete"],"unknown_effects":scan["unknown_effects"],"warnings":scan["warnings"]})
        except Exception as error:
            yield event.plain_result("⚠️ 变种数据不可用："+type(error).__name__)
        finally:
            request_id.reset(token)
            event.stop_event()

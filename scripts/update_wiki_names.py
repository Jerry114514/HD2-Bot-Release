"""Build a versioned name-only catalogue from an explicit local site data directory.

Anonymous API verification is explicit maintenance, never triggered by chat queries.
Only a complete successful run atomically replaces the bundled catalogue.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
from astrbot_plugin_hd2_common.data import atomic_json
from astrbot_plugin_hd2_common.search import models, natural, MODEL, normalize

API = "https://helldivers.wiki.gg/zh/api.php"


def build(source):
    records = {}
    provenance = []
    for filename, key in (("weapons.json", "weapons"), ("stratagems_full.json", "stratagems")):
        path = source / filename
        raw = path.read_bytes(); doc = json.loads(raw)
        provenance.append({"file":filename,"sha256":hashlib.sha256(raw).hexdigest(),"updated_at":doc.get("updated_at")})
        for item in doc[key]:
            title = item.get("name", "")
            if not title or "|" in title:
                continue
            records.setdefault(title, []).append(item)
    entries = []
    titles = list(records)
    for offset in range(0, len(titles), 40):
        batch = titles[offset:offset+40]
        params = {"action":"query","format":"json","prop":"info|pageprops|revisions","ppprop":"disambiguation",
                  "rvprop":"ids","titles":"|".join(batch),"redirects":1,"inprop":"url"}
        req = urllib.request.Request(API+"?"+urllib.parse.urlencode(params),headers={"User-Agent":"HD2-Bot/2.0 name-catalogue"})
        with urllib.request.urlopen(req, timeout=15) as response:
            doc = json.load(response)
        if doc.get("error") or not isinstance(doc.get("query",{}).get("pages"),dict):
            raise ValueError("Wiki catalogue verification failed")
        query = doc["query"]
        redirects = {r["from"]:r["to"] for r in query.get("normalized",[]) + query.get("redirects",[])}
        pages = {p["title"]:p for p in query["pages"].values()}
        for title in batch:
            target = title
            for _ in range(len(redirects)+1):
                if target not in redirects: break
                target = redirects[target]
            page = pages.get(target, {})
            valid = page.get("pageid",-1)>0 and "disambiguation" not in page.get("pageprops",{})
            aliases = {title}
            for item in records[title]:
                en = item.get("name_en", "")
                if en:
                    aliases.add(en)
                    if models(en) and models(en) == models(target):
                        aliases.add(natural(en))
            entries.append({"title":page.get("title",title),"pageid":page.get("pageid") if valid else None,
                "verified":bool(valid),"aliases":sorted(aliases),"scope":"HD2",
                "category":records[title][0].get("category", "装备"),
                "source":API,"source_ids":[r.get("id") for r in records[title]],
                "revision":page.get("lastrevid"),"url":page.get("fullurl")})
    # Old Chinese translations may have moved. Resolve only an unambiguous full model,
    # never the first full-text result. English aliases preserve composite AX/ prefixes.
    for entry in entries:
        if entry["verified"]: continue
        aliases = [a for a in entry["aliases"] if a.isascii() and models(a)]
        if not aliases: continue
        query = MODEL.search(normalize(aliases[0])).group()
        params={"action":"query","format":"json","generator":"search","gsrsearch":'"'+query+'"',
                "gsrlimit":20,"gsrnamespace":0,"prop":"info|pageprops","ppprop":"disambiguation","inprop":"url"}
        pages={}; continuation={}
        for _ in range(3):
            req=urllib.request.Request(API+"?"+urllib.parse.urlencode({**params,**continuation}),headers={"User-Agent":"HD2-Bot/2.0 name-catalogue"})
            with urllib.request.urlopen(req,timeout=15) as response: result=json.load(response)
            if result.get("error") or ("query" not in result and "batchcomplete" not in result) or ("query" in result and not isinstance(result["query"].get("pages"),dict)):
                raise ValueError("Catalogue model lookup failed")
            pages.update((result.get("query") or {}).get("pages",{}))
            continuation=result.get("continue") or {}
            if not continuation: break
        if continuation: continue  # incomplete search cannot establish a unique mapping
        matches=[p for p in pages.values() if models(p["title"])==models(query) and "disambiguation" not in p.get("pageprops",{})]
        if len(matches)!=1: continue
        page=matches[0]
        entry.update(title=page["title"],pageid=page["pageid"],verified=True,revision=page.get("lastrevid"),url=page.get("fullurl"))
        entry["aliases"]=sorted(set(entry["aliases"]+[natural(a) for a in aliases]))
    payload = {"schema":1,"verified_at":datetime.now(timezone.utc).isoformat(),"sources":provenance,"entries":entries}
    payload["version"] = hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path)
    parser.add_argument("--output",type=Path,default=ROOT/"plugins/astrbot_plugin_hd2_wiki/names.json")
    args = parser.parse_args()
    result = build(args.source)
    atomic_json(args.output,result)
    print(f"Catalogue: {len(result['entries'])} entries; {sum(r['verified'] for r in result['entries'])} verified")

"""Bounded HTML table extraction preserving headers and source positions."""


def extract(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for edit in soup.select(".mw-editsection"):
        edit.decompose()
    out, section, unsupported = [], "数据", 0
    for el in soup.find_all(["h2", "h3", "table"]):
        if el.name != "table":
            section = el.get_text(" ", strip=True).replace("[编辑]", "").strip() or section
            continue
        if el.find_parent("table") or any(c in " ".join(el.get("class", [])) for c in ("navbox", "tabs")):
            continue
        if len(out) >= 64:
            unsupported += 1
            continue
        rows, headers, origins, occupied = [], [], [], {}
        bad = bool(el.find("table"))
        trs = [r for r in el.find_all("tr") if r.find_parent("table") is el]
        try:
            if len(trs) > 500:
                raise ValueError("oversize table")
            for ri, tr in enumerate(trs):
                ci = 0
                for cell in tr.find_all(["th", "td"], recursive=False):
                    while (ri, ci) in occupied:
                        ci += 1
                    rs = int(cell.get("rowspan", 1)); cs = int(cell.get("colspan", 1))
                    if not 1 <= rs <= len(trs)-ri or not 1 <= cs <= 16 or ci+cs > 16:
                        raise ValueError("unsupported span")
                    value = cell.get_text(" ", strip=True)
                    if not value and cell.find("img"):
                        # Image-only values are not empty fields. Keep readable
                        # alternatives, or explicitly refer readers to the source.
                        value = " / ".join(img.get("alt") or img.get("title") or "图片（见原文）"
                                           for img in cell.find_all("img"))
                    if len(value) > 500:
                        value = value[:500] + "…"; bad = True
                    for rr in range(ri, ri+rs):
                        for cc in range(ci, ci+cs):
                            if (rr, cc) in occupied:
                                raise ValueError("overlapping spans")
                            occupied[rr, cc] = (value, cell.name == "th", [ri, ci])
                    ci += cs
            width = max((c for r, c in occupied), default=-1)+1
            for ri in range(len(trs)):
                row = [occupied.get((ri,c), ("",False,None)) for c in range(width)]
                rows.append([x[0] for x in row]); headers.append([x[1] for x in row]); origins.append([x[2] for x in row])
        except (ValueError, TypeError):
            bad = True
        if bad:
            unsupported += 1
        if not rows:
            continue
        caption = el.find("caption", recursive=False)
        title = caption.get_text(" ", strip=True) if caption else ""
        if not title and "enemy-attack-stats" in el.get("class", []):
            box = el.find_parent(class_="biome-box")
            heading = box.find(class_="MPheading", recursive=False) if box else None
            title = heading.get_text(" ", strip=True) if heading else ""
        kind = "key_value" if "table-weapon-stats" in el.get("class", []) and width == 2 else "grid"
        out.append({"section":section, "title":title, "kind":kind,
                    "rows":rows, "headers":headers, "origins":origins, "position":len(out), "unsupported":bad})
    return out, unsupported

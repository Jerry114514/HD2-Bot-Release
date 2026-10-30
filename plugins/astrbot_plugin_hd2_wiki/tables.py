"""Bounded HTML table extraction preserving headers and source positions."""


def extract(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
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
        out.append({"section":section, "title":caption.get_text(" ",strip=True) if caption else "",
                    "rows":rows, "headers":headers, "origins":origins, "position":len(out), "unsupported":bad})
    return out, unsupported

"""Small, field-aware name matching. Distances are suggestions, never probabilities."""
import re
import unicodedata

RULE_VERSION = "osa-zh-en-1"
MODEL = re.compile(r"(?<![a-z0-9/])(?:[a-z]{1,5}/)?[a-z]{1,5}[- ]?\d+[a-z]*(?![a-z0-9])", re.I)


def normalize(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.translate(str.maketrans({c: "-" for c in "‐‑‒–—−"}))
    return " ".join(text.split())


def models(value):
    return {re.sub(r"[- ]", "", m.group()) for m in MODEL.finditer(normalize(value))}


def natural(value):
    return MODEL.sub("", normalize(value)).strip(' -“”"「」')


def osa_limit(value):
    """Chinese is conservative: <=2 none, 3..7 one, >=8 two. Latin: <4 none, 4..8 one, >=9 two."""
    text = natural(value)
    if re.search(r"[\u3400-\u9fff]", text):
        n = len(re.sub(r"\s", "", text))
        return 0 if n <= 2 else 1 if n < 8 else 2
    n = len(re.sub(r"\s", "", text))
    return 0 if n < 4 else 1 if n < 9 else 2


def match_name(query, title, aliases=(), verified=True):
    """Return (tier, distance, reason), or None. Model conflicts cannot become name hits."""
    q = normalize(query)
    qm = models(q)
    fields = [title, *aliases]
    if qm and qm != models(title):
        return None
    best = None
    for field in fields:
        f = normalize(field)
        if q == f or (qm and not natural(q) and qm == models(f)):
            score = (0 if verified else 1, 0, "精确名称/型号" if verified else "待核实名称")
        else:
            a, b = natural(q), natural(f)
            if not a or not b:
                continue
            # Exact natural names remain candidates unless explicitly registered aliases.
            if a == b or (len(a) >= 2 and re.search(r"[\u3400-\u9fff]", a) and a in b) or (
                len(a) >= 3 and not re.search(r"[\u3400-\u9fff]", a) and any(w.startswith(a) for w in b.split())
            ):
                score = (1, 0, "名称片段")
            else:
                limit = osa_limit(a)
                if not limit or abs(len(a)-len(b)) > limit:
                    continue
                try:
                    from rapidfuzz.distance import OSA
                except ImportError:
                    continue
                distance = OSA.distance(a, b, score_cutoff=limit)
                if distance > limit or distance / max(len(a), len(b)) > .34:
                    continue
                score = (2, distance, "近似名称")
        best = min(best, score) if best is not None else score
    return best


def planet_key(value):
    text = normalize(value)
    return next(iter(models(text))) if MODEL.fullmatch(text) else text

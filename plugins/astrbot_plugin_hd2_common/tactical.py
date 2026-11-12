"""Planet tactical objectives using the same topology as the main site."""
from functools import lru_cache
import json

from . import sources


OWNER_CN = {
    "Humans": "超级地球",
    "Terminids": "终结族",
    "Terminid": "终结族",
    "Automaton": "机器人",
    "Automatons": "机器人",
    "Illuminate": "光能者",
}


@lru_cache(maxsize=1)
def _sector_map():
    return sources.load_starmap_sectors()


def load_waypoints():
    """Load the frozen supply-line table from the repository."""
    for path in sources.table_candidates("waypoints.json"):
        try:
            with open(path, encoding="utf-8") as stream:
                value = json.load(stream)
            if isinstance(value, dict):
                return value.get("waypoints") or value
        except (OSError, ValueError, TypeError):
            continue
    return {}


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _planet_name(planet, index):
    raw = str((planet or {}).get("name") or "")
    return sources.PLANET_NAME_CN.get(raw.upper()) or raw or f"星球 #{index}"


def _owner_is_humans(planet):
    return (planet or {}).get("currentOwner") in ("Humans", 1, "1")


def _campaign_type(campaign):
    return str((campaign or {}).get("campaignType") or "").strip().lower()


def compute_for(doc, planet_index, waypoints=None):
    """Return tactical objectives for one planet, matching ``js/tactical.js``.

    The result contains the display name, explanation and target-planet links for
    each objective. Missing topology or campaign data produces no invented tags.
    """
    index = _number(planet_index)
    if index is None:
        return []
    planets = {
        _number(row.get("index")): dict(row)
        for row in doc.get("planets") or []
        if isinstance(row, dict) and _number(row.get("index")) is not None
    }
    self_planet = planets.get(index)
    if not self_planet:
        return []

    sectors = _sector_map()
    for row in planets.values():
        name = str(row.get("name") or "").upper()
        row["sector"] = sectors.get(name) or row.get("sector") or ""

    wp = waypoints if waypoints is not None else load_waypoints()
    if isinstance(wp, dict) and isinstance(wp.get("waypoints"), dict):
        wp = wp["waypoints"]
    if not isinstance(wp, dict):
        return []
    neighbors = [_number(value) for value in wp.get(str(index), [])]
    neighbors = [value for value in neighbors if value is not None]
    if not neighbors:
        return []

    campaigns = [row for row in doc.get("campaigns") or [] if isinstance(row, dict)]
    defense_planets = {
        _number((campaign.get("planet") or {}).get("index", campaign.get("planetIndex")))
        for campaign in campaigns
        if _campaign_type(campaign) == "defense"
    }
    defense_planets.discard(None)
    liberation_planets = {
        _number((campaign.get("planet") or {}).get("index", campaign.get("planetIndex")))
        for campaign in campaigns
        if _campaign_type(campaign) == "liberation"
    }
    liberation_planets.discard(None)
    if index not in liberation_planets:
        return []

    reverse = {}
    for source, targets in wp.items():
        source_index = _number(source)
        if source_index is None:
            continue
        for target in targets or ():
            target_index = _number(target)
            if target_index is None:
                continue
            reverse.setdefault(target_index, []).append(source_index)

    def all_ways(value):
        out = []
        for target in [_number(item) for item in wp.get(str(value), [])] + reverse.get(value, []):
            if target is not None and target not in out:
                out.append(target)
        return out

    def ours(value):
        return _owner_is_humans(planets.get(value))

    def enemy_neighbors(value):
        return [
            target for target in all_ways(value)
            if not ours(target) or target in defense_planets
        ]

    def target_links(values):
        from .data import site_url

        links = []
        for value in values:
            name = _planet_name(planets.get(value), value)
            links.append({
                "index": value,
                "name": name,
                "url": site_url(f"planet.html?idx={value}"),
            })
        return links

    self_name = _planet_name(self_planet, index)
    owner = OWNER_CN.get(self_planet.get("currentOwner"), self_planet.get("currentOwner") or "敌方")
    tags = []

    if all_ways(index) and all(ours(value) for value in all_ways(index)):
        tags.append({
            "key": "besieged",
            "title": "被围攻",
            "text": f"{owner}的部队在{self_name}已被包围，无法再阻挡解放进度。",
            "target_links": [],
        })

    outbound = [_number(value) for value in wp.get(str(index), [])]
    circ_targets = [
        value for value in outbound
        if value is not None
        and (not ours(value) or value in defense_planets)
        and enemy_neighbors(value) == [index]
    ]
    if circ_targets:
        names = "、".join(_planet_name(planets.get(value), value) for value in circ_targets)
        races = []
        for value in circ_targets:
            race = OWNER_CN.get((planets.get(value) or {}).get("currentOwner"), "")
            if race and race not in races:
                races.append(race)
        race = "/".join(races)
        if race == "超级地球":
            race = "攻城敌军"
        title = "双重围攻" if len(circ_targets) == 2 else (
            "多重围攻" if len(circ_targets) > 2 else "天体包围圈"
        )
        tags.append({
            "key": "circumvallation",
            "title": title,
            "targets": list(circ_targets),
            "target_links": target_links(circ_targets),
            "text": (
                f"解放{self_name}将对{names}的{race}形成包围，切断其支援与增援。"
                f"固守该星可开启{len(circ_targets)}处围攻解放，降低当地敌方抵抗度。"
            ),
        })

    gambits = [
        value for value in all_ways(index)
        if ours(value) and value in defense_planets
        and enemy_neighbors(value) == [index]
    ]
    if gambits:
        names = "、".join(_planet_name(planets.get(value), value) for value in gambits)
        tags.append({
            "key": "gambit",
            "title": "对弈点",
            "targets": list(gambits),
            "target_links": target_links(gambits),
            "text": f"解放{self_name}将立即结束{names}上的敌方战役，守军无需再坚守防线。",
        })

    sector = self_planet.get("sector") or ""
    if sector:
        siblings = [
            row for value, row in planets.items()
            if value != index and (row.get("sector") or "") == sector
        ]
        if siblings and all(_owner_is_humans(row) for row in siblings):
            tags.append({
                "key": "libsector",
                "title": "星域解放",
                "target_links": [],
                "text": (
                    f"解放{self_name}将使超级地球完全掌控{sector}星区，"
                    f"并在大片星图上清除{owner}的视觉标识。"
                ),
            })
    return tags

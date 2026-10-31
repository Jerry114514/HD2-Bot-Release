"""Framework entrypoint; reusable source helpers live outside plugin registration."""
from .sources import (
    OFFICIAL_API_URL,
    OFFICIAL_WAR_ID,
    LIVE_API_URL,
    ASSIGNMENTS_URL,
    EXTENDED_API_URL,
    PLANETS_URL,
    TRANSLATE_URL,
    fetch_json,
    fetch_official,
    table_candidates,
    load_effect_id_cn,
    load_starmap_sectors,
    load_planet_map,
    PLANET_NAME_CN,
    SECTOR_CN,
)
from astrbot.api.star import Star


class Hd2CommonPlugin(Star):
    pass

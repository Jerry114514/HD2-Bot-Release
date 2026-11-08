"""Actual numbered selection -> extraction -> Pillow -> SDK reply, without network."""
from pathlib import Path
from unittest.mock import patch


async def audit_wiki_tables(modules, scheduler, Event, sandbox):
    from astrbot.api.message_components import Image, Plain
    from astrbot_plugin_hd2_common.choices import choices, owner
    from astrbot_plugin_hd2_wiki import wiki_data, wiki_card

    html = '''<h2>武器配置</h2><div class="biome-box"><div class="MPheading">抓挠</div>
      <table class="enemy-attack-stats"><tr><th>属性</th><th colspan="2">数值</th></tr>
      <tr><th colspan="3">空分类</th></tr><tr><th>空字段</th><td colspan="2"></td></tr>
      <tr><th colspan="3">近战伤害</th></tr><tr><th>标准伤害</th><td colspan="2">55</td></tr>
      <tr><th>击退力</th><td colspan="2">0</td></tr></table></div>'''
    page = {"pageid": 123, "title": "测试怪物", "revisions": [{"revid": 1}],
            "extract": "离线流程样本", "fullurl": "https://example.invalid/wiki/test"}
    candidates = {"status": "ambiguous", "title": "怪物", "text": "", "rev": "none",
                  "url": page["fullurl"], "search_complete": True,
                  "candidates": [{"title": page["title"], "pageid": 123, "reason": "测试"}]}
    selected_ids = []

    def lookup(keyword, **options):
        if options.get("selected") is None:
            return candidates
        selected_ids.append(options["selected"]["pageid"])
        return wiki_data.wiki_lookup(keyword, **options)

    count = 0
    original_text = wiki_card.ImageDraw.ImageDraw.text
    with patch.object(modules["wiki"], "wiki_lookup", side_effect=lookup), \
         patch.object(wiki_data, "catalogue", return_value={"version": "test", "entries": []}), \
         patch.object(wiki_data, "_page_query", return_value={"query": {"pages": {"123": page}}}) as queried, \
         patch.object(wiki_data, "_html", return_value=html):
        for platform in ("qq_official", "aiocqhttp"):
            for private in (False, True):
                choices.clear()
                menu = Event("/wiki 怪物", platform, True, private)
                menu.message_obj.message_id = f"menu-{count}"
                await scheduler.execute(menu)
                count += 1
                assert choices.menus[owner(menu)].state == "ready"
                assert any(isinstance(c, Plain) and "1. 测试怪物" in c.text for chain in menu.sent for c in chain)
                labels = []

                def record(draw, xy, label, *args, **kwargs):
                    labels.append(str(label))
                    return original_text(draw, xy, label, *args, **kwargs)

                target = sandbox / f"wiki-tables-{platform}-{private}"
                with patch.object(wiki_card, "CACHE_DIR", str(target)), \
                     patch.object(wiki_card.ImageDraw.ImageDraw, "text", record), \
                     patch.object(wiki_card, "_render_long_cached", wraps=wiki_card._render_long_cached) as renderer:
                    selected = Event("1", platform, True, private)
                    selected.message_obj.message_id = f"selected-{count}"
                    await scheduler.execute(selected)
                count += 1
                assert selected_ids[-1] == 123
                assert queried.call_args.args == ("测试怪物", 123)
                assert len(selected.sent) == 1 and any(isinstance(c, Image) for c in selected.sent[0])
                assert selected._has_send_oper and selected.is_stopped() and not choices.menus
                # A valid cached image also counts on a repeat run of this script.
                assert renderer.call_count == 1
                path = Path(renderer.call_args.args[0])
                assert path.is_file()
                with wiki_card.Image.open(path) as rendered:
                    rendered.verify()
                if labels:
                    assert "武器配置 · 抓挠" in labels and "55" in labels and "0" in labels
                    assert "空分类" not in labels and "空字段" not in labels
    return count

"""Table display regressions, including the enemy-attack template's empty fields."""
import copy
import tempfile
import unittest
from unittest.mock import patch

from framework import install
install()
from astrbot_plugin_hd2_wiki import tables, wiki_card
from astrbot_plugin_hd2_wiki.table_display import compact, display_cells


def attack(title, damage):
    # Reduced reproduction of 阿尔法指挥官, revision 15555 (2026-11-08).
    # Hidden template rows remain in the extracted source; display omits only
    # empty data, not arbitrary CSS-hidden nonempty cells.
    return f'''<div class="biome-box"><div class="MPheading">{title}</div>
    <table class="enemy-attack-stats">
      <tr><th>属性</th><th colspan="2">数值</th></tr>
      <tr style="display:none"><th colspan="3">射弹伤害</th></tr>
      <tr style="display:none"><th>标准伤害</th><td colspan="2"></td></tr>
      <tr><th colspan="3">近战伤害</th></tr>
      <tr><th>标准伤害</th><td colspan="2">{damage}</td></tr>
      <tr><th>击退力</th><td colspan="2">0</td></tr>
      <tr><th>元素</th><td colspan="2"> </td></tr>
      <tr><th colspan="3">施加状态</th></tr>
      <tr><th colspan="3">状态效果伤害</th></tr>
      <tr><th>标准伤害</th><td colspan="2"></td></tr>
    </table></div>'''


class WikiTableTests(unittest.TestCase):
    def test_enemy_sections_values_names_and_source_are_preserved(self):
        source, bad = tables.extract('<h2>武器配置<span class="mw-editsection">编辑</span></h2>'
                                    + attack('抓挠', '55') + attack('野蛮冲撞', '70'))
        before = copy.deepcopy(source)
        self.assertEqual(bad, 0)
        views = [compact(t) for t in source]
        self.assertEqual([t['title'] for t in views], ['抓挠', '野蛮冲撞'])
        self.assertEqual(views[0]['section'], '武器配置')
        self.assertEqual(views[0]['rows'], [
            ['属性', '数值'], ['近战伤害', '近战伤害'], ['标准伤害', '55'], ['击退力', '0']])
        self.assertEqual(views[1]['rows'][2], ['标准伤害', '70'])
        self.assertEqual(display_cells(views[0], 1), ['近战伤害', ''])
        self.assertEqual(source, before)

    def test_explicit_values_are_not_mistaken_for_missing(self):
        values = ['0', '0%', '0 (震荡)', '无', '否', '未知', 'None', 'No', 'N/A', '-', '—']
        body = ''.join(f'<tr><th>值{i}</th><td>{v}</td></tr>' for i, v in enumerate(values))
        body += '<tr><th>未填</th><td>&nbsp;\u200b</td></tr>'
        source, _ = tables.extract('<table><tr><th>属性</th><th>值</th></tr>' + body + '</table>')
        view = compact(source[0])
        self.assertEqual([r[1] for r in view['rows'][1:]], values)

    def test_empty_table_and_orphan_groups_are_omitted(self):
        source, _ = tables.extract('''<table>
          <tr><th>属性</th><th>值</th></tr><tr><th colspan="2">空分类</th></tr>
          <tr><th>伤害</th><td> </td></tr></table>''')
        self.assertIsNone(compact(source[0]))
        self.assertEqual(len(source[0]['rows']), 3)
        blank, _ = tables.extract('<table><tr><th> </th><th>&nbsp;</th></tr></table>')
        self.assertIsNone(compact(blank[0]))

    def test_multi_level_headers_and_rowspans_keep_value_ownership(self):
        source, _ = tables.extract('''<table>
          <tr><th rowspan="2">部位</th><th colspan="2">伤害</th><th rowspan="2">空列</th></tr>
          <tr><th>标准</th><th>耐久</th></tr>
          <tr><th rowspan="2">头部</th><td>55</td><td>55</td><td></td></tr>
          <tr><td>70</td><td>65</td><td></td></tr></table>''')
        view = compact(source[0])
        self.assertEqual(view['rows'], [
            ['部位', '伤害', '伤害'], ['部位', '标准', '耐久'], ['头部', '55', '55'], ['头部', '70', '65']])
        self.assertEqual(display_cells(view, 2), ['头部', '55', '55'])
        self.assertEqual(view['origins'][3][0], [2, 0])

    def test_header_hierarchy_retains_table_title_but_not_empty_section(self):
        source, _ = tables.extract('''<table>
          <tr><th colspan="2">整表名称</th></tr><tr><th colspan="2">空分类</th></tr>
          <tr><th>空字段</th><td></td></tr><tr><th colspan="2">有效分类</th></tr>
          <tr><th>伤害</th><td>1</td></tr><tr><th colspan="2">末尾空分类</th></tr></table>''')
        self.assertEqual([r[0] for r in compact(source[0])['rows']], ['整表名称', '有效分类', '伤害'])

    def test_image_only_values_and_known_td_field_labels(self):
        source, _ = tables.extract('''<table class="table-weapon-stats">
          <tr><th colspan="2">武器</th></tr><tr><td>空字段</td><td></td></tr>
          <tr><td>图标</td><td><img alt="中甲" src="x"></td></tr>
          <tr><td>示意图</td><td><img src="x"></td></tr></table>''')
        self.assertEqual(compact(source[0])['rows'], [
            ['武器', '武器'], ['图标', '中甲'], ['示意图', '图片（见原文）']])

    def test_unknown_layouts_are_not_guessed_and_hidden_real_data_survives(self):
        source, _ = tables.extract('''<table><tr style="display:none"><td>0</td><td></td></tr>
          <tr><td>未知布局中的文字</td><td></td></tr></table>''')
        self.assertEqual(compact(source[0])['rows'], [['0'], ['未知布局中的文字']])
        for overrides in ({'unsupported': True}, {'headers': []}):
            value = {**source[0], **overrides}
            self.assertEqual(compact(value), value)

    def test_padded_headers_and_all_th_data_are_not_lost(self):
        source, _ = tables.extract('''<table><tr><th>部位</th><th>生命值</th></tr>
          <tr><td>头部</td><td>250</td><td>额外说明</td></tr></table>
          <table><tr><th>伤害</th><th>100</th></tr></table>''')
        self.assertEqual(compact(source[0])['rows'], source[0]['rows'])
        self.assertEqual(compact(source[1]), source[1])

    def test_render_is_compact_but_keeps_distinct_attacks_and_cache_inputs(self):
        source, _ = tables.extract('<h2>武器配置</h2>' + attack('抓挠', '55') + attack('野蛮冲撞', '70'))
        value = {'status': 'resolved', 'title': '测试怪物', 'url': 'https://example.test',
                 'rev': '15555', 'text': '测试摘要', 'table_status': 'available', 'tables': source}
        labels = []
        original = wiki_card.ImageDraw.ImageDraw.text

        def record(draw, xy, label, *args, **kwargs):
            labels.append(str(label))
            return original(draw, xy, label, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temp, patch.object(wiki_card.ImageDraw.ImageDraw, 'text', record):
            first = wiki_card.render_wiki_card('测试怪物', temp, value)
            with wiki_card.Image.open(first) as img:
                self.assertEqual(img.width, wiki_card.W)
                self.assertLess(img.height, 1100)
            self.assertIn('武器配置 · 抓挠', labels)
            self.assertIn('武器配置 · 野蛮冲撞', labels)
            self.assertIn('55', labels)
            self.assertIn('70', labels)
            self.assertEqual(labels.count('近战伤害'), 2)
            self.assertNotIn('射弹伤害', labels)
            self.assertTrue(any('已收起空字段' in s for s in labels))
            value['tables'][0]['title'] = '新攻击名'
            second = wiki_card.render_wiki_card('测试怪物', temp, value)
            self.assertNotEqual(first, second)


if __name__ == '__main__':
    unittest.main()

// 从原始迁移档案（equipment-expanded.json）再生拆洞候选集：只做结构提取与来源绑定，
// 不猜任何缺失数值。输出确定性 JSON（无运行时间戳），逐字段可回查到 页面/revid/表ID/原文。
// 候选等级永远是 candidate：档案抓取成功不等于当前补丁核验（contract 事实分层）。
import {HOLE_ROUTES, EQUIPMENT_ARCHIVE} from './sources.mjs';

const ECONOMY_KEYS = Object.freeze([
  'capacity', 'max_rounds', 'starting_rounds', 'starting_magazines', 'spare_rounds', 'spare_magazines', 'spare_shells',
  'mags_from_supply', 'mags_from_ammo_box', 'fuse_time', 'warmup', 'uses', 'total_uses', 'cooldown', 'call_in_time',
]);
const ATTACK_DAMAGE_KEYS = Object.freeze(['damage_element', 'damage_inner_radius', 'inner_durable', 'damage_outer_radius', 'outer_durable']);
const ATTACK_PEN_KEYS = Object.freeze(['pen_inner', 'pen_outer', 'pen_shockwave']);
const normalize = s => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');

const rowValue = row => (row && row.parsed ? {raw: row.raw, number: row.parsed.number ?? null, unit: row.parsed.unit ?? null} : null);

function provenance(item) {
  const p = item.provenance || {};
  return EQUIPMENT_ARCHIVE.provenance_fields.reduce((out, k) => ({...out, [k]: p[k] ?? null}), {});
}

// 攻击表如何被触发：weapon_stats 表 Attacks 节的行以 label 指向攻击表标题（remote_targets 同序）。
function triggerChains(item) {
  const chains = [];
  for (const t of item.tables || []) {
    if (t.kind !== 'weapon_stats') continue;
    const attackRows = (t.rows || []).filter(r => r.section === 'Attacks');
    (t.remote_targets || []).forEach((target, i) => {
      const row = attackRows[i] || attackRows.find(r => normalize(r.label) === normalize(target)) || null;
      chains.push({stats_table_index: t.index, attack_table_id: target, stats_row: row ? {label: row.label, key: row.key, raw: row.raw} : null});
    });
  }
  return chains;
}

const deliveryClass = item => {
  if (item.kind === 'weapon') return item.category === 'throwables' ? 'thrown' : 'direct_fire';
  return {stratagem: 'stratagem_delivery', deployable: 'deployed_device', backpack: 'backpack_device', vehicle: 'vehicle'}[item.category] || 'unknown';
};

// 路线分类：数量可解析才比较；status 表（燃烧/毒气等）不参与；要求爆炸类型的路线对
// 非爆炸组件是“不适用”而不是未知；force 无法解析（如 '30-40[1]'）才记 unknown。
function classifyRoutes(attackTypeId, forceNumber) {
  const routes = {};
  for (const [route, rule] of Object.entries(HOLE_ROUTES)) {
    if (attackTypeId === 'status') { routes[route] = {status: 'not_applicable', reason: 'status_component'}; continue; }
    if (rule.requires_explosive_type && attackTypeId !== 'explosion') { routes[route] = {status: 'not_applicable', reason: 'route requires explosive damage type; component attack_type=' + attackTypeId}; continue; }
    if (forceNumber == null) { routes[route] = {status: 'unknown', reason: 'unparsed_force'}; continue; }
    routes[route] = forceNumber >= rule.min_force
      ? {status: 'qualifies'}
      : {status: 'below', reason: 'force ' + forceNumber + ' < ' + rule.min_force};
  }
  return routes;
}

function economyRows(item) {
  const out = {};
  const push = (scope, row) => { if (!row || !ECONOMY_KEYS.includes(row.key)) return; const v = rowValue(row); if (!v) return; const slot = out[row.key] || (out[row.key] = []); slot.push({scope, locator: scope + '[' + (row.label || row.key) + ']', ...v}); };
  for (const r of item.infobox || []) push('infobox.' + r.section, r);
  for (const t of item.tables || []) if (t.kind === 'weapon_stats') for (const r of t.rows || []) push('weapon_stats[' + t.index + ']', r);
  for (const [k, v] of Object.entries(out)) out[k] = v.length === 1 ? v[0] : v;
  return out;
}

export function extractDemolitionCandidates(rawArchive) {
  const items = rawArchive && Array.isArray(rawArchive.items) ? rawArchive.items : null;
  if (!items) return {status: 'INVALID_INPUT', reason: 'archive .items array required'};
  const equipment = [];
  for (const item of items) {
    const chains = triggerChains(item);
    const attacks = [];
    for (const t of item.tables || []) {
      const rows = t.rows || [];
      const demo = rows.find(r => r.key === EQUIPMENT_ARCHIVE.row_key);
      const hasAttackShape = t.attack_type || rows.some(r => [...ATTACK_DAMAGE_KEYS, ...ATTACK_PEN_KEYS].includes(r.key));
      if (!demo && !hasAttackShape) continue;
      const id = t.id || t.title || 'table_' + t.index;
      const backblast = /BACKBLAST/i.test(id + ' ' + (t.title || ''));
      const attackTypeId = t.attack_type || 'unknown';
      const force = demo ? rowValue(demo) : null;
      attacks.push({
        component_id: id,
        attack_type: attackTypeId,
        mode: t.type_label || null,
        heading: t.context_heading || null,
        backblast,
        demolition_force: force,
        rows_locators: {
          demolition_force: demo ? 'tables[' + t.index + '].rows[' + EQUIPMENT_ARCHIVE.row_key + ']' : null,
          damage: Object.fromEntries(ATTACK_DAMAGE_KEYS.map(k => [k, rows.find(r => r.key === k) ? rowValue(rows.find(r => r.key === k)) : null])),
          penetration: Object.fromEntries(ATTACK_PEN_KEYS.map(k => [k, rows.find(r => r.key === k) ? rows.find(r => r.key === k).raw : null])),
        },
        routes: backblast ? Object.fromEntries(Object.keys(HOLE_ROUTES).map(r => [r, {status: 'excluded', reason: 'backblast_component'}])) : classifyRoutes(attackTypeId, force ? force.number : null),
      });
    }
    equipment.push({
      id: item.id,
      name_en: item.name_en ?? null,
      name_zh: item.name_zh ?? null,
      kind: item.kind,
      category: item.category,
      delivery_class: deliveryClass(item),
      provenance: provenance(item),
      economy: economyRows(item),
      attacks,
      trigger_chains: chains,
    });
  }
  // 汇总不筛正例：全量保留（含 below/unknown/excluded），统计只是观察面。
  const routeStats = {};
  for (const e of equipment) for (const a of e.attacks) for (const [r, v] of Object.entries(a.routes || {})) {
    routeStats[r] = routeStats[r] || {qualifies: 0, below: 0, unknown: 0, excluded: 0, not_applicable: 0};
    routeStats[r][v.status] = (routeStats[r][v.status] || 0) + 1;
  }
  return {schema: 1, generated_by: 'shared/hd2-loadout/demolition/extract.mjs', archive_file: EQUIPMENT_ARCHIVE.file, equipment, route_stats: routeStats};
}

// 便捷查询：一件装备在某路线上的可用攻击组件（candidate 级，含条件与排除项）。
export function routeCandidates(candidates, equipmentId, route) {
  const e = (candidates.equipment || []).find(x => x.id === equipmentId);
  if (!e) return {status: 'UNKNOWN_EQUIPMENT', equipment_id: equipmentId};
  return {
    status: 'OK', equipment_id: equipmentId, delivery_class: e.delivery_class, provenance: e.provenance,
    attacks: (e.attacks || []).map(a => ({component_id: a.component_id, attack_type: a.attack_type, mode: a.mode, backblast: a.backblast, demolition_force: a.demolition_force, route: a.routes ? a.routes[route] : undefined})),
  };
}

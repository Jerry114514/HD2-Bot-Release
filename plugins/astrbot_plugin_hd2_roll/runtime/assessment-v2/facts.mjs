// Source-only adapter. Never reads retired dimensions, ratings or anchor scores.
import {projectileMechanics} from '../calibration/practical/attack-mode-core.mjs';
import {roundReloadProfile} from '../calibration/practical/round-reload.mjs';
import {extractDemolitionCandidates} from '../demolition/extract.mjs';

export const number = raw => {
  const m = /^\s*([\d,]+(?:\.\d+)?)(?=\s|$|[a-z%°])/i.exec(String(raw ?? ''));
  return m ? Number(m[1].replaceAll(',', '')) : null;
};
export const field = (rows, key) => rows?.find(r => r.key === key)?.raw ?? null;
export const info = (item, key) => field(item.infobox, key);
export const tableRows = (item, kind) => item.tables.filter(t => t.kind === kind).flatMap(t => t.rows);
export const stat = (item, key) => field(tableRows(item, 'weapon_stats'), key) ?? info(item, key);
const integer = n => Number.isSafeInteger(n) && n >= 0;
const ROUND_RELOAD_IDS = new Set(['sg_8_punisher','sg_8s_slugger','sg_20_halt','sg_97_sweeper','sg_451_cookout','m90a_shotgun','r_6_deadeye','r_2124_constitution']);
const milliseconds = raw => {
  const match = /(\d+(?:\.\d+)?)\s*s(?:ec(?:onds)?)?\s*\(Upgraded\)/i.exec(String(raw ?? ''));
  const seconds = match ? Number(match[1]) : number(raw);
  return Number.isFinite(seconds) ? Math.round(seconds * 1000) : null;
};
const rates = text => [...String(text).matchAll(/([\d,]+(?:\.\d+)?)\s*rpm/gi)].map(m => Number(m[1].replaceAll(',', '')));
// Repeated Strength keys belong to the immediately preceding Status row.
export function statusApplications(rows,effects) {
  const result=[];let current=null;
  for(const row of rows??[]) {
    if(['status','second_status','third_status','aoe_effect_status'].includes(row.key)) {
      current={id:row.raw.replaceAll(' ','_'),strength:null};result.push(current);
    } else if(current&&/status_strength$/.test(row.key))current.strength=number(row.raw);
  }
  return result.map(a=>({...a,effect:effects.find(e=>e.id?.replaceAll(' ','_')===a.id)??null}));
}

// A reference to a shared Wiki status template may omit its expanded table.
// Link it only when every saved definition of that exact name agrees.
export function sharedStatusDefinitions(items,tiers) {
  const groups=new Map();
  for(const item of items)for(const t of item.tables.filter(t=>t.kind==='attack_status')) {
    const id=t.id.replaceAll(' ','_'),effect={id,duration_s:number(field(t.rows,'status_duration')),
      element:field(t.rows,'element'),normal:number(field(t.rows,'damage_standard')),
      durable:number(field(t.rows,'damage_durable')),pen:tiers[field(t.rows,'pen_direct')]??null};
    const entries=groups.get(id)??[];entries.push({effect,source_ref:`wiki:${item.id}@${item.provenance.revid}`});groups.set(id,entries);
  }
  return Object.fromEntries([...groups].filter(([,xs])=>new Set(xs.map(x=>JSON.stringify(x.effect))).size===1)
    .map(([id,xs])=>[id,{...xs[0].effect,linked_definition_refs:xs.map(x=>x.source_ref),basis:'EXPLICIT_NAMED_SHARED_STATUS_TEMPLATE'}]));
}

export function sourceInventory(item) {
  const rows = tableRows(item, 'weapon_stats');
  const heat = rows.some(r => r.key === 'overheats_at');
  const explicitRounds = number(info(item, 'spare_rounds'));
  const roundRow = rows.find(r => r.key === 'spare_rounds' && !/underbarrel/i.test(r.section ?? ''));
  const reserveRounds = /Backpack/i.test(info(item, 'spare_rounds') ?? '') ? explicitRounds : number(roundRow?.raw) ?? explicitRounds;
  const capacity = (item.category === 'throwables' ? number(info(item, 'capacity')) : null)
    ?? number(stat(item, 'capacity')) ?? number(stat(item, 'max_rounds'));
  const observations = [number(field(rows, 'spare_magazines')), number(info(item, 'spare_mags'))].filter(Number.isFinite);
  const conflict = new Set(observations).size > 1;
  const spares = observations.length ? Math.min(...observations) : null;
  if (heat) return {kind: 'heat', feed: 'heat_sink', capacity: null, reserve: null, total: null,
    reserve_rounds: null, spare_magazines: spares, spare_heat_sinks: spares,
    continuous_fire_seconds: /s(?:econds)?\b/i.test(String(stat(item, 'capacity'))) ? capacity : null,
    source_conflict: conflict ? {spare_heat_sink_observations: observations, selected_lower_bound: spares} : null};
  const grenade = item.category === 'throwables';
  const belt = ['heavy_support_gl_28_belt_fed_grenade_launcher','heavy_support_m_1000_hmg','heavy_support_b_flam_80_flamer'].includes(item.id);
  const disposable = /\bExpendable\b/.test(info(item, 'traits') ?? '');
  const reserve = grenade || belt || disposable ? 0 : reserveRounds ?? (spares !== null && capacity !== null ? spares * capacity : null);
  const total = capacity !== null && reserve !== null ? capacity + reserve : null;
  return {kind: heat ? 'heat' : /∞|Unlimited/i.test(String(stat(item, 'capacity'))) ? 'unlimited' : 'finite',
    feed: grenade ? 'loose' : belt ? 'belt' : 'magazine', capacity, reserve, total, disposable,
    reserve_rounds: reserveRounds, spare_magazines: spares,
    source_conflict: conflict ? {spare_magazine_observations: observations, selected_lower_bound: spares} : null};
}

export function sourceRefill(item, inventory) {
  const observations = [number(info(item, 'mags_from_supply')), number(field(tableRows(item, 'weapon_stats'), 'mags_from_supply'))].filter(Number.isFinite);
  const cap = inventory.kind === 'heat' ? inventory.spare_magazines : inventory.feed !== 'magazine' ? inventory.capacity : inventory.reserve;
  if (!integer(cap)) return {public: null, portable: null, reason: 'INVENTORY_UNRESOLVED'};
  // Capacity of 0 is meaningful for a non-refillable one-use weapon.
  if (cap === 0 && inventory.feed !== 'loose') return {public: 0, portable: 0};
  if (!observations.length) return {public: null, portable: null, reason: 'SUPPLY_AMOUNT_MISSING'};
  const inRounds = inventory.kind === 'heat' || inventory.feed !== 'magazine' || inventory.reserve_rounds !== null;
  const candidates = observations.map(n => Math.min(cap, n * (inRounds ? 1 : inventory.capacity)));
  const portable = Math.min(...candidates);
  // Global all-modules scenario; the MG43 module row explicitly says all Support Weapons.
  const upgrade = item.category === 'support';
  return {public: upgrade ? cap : portable, portable, unit: inventory.kind === 'heat' ? 'heat_sinks' : 'rounds',
    observed_rounds: [...new Set(candidates)], public_full_upgrade: upgrade,
    basis: new Set(candidates).size > 1 ? 'SOURCE_LOWER_BOUND' : 'SOURCE_READING'};
}

export function sourcePanel(enemies, heavy, armorDoc, tiers, id, part = 'head') {
  const e = enemies.enemies.find(t => t.id === id);
  const p = heavy.targets?.[id]?.parts?.[part] ?? e?.body_parts?.find(p => p.part_id === part);
  if (!p) return null;
  let hp = typeof p.health === 'number' ? p.health : null;
  if (hp === null) {
    const match = /难度\s*(\d+)\s*以上为\s*(\d+)/.exec(String(p.health));
    if (match && Number(match[1]) <= 10) hp = Number(match[2]);
  }
  const armor = tiers[p.armor_level] ?? armorDoc.per_enemy?.[id]?.parts?.[part]?.at_calibration_difficulty?.armor_ordinal;
  const durable = /^\d+(?:\.\d+)?%$/.test(p.durable) ? Number(p.durable.slice(0, -1)) / 100 : null;
  const bleed = /^([\d,]+)\s*\[[-−]?([\d.]+)\/s\]$/.exec(p.constitution ?? '');
  const constitution = p.constitution === 'None' ? {hp: 0, decay_per_second: 0}
    : bleed ? {hp: Number(bleed[1].replaceAll(',', '')), decay_per_second: Number(bleed[2])} : null;
  return [hp, armor, durable].every(Number.isFinite) ? {target_id: id, part_id: part, hp, armor, durable, fatal: p.fatal === true,
    constitution_raw: p.constitution ?? null,
    constitution, transfer_to_main: part === 'main' ? 1 : number(p.percent_to_main) !== null ? number(p.percent_to_main) / 100 : null,
    transfer_cap: part === 'main' ? false : p.overflow_cap === 'Yes' ? true : p.overflow_cap === 'No' ? false : null,
    stagger_threshold: number(e?.stagger_threshold),fire_multiplier:number(e?.fire_damage_multiplier),
    residual_threat_ms: p.constitution === 'None' ? 0 : null,
    threat_basis: p.constitution === 'None' ? 'SOURCE_PANEL_NO_CONSTITUTION' : 'CONSTITUTION_EVENT_ORDER_UNRESOLVED'} : null;
}

export function weaponFacts(item, tiers, supplements = {}, sharedEffects = {}) {
  const supplyOnly = item.id === 'vehicle_m_103_supply_fast_recon_vehicle';
  const noncombat = supplyOnly || item.id === 'backpack_b_1_supply_pack';
  const inventory = sourceInventory(item);
  const trait = info(item, 'traits') ?? '';
  const handling = Object.fromEntries(['ergonomics', 'recoil_horizontal', 'recoil_vertical'].map(key => [key, number(stat(item, key))]));
  // Main-weapon exact keys only: never borrow an underbarrel or vehicle gun row.
  const handlingSource = Object.fromEntries(['ergonomics', 'recoil', 'recoil_horizontal', 'recoil_vertical'].map(key => [key, {
    selected: stat(item, key), detailed: field(tableRows(item, 'weapon_stats'), key), infobox: info(item, key),
    source_ref: `wiki:${item.id}@${item.provenance.revid}`,
  }]));
  const extra = supplements[item.id] ?? {};
  // A complete, explicitly labelled Partial single-round row is also a source
  // binding. Do not restrict this mechanic to the original weapon whitelist.
  // Unlabelled revolver, clip or underbarrel times still cannot be guessed.
  const roundReloadBound = ROUND_RELOAD_IDS.has(item.id) || item.infobox.some(r =>
    r.key === 'reload_time_for_one_round' && /\(\s*Partial\s*\)/i.test(r.raw));
  const fireModule=tableRows(item,'wikitable').some(r=>/Fire damage from Stratagems increased by 25%/.test(r.raw)) ? 1.25 : 1;
  const reload = extra.reload_ms ?? milliseconds(info(item, 'reload_time')) ?? (roundReloadBound ? roundReloadProfile(item).full_empty_ms : null);
  const rpms = rates([info(item, 'fire_rate'), stat(item, 'fire_rate')].join('\n'));
  const rpm = rpms.length ? Math.max(...rpms) : null;
  const modes = [];
  const attacks = item.tables.filter(t => ['attack_projectile', 'attack_beam', 'attack_arc', 'attack_spray'].includes(t.kind)
    || (item.id === 'k_2_throwing_knife' || /Melee/.test(trait)) && t.kind === 'attack_damage');
  const effects = item.tables.filter(t => t.kind === 'attack_status').map(t => ({id: t.id.replaceAll(' ','_'), duration_s: number(field(t.rows, 'status_duration')),
    element: field(t.rows, 'element'), normal: number(field(t.rows, 'damage_standard')),
    durable: number(field(t.rows, 'damage_durable')), pen: tiers[field(t.rows, 'pen_direct')]}));
  for(const r of item.tables.flatMap(t=>t.rows).filter(r=>['status','second_status','third_status','aoe_effect_status'].includes(r.key))) {
    const id=r.raw.replaceAll(' ','_');
    if(sharedEffects[id]&&!effects.some(e=>e.id===id))effects.push({...sharedEffects[id]});
  }
  for (const [index, table] of attacks.entries()) {
    const get = key => field(table.rows, key);
    const normal = number(get('damage_standard')), durable = number(get('damage_durable')), pen = tiers[get('pen_direct')];
    if (![normal, durable, pen].every(Number.isFinite)) continue;
    let pellets = get('pellets') ? Number(/^\s*x\s*(\d+)\s*$/i.exec(get('pellets'))?.[1]) : 1;
    if (table.kind === 'attack_arc') pellets = Number(/^\s*x\s*(\d+)\s*$/i.exec(stat(item, 'barrels') ?? '')?.[1] ?? 1);
    if (!Number.isSafeInteger(pellets) || pellets < 1) continue;
    const mechanics = table.kind === 'attack_projectile' ? projectileMechanics(item, table, tiers) : {direct_multiplier: 1, explosion: null, charge_ms: null};
    const chargeRows = tableRows(item, 'weapon_stats').flatMap(r => /^at_\d+_\d+_s$/.test(r.key) ? [Number(r.key.slice(3, -2).replace('_', '.'))] : []);
    const arcCharge = /Charge/i.test(trait) && chargeRows.length ? 1000 * Math.min(...chargeRows) : null;
    const mode = {mode_id: table.id ?? `${table.kind}:${index}`, kind: table.kind.slice(7),
      normal: normal * mechanics.direct_multiplier, durable: durable * mechanics.direct_multiplier, pen, pellets,
      element:get('element')??get('damage_element'),status_applications:statusApplications(table.rows,effects),
      rpm: table.kind === 'attack_beam' ? number(stat(item, 'beam_fire_rate')) : rpm,
      capacity: inventory.capacity, total: inventory.total, reload_ms: reload, traits: trait, disposable: inventory.disposable,
      handling,
      ...mechanics, warmup_ms: milliseconds(stat(item, 'warmup')) ?? milliseconds(stat(item, 'wind_up_time')) ?? 0,
      charge_ms: extra.charge_ms ?? mechanics.charge_ms ?? (Number.isFinite(arcCharge) ? Math.round(arcCharge) : null),
      stagger_force: number(get('stagger_force')), push_force: number(get('push_force')),
      status: get('status')?.replaceAll(' ','_'), range_m: number(get('beam_range')) ?? number(get('arc_range')),
      second_status: get('second_status')?.replaceAll(' ','_'), status_strength: number(get('special_effects_status_strength') ?? get('status_strength')),
      chain_count: number(get('chain_count')), max_split: number(get('max_split')),
      penetration_slowdown: number(get('penetration_slowdown')) !== null ? number(get('penetration_slowdown')) / 100 : null,
      direct_status_effects: statusApplications(table.rows,effects).map(a=>a.effect).filter(Boolean),
      fire_module_multiplier:fireModule,
      area_component_id: get('explosion_on_impact') ?? get('explode_after'),
      delayed_area_component_id: get('explode_after'),
      heat_per_shot: number(stat(item, 'heat_per_shot')), heat_per_second: number(stat(item, 'heat_per_second')),
      overheat_at: number(stat(item, 'overheats_at')),
      cooling_values: String(stat(item, 'cool_per_sec') ?? '').split('-').map(number).filter(Number.isFinite),
      heat_cycle_values: String(stat(item, 'cooldown_after_overheat') ?? '').split('-').map(number).filter(Number.isFinite),
      source_selector: `tables[${table.index ?? item.tables.indexOf(table)}]`,
    };
    // User-selected normal climate; raw triplet order is cold / normal / hot.
    mode.heat_environment = 'normal';
    mode.cool_per_second = mode.cooling_values.length === 3 ? mode.cooling_values[1] : null;
    mode.cooling_environment_basis = mode.cooling_values.length === 3
      ? 'WIKI_ATTACK_SETUP_COLD_NORMAL_HOT; USER_NORMAL_CLIMATE' : 'TEMPERATURE_MAPPING_UNRESOLVED';
    if (roundReloadBound) mode.round_reload_profile = roundReloadProfile(item);
    // Fragment and separate underbarrel projectiles must not borrow main-feed RPM.
    mode.feed_bound = index === 0 || Number.isFinite(mode.charge_ms);
    if(item.id==='heavy_support_gr_8_recoilless_rifle'&&table.id==='GR-8_P1') {
      mode.feed_bound=true;mode.firing_mode='HE';mode.mode_policy='SELECT_BEFORE_ENGAGEMENT; SHARED_MAIN_AMMO';
    }
    if (item.id==='heavy_support_ac_8_autocannon'&&table.id==='AC-8_P1') {
      mode.feed_bound=true;mode.firing_mode='FLAK';
      mode.proximity_m=number(get('explosion_proximity'));
      mode.direct_impact_alternative={normal:mode.normal,durable:mode.durable,pen:mode.pen};
      mode.normal=0;mode.durable=0;
    }
    const area=mechanics.explosion?.id?item.tables.find(t=>t.kind==='attack_explosion'&&t.id===mechanics.explosion.id):null;
    const fragmentId=field(area?.rows,'shrapnel'), fragment=fragmentId?item.tables.find(t=>t.kind==='attack_projectile'&&t.id===fragmentId):null;
    if (fragment&&fragmentId==='AC-8_P2'&&['heavy_support_ac_8_autocannon','r_36_eruptor'].includes(item.id)) {
      mode.fragments={component_id:fragmentId,count:number(field(area.rows,'shrapnel_count')),
        normal:number(field(fragment.rows,'damage_standard')),durable:number(field(fragment.rows,'damage_durable')),
        pen:tiers[field(fragment.rows,'pen_direct')],large_angle_pen:tiers[field(fragment.rows,'pen_large_angle')],
        source_ref:`wiki:${item.id}@${item.provenance.revid}`};
      mode.fragment_policy={main_contacts_per_shot:1,max_per_target:1,
        scope:'ENGINEERING_ONE_CONTACT_MAIN_SCENARIO; ACTUAL_HIT_COUNT_UNKNOWN; ALSO_REPORT_ZERO_CONTACT_BOUND'};
    } else if(fragment) {
      mode.unbound_child_projectiles={component_id:fragmentId,count:number(field(area.rows,'shrapnel_count')),
        reason:'SUBMUNITION_IMPACT_AND_DETONATION_REQUIRE_OWN_BINDING; NOT_AC8_BALLISTIC_FRAGMENT'};
    }
    if (mode.explosion&&area) {
      mode.explosion.status=field(area.rows,'status')?.replaceAll(' ','_');
      mode.explosion.status_strength=number(field(area.rows,'status_strength')??field(area.rows,'aoe_effect_status_strength'));
      mode.explosion.status_applications=statusApplications(area.rows,effects);
      mode.explosion.status_effects=mode.explosion.status_applications.map(a=>a.effect).filter(Boolean);
      mode.explosion.element=field(area.rows,'element')??field(area.rows,'damage_element');
      mode.explosion.fire_module_multiplier=fireModule;
    }
    if (extra.reload_ms === 0) mode.no_manual_reload = true;
    modes.push(mode);
  }
  return {id: item.id, category: item.category, traits: trait,
    holding: {support: item.category === 'support', requires_backpack: item.category === 'support' && /\bBackpack\b/.test(trait)},
    demolition_components: noncombat || item.category === 'deployable' ? []
      : extractDemolitionCandidates({items: [item]}).equipment[0].attacks.map(a => ({component_id: a.component_id,
        attack_type: a.attack_type, backblast: a.backblast, force: a.demolition_force?.number ?? null, routes: a.routes})),
    fuse_ms: /^Impact$/i.test(info(item, 'fuse_time') ?? '') ? 0 : milliseconds(info(item, 'fuse_time')),
    fuse_kind: /^Impact$/i.test(info(item, 'fuse_time') ?? '') ? 'impact'
      : /^Proximity$/i.test(info(item, 'fuse_time') ?? '') ? 'proximity'
        : milliseconds(info(item, 'fuse_time')) !== null ? 'timed' : 'unknown',
    inventory: noncombat ? {kind: 'supply_only', feed: 'none', capacity: 0, reserve: 0, total: 0} : inventory,
    refill: noncombat ? {public: 0, portable: 0} : sourceRefill(item, inventory),
    reload_ms: noncombat ? null : reload, modes: noncombat ? [] : modes,
    ergonomic: handling.ergonomics, handling: noncombat ? null : handling,
    handling_source: noncombat ? null : handlingSource, source_ref: `wiki:${item.id}@${item.provenance.revid}`,
    known_noncombat: noncombat, supply_only: supplyOnly,
    known_zero_dimensions: noncombat ? ['chaff', 'close_defense', 'anti_medium', 'anti_heavy', 'demolition'] : [],
    combat_exclusion: supplyOnly ? 'USER_SUPPLY_VEHICLE_TURRET_EXCLUDED' : noncombat ? 'SUPPLY_PROVIDER_ONLY' : null,
    areas: noncombat ? [] : item.tables.filter(t => t.kind === 'attack_explosion' && !/BACKBLAST/i.test(t.id ?? t.title ?? '')).map(t => ({
      id: t.id ?? t.title, normal: number(field(t.rows, 'damage_inner_radius')),
      element:field(t.rows,'element')??field(t.rows,'damage_element'),fire_module_multiplier:fireModule,
      status_applications:statusApplications(t.rows,effects),
      durable: number(field(t.rows, 'inner_durable')), pen: tiers[field(t.rows, 'pen_inner')],
      outer_normal:number(field(t.rows,'damage_outer_radius')),outer_durable:number(field(t.rows,'outer_durable')),
      outer_pen: tiers[field(t.rows, 'pen_outer')],
      inner_radius_m: number(field(t.rows, 'area_of_effect_inner_radius')),
      outer_radius_m: number(field(t.rows, 'area_of_effect_outer_radius') ?? field(t.rows, 'outer_radius')),
      duration_s: number(field(t.rows, 'aoe_duration')), status: (field(t.rows, 'status') ?? field(t.rows, 'aoe_effect_status'))?.replaceAll(' ','_'),
      arc_id: field(t.rows, 'arc'),
      status_strength:number(field(t.rows,'status_strength') ?? field(t.rows,'aoe_effect_status_strength')),
      second_status: field(t.rows, 'second_status')?.replaceAll(' ','_'), source_selector: `tables[${item.tables.indexOf(t)}]`,
    })),
    effects, supplements: extra, listed_fire_rate: number(info(item,'fire_rate')),
    fire_damage_multiplier: fireModule,
    stamina_cost_percent: number(stat(item,'stamina_cost')),
    declared_dps: /DPS/.test(info(item, 'damage') ?? '') ? number(info(item, 'damage')) : null,
  };
}

// Full-stock effective kill capacity: a pure arithmetic layer.
// Inputs are explicit (one weapon mode, one target profile, the AP multipliers); the module
// never scores, never selects a target, never models resupply and never touches data files.

import {directDamage, fixedBTK} from '../../engine/damage.mjs';

const finite = x => typeof x === 'number' && Number.isFinite(x);
const absent = v => v === null || v === undefined;
const uniq = a => [...new Set(a)];

export const CAPACITY_STATE = Object.freeze({
  KNOWN: 'KNOWN',
  MIXED: 'MIXED',
  UNKNOWN_DATA: 'UNKNOWN_DATA',
  UNKNOWN_DAMAGE_UNIT: 'UNKNOWN_DAMAGE_UNIT',
  UNKNOWN_INVENTORY: 'UNKNOWN_INVENTORY',
  AP_BLOCKED: 'AP_BLOCKED',
  INVALID_INPUT: 'INVALID_INPUT'
});

// Two firing modes share one magazine/one barrel; their stocks are never added up.
export const COMBINED_REASON = 'MODES_ARE_MUTUALLY_EXCLUSIVE_NOT_SUMMED';

// AP multipliers are supplied by the caller (see ap-multipliers/source-archive.json).
// "less" is fixed to NO_DAMAGE: low penetration produces a ricochet, not ceil(hp/0) = Infinity.
export function apMultipliers(ap) {
  if (!ap || typeof ap !== 'object') return null;
  const {equal, greater} = ap;
  if (!finite(equal) || equal < 0 || !finite(greater) || greater < 0) return null;
  if (!absent(ap.less) && ap.less !== 'NO_DAMAGE' && !(finite(ap.less) && ap.less === 0)) return null;
  return Object.freeze({equal, greater, less: 'NO_DAMAGE'});
}

export function armorRelation(penetration, armor) {
  if (!finite(penetration) || !finite(armor)) return null;
  return penetration > armor ? 'AP_GREATER' : penetration === armor ? 'AP_EQUAL' : 'AP_LESS';
}

// Target profile = one hit part. hp is that part's pool, armor is the AV ordinal of that part,
// durable is the part's durable fraction (0..1) as written by the source table.
function targetFields(target) {
  if (!target || typeof target !== 'object') return ['target'];
  const missing = [];
  if (!finite(target.hp) || target.hp <= 0) missing.push('target.hp');
  if (!finite(target.armor) || target.armor < 0) missing.push('target.armor');
  if (!finite(target.durable) || target.durable < 0 || target.durable > 1) missing.push('target.durable');
  return missing;
}

function componentFields(component) {
  if (!component || typeof component !== 'object') return ['component'];
  return ['normal', 'durable', 'pen'].filter(k => !finite(component[k]) || component[k] < 0);
}

// One projectile packet (or one explosion) against one part, with that packet's own AP value.
// Blending and rounding reuse engine/damage.mjs: blend normal/durable first, multiply the AP
// factor, floor once per projectile, then multiply by the pellet count.
export function projectileDamage(projectile, target, ap) {
  const mult = apMultipliers(ap);
  const missing = [...targetFields(target), ...componentFields(projectile)];
  if (!Number.isInteger(projectile?.pellets) || projectile.pellets < 1) missing.push('pellets');
  if (!mult) missing.push('ap.equal/greater');
  if (missing.length) return {state: CAPACITY_STATE.UNKNOWN_DATA, missing: uniq(missing)};

  const relation = armorRelation(projectile.pen, target.armor);
  const result = directDamage({
    normal: projectile.normal,
    durable: projectile.durable,
    durability: target.durable,
    penetration: projectile.pen,
    armor: target.armor,
    equal_multiplier: mult.equal,
    greater_multiplier: mult.greater,
    rounding: projectile.continuous ? 'none' : 'floor'
  });
  if (result.status !== 'KNOWN') return {state: CAPACITY_STATE.UNKNOWN_DATA, missing: ['directDamage:UNKNOWN_DATA']};

  return {
    state: relation === 'AP_LESS' ? CAPACITY_STATE.AP_BLOCKED : CAPACITY_STATE.KNOWN,
    relation,
    penetration: projectile.pen,
    armor: target.armor,
    per_pellet_damage: result.damage,
    pellets: projectile.pellets,
    total_damage: result.damage * projectile.pellets
  };
}

// One round = the direct projectile packet plus at most the one explosion that is bound to the
// same hit. The explosion keeps its own AP and its own durable blend; it never borrows the
// projectile's AP, and no extra body part or extra blast target is added.
export function shotDamage(mode, target, ap) {
  if (!mode || typeof mode !== 'object') return {state: CAPACITY_STATE.UNKNOWN_DAMAGE_UNIT, reason: 'NO_MODE_DEFINITION'};
  const missing = [...targetFields(target), ...componentFields(mode)];
  if (!Number.isInteger(mode.pellets) || mode.pellets < 1) missing.push('pellets');
  if (!apMultipliers(ap)) missing.push('ap.equal/greater');
  if (missing.length) return {state: CAPACITY_STATE.UNKNOWN_DATA, reason: 'MISSING_FIELDS', missing: uniq(missing)};

  const direct = projectileDamage({normal: mode.normal, durable: mode.durable, pen: mode.pen, pellets: mode.pellets, continuous: mode.continuous}, target, ap);
  if (direct.state === CAPACITY_STATE.UNKNOWN_DATA) return {state: CAPACITY_STATE.UNKNOWN_DATA, reason: 'MISSING_FIELDS', missing: direct.missing.map(k => `direct.${k}`)};

  let explosion = null;
  if (!absent(mode.explosion)) {
    if (typeof mode.explosion !== 'object') return {state: CAPACITY_STATE.UNKNOWN_DATA, reason: 'MISSING_FIELDS', missing: ['explosion']};
    explosion = projectileDamage({normal: mode.explosion.normal, durable: mode.explosion.durable, pen: mode.explosion.pen, pellets: 1}, target, ap);
    if (explosion.state === CAPACITY_STATE.UNKNOWN_DATA) return {state: CAPACITY_STATE.UNKNOWN_DATA, reason: 'MISSING_FIELDS', missing: explosion.missing.map(k => `explosion.${k}`)};
  }

  const perShot = direct.total_damage + (explosion?.total_damage ?? 0);
  const warnings = [];
  if (direct.state === CAPACITY_STATE.AP_BLOCKED && perShot > 0) warnings.push('DIRECT_AP_BLOCKED_EXPLOSION_ONLY');
  if (explosion?.state === CAPACITY_STATE.AP_BLOCKED && direct.total_damage > 0) warnings.push('EXPLOSION_AP_BLOCKED_DIRECT_ONLY');
  if (mode.pellets > 1) warnings.push('PELLET_PACKET_AGGREGATED_PER_ROUND');

  if (perShot === 0) {
    return {state: CAPACITY_STATE.AP_BLOCKED, reason: 'ALL_COMPONENTS_BELOW_TARGET_ARMOR', per_shot_damage: 0, direct, explosion, warnings};
  }
  return {state: CAPACITY_STATE.KNOWN, per_shot_damage: perShot, direct, explosion, warnings};
}

// Stock accounting: how many discrete rounds the source records. Heat/beam/spray weapons have no
// confirmed per-ammo-unit stock, so they stay UNKNOWN instead of being faked as infinite or zero.
export function inventoryState(mode, {resupply_kind} = {}) {
  if (!mode || typeof mode !== 'object') return {state: CAPACITY_STATE.UNKNOWN_DAMAGE_UNIT, reason: 'NO_MODE_DEFINITION'};
  if (resupply_kind === 'heat') return {state: CAPACITY_STATE.UNKNOWN_INVENTORY, reason: 'HEAT_OR_BEAM_STOCK_IS_NOT_A_CONFIRMED_ROUND_COUNT'};
  if (resupply_kind !== 'finite_ammo' && /(^|[•\s])heat($|[•\s])/i.test(String(mode.traits ?? ''))) return {state: CAPACITY_STATE.UNKNOWN_INVENTORY, reason: 'HEAT_TRAIT_WITHOUT_CONFIRMED_ROUND_STOCK'};
  // Only an explicit resupply_kind=finite_ammo may override the heat trait: heat sinks are not rounds.
  if (!finite(mode.total) || mode.total <= 0) return {state: CAPACITY_STATE.UNKNOWN_INVENTORY, reason: 'NO_CONFIRMED_ROUND_COUNT'};
  const warnings = Number.isInteger(mode.total) ? [] : ['FRACTIONAL_ROUND_COUNT_FLOORED'];
  return {state: CAPACITY_STATE.KNOWN, rounds_total: Math.floor(mode.total), warnings};
}

// Full-stock capacity for one mode against one part.
// mode.normal / mode.durable are already the per-projectile values of the parsed mode, including
// that mode's direct_multiplier, so the multiplier is deliberately not applied a second time.
export function killCapacity(mode, target, ap, {resupply_kind} = {}) {
  const shot = shotDamage(mode, target, ap);
  const head = {
    mode_id: mode?.mode_id ?? null,
    target_id: target?.target_id ?? null,
    part_id: target?.part_id ?? null,
    hp: finite(target?.hp) ? target.hp : null,
    armor: finite(target?.armor) ? target.armor : null,
    durable: finite(target?.durable) ? target.durable : null,
    per_shot_damage: shot.per_shot_damage ?? null,
    direct: shot.direct ?? null,
    explosion: shot.explosion ?? null,
    warnings: shot.warnings ?? []
  };
  const blank = {btk: null, max_full_kills: null, shots_consumed: null, remaining_shots: null, damage_delivered: null, damage_utilized: null, overkill_loss: null, raw_stock_damage: null, rounds_total: null};

  if (shot.state === CAPACITY_STATE.UNKNOWN_DATA || shot.state === CAPACITY_STATE.UNKNOWN_DAMAGE_UNIT) {
    return {...head, ...blank, state: shot.state, reason: shot.reason, missing: shot.missing ?? []};
  }

  const inventory = inventoryState(mode, {resupply_kind});
  const rounds = inventory.state === CAPACITY_STATE.KNOWN ? inventory.rounds_total : null;

  if (shot.state === CAPACITY_STATE.AP_BLOCKED) {
    return {
      ...head,
      ...blank,
      state: CAPACITY_STATE.AP_BLOCKED,
      reason: shot.reason,
      rounds_total: rounds,
      inventory: inventory.state === CAPACITY_STATE.KNOWN ? {state: CAPACITY_STATE.KNOWN} : {state: inventory.state, reason: inventory.reason},
      damage_delivered: 0,
      damage_utilized: 0,
      overkill_loss: 0
    };
  }

  if (inventory.state !== CAPACITY_STATE.KNOWN) {
    return {...head, ...blank, state: inventory.state, reason: inventory.reason, inventory: {state: inventory.state, reason: inventory.reason}};
  }

  const btk = fixedBTK(head.hp, shot.per_shot_damage);
  if (!Number.isFinite(btk)) return {...head, ...blank, state: CAPACITY_STATE.UNKNOWN_DATA, reason: 'BTK_UNDEFINED', rounds_total: rounds};

  const kills = Math.floor(rounds / btk);
  const shotsConsumed = kills * btk;
  const delivered = shotsConsumed * shot.per_shot_damage;
  const utilized = kills * head.hp;
  return {
    ...head,
    state: CAPACITY_STATE.KNOWN,
    reason: null,
    btk,
    max_full_kills: kills,
    shots_consumed: shotsConsumed,
    remaining_shots: rounds - shotsConsumed,
    damage_delivered: delivered,
    damage_utilized: utilized,
    overkill_loss: delivered - utilized,
    raw_stock_damage: rounds * shot.per_shot_damage,
    rounds_total: rounds,
    inventory: {state: CAPACITY_STATE.KNOWN, ...(inventory.warnings?.length ? {warnings: inventory.warnings} : {})},
    notes: [
      '整枪只有一个命中部位口径：hp 是该部位池，不做多部位分摊/爆炸乘倍',
      'kills 用整弹量整除 BTK，余弹不足以再完成一次完整击杀'
    ]
  };
}

// Several modes of one weapon:每模式分别报，绝不把互斥模式的库存相加。
export function stockCapacity({modes, target, ap, mode_id, resupply_kind} = {}) {
  const all = Array.isArray(modes) ? modes : [];
  const base = {target_id: target?.target_id ?? null, part_id: target?.part_id ?? null, mode_id: mode_id ?? null, combined_kills: null, combined_reason: COMBINED_REASON};
  if (!all.length) return {...base, state: CAPACITY_STATE.UNKNOWN_DAMAGE_UNIT, reason: 'NO_MODE_DEFINITION', per_mode: []};

  const selected = absent(mode_id) ? all : all.filter(m => m?.mode_id === mode_id);
  if (!selected.length) return {...base, state: CAPACITY_STATE.UNKNOWN_DATA, reason: 'MODE_ID_NOT_FOUND', per_mode: []};

  const ids = selected.map(m => m?.mode_id);
  if (uniq(ids).length !== ids.length) return {...base, state: CAPACITY_STATE.INVALID_INPUT, reason: 'DUPLICATE_MODE_ID', per_mode: []};

  const perMode = selected.map(m => killCapacity(m, target, ap, {resupply_kind}));
  const states = uniq(perMode.map(r => r.state));
  return {
    ...base,
    state: states.length === 1 ? states[0] : CAPACITY_STATE.MIXED,
    ap: apMultipliers(ap) ? {...apMultipliers(ap)} : null,
    per_mode: perMode
  };
}

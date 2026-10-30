import {directDamage} from '../engine/damage.mjs';
import {falloff} from '../explosions/falloff.mjs';
import {capacityCurve} from './formulas.mjs';
import {assessCrowdPressure} from '../practical/crowd-pressure.mjs';

const nn = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
const pos = n => nn(n) && n > 0;
const unknown = reason => ({status: 'UNKNOWN', reason});

// One explicit target instance / one part per blast. Multiple parts of one heavy
// enemy must go through the existing composite explosion route engine instead.
// Radius alone never invents a target density, LoS or a second blast event.
export function finiteBlast({blast, targets, ap, geometry} = {}) {
  if (!geometry?.id || !['source', 'user', 'scenario'].includes(geometry.basis)
    || !Array.isArray(targets) || !targets.length) return unknown('FINITE_GEOMETRY_REQUIRED');
  if (new Set(targets.map(t => t.instance_id)).size !== targets.length) return unknown('MULTIPART_REQUIRES_COMPOSITE_ROUTE');
  const hits = [];
  for (const t of targets) {
    if (!t.instance_id || !t.part_id || !pos(t.hp) || !['clear', 'blocked'].includes(t.line_of_sight)) return unknown('TARGET_OR_VISIBILITY_REQUIRED');
    if (!nn(t.explosion_resistance) || t.explosion_resistance > 1) return unknown('PART_EXPLOSION_RESISTANCE_REQUIRED');
    const radial = falloff({distance_m: t.distance_m, inner_radius: blast?.inner_radius_m,
      outer_radius: blast?.outer_radius_m, curve: blast?.falloff_curve});
    if (!['KNOWN', 'OUT_OF_RADIUS'].includes(radial.status)) return unknown(radial.status);
    const result = radial.multiplier === 0 || t.line_of_sight === 'blocked' ? {status: 'KNOWN', damage: 0}
      : directDamage({normal: blast.normal, durable: blast.durable, penetration: blast.pen + radial.ap_delta,
        durability: t.durable, armor: t.armor, equal_multiplier: ap?.equal, greater_multiplier: ap?.greater,
        falloff: radial.multiplier, multiplier: 1 - t.explosion_resistance});
    if (result.status !== 'KNOWN') return unknown('BLAST_DAMAGE_FIELDS_REQUIRED');
    hits.push({instance_id: t.instance_id, part_id: t.part_id, damage: result.damage,
      effective_hp_damage: Math.min(t.hp, result.damage), remaining_hp: Math.max(0, t.hp - result.damage),
      lethal_part_broken: result.damage >= t.hp && t.fatal === true, zone: radial.zone,
      explosion_resistance: t.explosion_resistance});
  }
  return {status: 'ESTIMATE', geometry_id: geometry.id, geometry_basis: geometry.basis, hits,
    effective_hp_damage: hits.reduce((n, h) => n + h.effective_hp_damage, 0),
    caveat: 'PART_DAMAGE_ONLY; NO_INVENTED_MAIN_TRANSFER_OR_INSTANT_NEUTRALIZATION'};
}

export function unionWindows(windows) {
  if (!Array.isArray(windows) || windows.some(w => !nn(w.start_ms) || !nn(w.end_ms) || w.end_ms < w.start_ms)) return null;
  const sorted = windows.map(w => ({...w})).sort((a, b) => a.start_ms - b.start_ms), merged = [];
  for (const w of sorted) {
    const last = merged.at(-1);
    if (last && w.start_ms <= last.end_ms) last.end_ms = Math.max(last.end_ms, w.end_ms);
    else merged.push(w);
  }
  return merged;
}

// Reapplications refresh an explicitly declared non-stacking effect. Different
// damage/status types are separate calls; do not use this to infer stacking rules.
export function refreshingStatus({applications_ms, duration_ms, damage_per_second, target_hp, horizon_ms, stacking} = {}) {
  if (stacking !== 'refresh' || !Array.isArray(applications_ms) || applications_ms.some(t => !nn(t))
    || !pos(duration_ms) || !nn(damage_per_second) || !pos(target_hp) || !nn(horizon_ms)) return unknown('STATUS_TIMELINE_OR_STACKING_REQUIRED');
  const windows = unionWindows(applications_ms.filter(t => t < horizon_ms).map(t => ({start_ms: t, end_ms: Math.min(horizon_ms, t + duration_ms)})));
  const activeMs = windows.reduce((n, w) => n + w.end_ms - w.start_ms, 0);
  return {status: 'ESTIMATE', windows, active_ms: activeMs,
    effective_hp_damage: Math.min(target_hp, activeMs / 1000 * damage_per_second),
    caveat: 'TARGET_EFFECTIVE_DPS_REQUIRED; NO_DAMAGE_FROM_CONFUSION_OR_SLOW'};
}

// A continuous heat weapon's repeating fire/cool cycle. The caller supplies a
// usable firing heat budget and a confirmed environment; no universal climate,
// hidden sink replacement or assumed overheat behaviour is selected here.
export function thermalCycle({usable_heat, heat_per_second, cool_per_second, cooling_delay_s, warmup_s} = {}) {
  if (![usable_heat, heat_per_second, cool_per_second].every(pos)
    || !nn(cooling_delay_s) || !nn(warmup_s)) return unknown('HEAT_CYCLE_FIELDS_REQUIRED');
  const fire = usable_heat / heat_per_second, cool = usable_heat / cool_per_second;
  return {status: 'ESTIMATE', uninterrupted_fire_s: fire, cooling_s: cool,
    cycle_s: warmup_s + fire + cooling_delay_s + cool,
    firing_fraction: fire / (warmup_s + fire + cooling_delay_s + cool),
    scope: 'CONTROLLED_STOP_BEFORE_OVERHEAT; WARMUP_HEAT_EXCLUDED_FROM_USABLE_BUDGET'};
}

// An explicit enemy-pressure scenario, not a guess at N10's real spawn rate.
// Burst kills and available sustained kills MUST use disjoint resources.
export function pressureAssessment(input = {}) {
  const physical = assessCrowdPressure(input);
  if (physical.status === 'UNKNOWN') return physical;
  const {present: n, arrivalsPerSecond: lambda, effectiveKillsPerSecond: mu,
    availableKills: stock, safeWindowSeconds: window, immediateKills: suppliedBurst = 0} = input;
  const burst = Math.min(n, suppliedBurst), remaining = n - burst;
  // Solve clearance until the backlog actually reaches zero, not until the end
  // of the full window: early clearance need not pay for hypothetical later spawns.
  const capacity = mu > lambda ? Math.max(0, Math.min((mu - lambda) * window, stock * (1 - lambda / mu))) : 0;
  const ratio = remaining > 0 ? capacity / remaining : null;
  const initialLimit = burst + capacity;
  return {status: 'ESTIMATE', physical, capacity_ratio: ratio,
    score: ratio === null ? null : capacityCurve(ratio), critical_present: initialLimit,
    unit: 'ENEMIES_OF_DECLARED_MIX', not_a_verified_gate: true};
}

// Separate a lethal-part break from the end of residual threat. No arbitrary
// bleed-out delay is inferred from a constitution label or a weapon's BTK.
export function threatResolution({part_break_ms, residual_threat_ms} = {}) {
  if (!nn(part_break_ms)) return unknown('PART_BREAK_TIME_REQUIRED');
  return nn(residual_threat_ms) ? {status: 'ESTIMATE', part_break_ms, neutralized_at_ms: part_break_ms + residual_threat_ms}
    : {...unknown('RESIDUAL_THREAT_ROUTE_REQUIRED'), part_break_ms};
}

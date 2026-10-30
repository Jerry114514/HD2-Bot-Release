const finite = x => typeof x === 'number' && Number.isFinite(x);
export function directDamage({normal, durable, durability, penetration, armor, equal_multiplier, greater_multiplier, falloff = 1, multiplier = 1, rounding = 'floor'}) {
  if (![normal, durable, durability, penetration, armor, equal_multiplier, greater_multiplier, falloff, multiplier].every(finite) || durability < 0 || durability > 1 || [normal, durable, equal_multiplier, greater_multiplier, falloff, multiplier].some(x => x < 0) || !['floor', 'none'].includes(rounding)) return {status: 'UNKNOWN_DATA'};
  const ap = penetration < armor ? 0 : penetration === armor ? equal_multiplier : greater_multiplier;
  const raw = (normal * (1 - durability) + durable * durability) * ap * falloff * multiplier;
  return {status: 'KNOWN', damage: rounding === 'floor' ? Math.floor(raw) : raw};
}
export function fixedBTK(hp, damage) {
  if (!finite(hp) || hp <= 0 || !finite(damage) || damage < 0) return null;
  return damage === 0 ? Infinity : Math.ceil(hp / damage);
}
export function responseTime({shots, rpm, aim_ms = 0, charge_ms = 0, flight_ms = 0, switch_ms = 0, reload_ms = 0, magazine, post_lethal_ms = 0}) {
  if (!Number.isInteger(shots) || shots < 1 || !Number.isInteger(magazine) || magazine < 1 || !finite(rpm) || rpm <= 0 || [aim_ms, charge_ms, flight_ms, switch_ms, reload_ms, post_lethal_ms].some(x => !finite(x) || x < 0)) return null;
  return aim_ms + charge_ms + switch_ms + (shots - 1) * 60000 / rpm + Math.floor((shots - 1) / magazine) * reload_ms + flight_ms + post_lethal_ms;
}
export function sustainedDPS({magazine, damage, rpm, reload_ms}) {
  if (!Number.isInteger(magazine) || magazine < 1 || ![damage, rpm, reload_ms].every(finite) || damage < 0 || rpm <= 0 || reload_ms < 0) return null;
  // Cycle begins with the first shot; includes the interval before the next magazine's first shot.
  return magazine * damage / (magazine * 60 / rpm + reload_ms / 1000);
}
export function demolitionRoute(component, route, grade = 'verified') {
  // A demolition route is only as strong as its own grade: a reviewed component never
  // rides a verified route and a verified component never rides a reviewed one.
  if (!component || component.kind !== 'explosion' || component.usable !== true || component.state !== grade || !component.evidence_refs?.length) return false;
  if (finite(component.demolition) && component.demolition >= 20) return true;
  return !!(route?.id === 'southwest' && route.state === 'verified' && route.evidence_refs?.length && route.component_id === component.id && route.success === true);
}
export function finiteExplosion(hits, {coverage_verified = false, transfer = 'max', evidence_refs = []} = {}) {
  // Input is a calibrated path table, not a geometric blast simulator. One transfer per target/event.
  if (!coverage_verified || !evidence_refs.length || !['max', 'sum'].includes(transfer) || hits.some(h => !finite(h.damage) || h.damage < 0 || !h.part_id)) return {status: 'UNKNOWN_COVERAGE'};
  if (new Set(hits.map(h => h.part_id)).size !== hits.length) return {status: 'INVALID_INPUT'};
  return {status: 'KNOWN', part_damage: Object.fromEntries(hits.map(h => [h.part_id, h.damage])), main_damage: transfer === 'max' ? Math.max(0, ...hits.map(h => h.damage)) : hits.reduce((n, h) => n + h.damage, 0)};
}

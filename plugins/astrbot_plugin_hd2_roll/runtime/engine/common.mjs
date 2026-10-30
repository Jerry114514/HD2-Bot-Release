export const VERSION = '3.0.0';
export const withinExosuitLimit = ids => ids.filter(id=>/^vehicle_exo_/.test(id)).length<=1;
export const DIMENSIONS = Object.freeze(['anti_heavy', 'chaff', 'sustain', 'anti_medium', 'close_defense', 'demolition']);
export const SLOTS = Object.freeze(['primary', 'secondary', 'grenade']);
export const clone = value => structuredClone(value);
// Timeline active slot -> catalog capability token. A card can only occupy a slot its
// active_slots declares; primaries/grenades/etc. carry [] and stay out of both slots.
export const SLOT_CAPABILITY = Object.freeze({weapon: 'support_weapon', backpack: 'backpack'});
export const canOccupy = (equipment, slot) => (equipment?.active_slots || []).includes(SLOT_CAPABILITY[slot]);
// Initial/declared slots must be a plain object when given; arrays, null and strings are
// errors, never silently coerced to "no slots".
export function validSlotsShape(value) {
  if (value === undefined) return true;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  return Object.entries(value).every(([slot, id]) => ['weapon', 'backpack'].includes(slot) && typeof id === 'string' && id.length > 0);
}
export function stable(value) {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + stable(value[k])).join(',') + '}';
  return JSON.stringify(value);
}
export async function sha256(value) {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(v => v.toString(16).padStart(2, '0')).join('');
}
export function canonical(loadout) {
  return stable({primary: loadout.primary, secondary: loadout.secondary, grenade: loadout.grenade, stratagems: [...loadout.stratagems].sort()});
}
export const equipmentIDs = l => [...SLOTS.map(s => l[s]), ...l.stratagems];
export function profileReady(profile) {
  return !!(profile?.enabled && profile.game_build && profile.calibration?.state === 'verified' && profile.calibration.evidence_refs?.length);
}
export function legality(loadout, catalog, options = {}) {
  if (!loadout || !Array.isArray(loadout.stratagems) || loadout.stratagems.length !== 4 || SLOTS.some(s => typeof loadout[s] !== 'string')) return {status: 'INVALID_INPUT', reason: 'SEVEN_SLOTS_REQUIRED'};
  const ids = equipmentIDs(loadout), byID = new Map(catalog.equipment.map(e => [e.id, e]));
  if (new Set(ids).size !== 7) return {status: 'FAIL_PROVED', reason: 'DUPLICATE_SELECTION'};
  const excluded = new Set(options.excluded || []), unlocked = options.unlocked ? new Set(options.unlocked) : null;
  for (let i = 0; i < ids.length; i++) {
    const e = byID.get(ids[i]);
    if (!e || e.selection_slot !== (i < 3 ? SLOTS[i] : 'stratagem')) return {status: 'INVALID_INPUT', reason: 'WRONG_SELECTION_SLOT', equipment_id: ids[i]};
    if (excluded.has(e.id) || (unlocked && !unlocked.has(e.id))) return {status: 'FAIL_PROVED', reason: 'USER_POOL_CONSTRAINT', equipment_id: e.id};
  }
  // Only documented card-selection rules belong here. Active backpack/weapon capacity belongs to the timeline.
  for (const rule of catalog.selection_rules || []) {
    if (rule.state !== 'verified' || !rule.evidence_refs?.length) continue;
    if (rule.kind === 'mutually_exclusive' && rule.equipment_ids.filter(id => ids.includes(id)).length > 1) return {status: 'FAIL_PROVED', reason: rule.id};
    if (rule.kind !== 'mutually_exclusive') return {status: 'INVALID_INPUT', reason: 'UNSUPPORTED_SELECTION_RULE'};
  }
  return {status: 'LEGAL'};
}

// A route can remain a legal emergency action without funding a heavy-duty goal.
export function actionAllowedInCase(action, spec) {
  const dims = new Set([...(spec.goals || []), ...(spec.conditional_goals || [])].map(g => g.dimension));
  return !(action.excluded_dimensions || []).some(d => dims.has(d));
}

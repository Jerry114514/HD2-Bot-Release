// Armor / penetration comparison with an INJECTED, sourced model.
//
// This file deliberately contains no default ordering, no default multipliers and no
// "Anti-Tank is above Heavy" ladders. The wiki part panels use tier labels and the
// weapon tables use AP labels; the usable numeric mapping (AP value, armor value) and
// the equal/greater damage multipliers come from a separate, sourced package. Until a
// caller injects one, every comparison returns UNKNOWN_MODEL and no damage number is
// produced from a guessed relation.
//
// Model shape:
//   {
//     ap_values:     { 'Anti-Tank II': <number>, ... },
//     armor_values:  { Heavy: <number>, 'Very Light': <number>, ... },
//     multipliers:   { below: 0, equal: <number>, greater: <number> },
//     state:         'sourced' | 'illustrative',
//     source_refs:   ['...'],
//     note:          optional string,
//   }
// 'illustrative' models are allowed for tests/diagnostics but always mark results as
// assumed inputs so they can never be confused with sourced evidence.

export const MODEL_STATE = Object.freeze({ SOURCED: 'sourced', ILLUSTRATIVE: 'illustrative', CANDIDATE_UNVERIFIED: 'candidate_unverified' });

const finite = x => typeof x === 'number' && Number.isFinite(x);

export function validatePenetrationModel(model) {
  if (!model || typeof model !== 'object') return { status: 'MISSING', reasons: ['PENETRATION_MODEL_MISSING'] };
  const reasons = [];
  if (!model.ap_values || typeof model.ap_values !== 'object') reasons.push('AP_VALUES_MISSING');
  if (!model.armor_values || typeof model.armor_values !== 'object') reasons.push('ARMOR_VALUES_MISSING');
  const multipliers = model.multipliers;
  if (!multipliers || !['below', 'equal', 'greater'].every(key => finite(multipliers[key]) && multipliers[key] >= 0)) {
    reasons.push('MULTIPLIERS_MISSING');
  }
  if (![MODEL_STATE.SOURCED, MODEL_STATE.ILLUSTRATIVE, MODEL_STATE.CANDIDATE_UNVERIFIED].includes(model.state)) reasons.push('MODEL_STATE_INVALID');
  if (!Array.isArray(model.source_refs) || !model.source_refs.length) reasons.push('MODEL_SOURCE_REFS_MISSING');
  if (model.state === MODEL_STATE.SOURCED && (!Array.isArray(model.source_refs) || !model.source_refs.length)) reasons.push('SOURCED_MODEL_WITHOUT_SOURCE');
  const apCount = model.ap_values ? Object.keys(model.ap_values).filter(key => finite(model.ap_values[key])).length : 0;
  const armorCount = model.armor_values ? Object.keys(model.armor_values).filter(key => finite(model.armor_values[key])).length : 0;
  if (model.ap_values && !apCount) reasons.push('AP_VALUES_EMPTY');
  if (model.armor_values && !armorCount) reasons.push('ARMOR_VALUES_EMPTY');
  if (reasons.length) return { status: 'INVALID', reasons };
  return { status: 'KNOWN', state: model.state, source_refs: [...model.source_refs], ap_labels: Object.keys(model.ap_values).length, armor_labels: Object.keys(model.armor_values).length };
}

export function modelIsSourced(model) {
  return validatePenetrationModel(model).status === 'KNOWN' && model.state === MODEL_STATE.SOURCED;
}

// Compare one AP label with one armor label under the injected model.
export function comparePenetration({ penetration, armor, model }) {
  const validation = validatePenetrationModel(model);
  if (validation.status !== 'KNOWN') return { status: 'UNKNOWN_MODEL', reasons: validation.reasons };
  if (typeof penetration !== 'string' || !penetration.trim()) return { status: 'UNKNOWN_AP_LABEL', reasons: ['AP_LABEL_MISSING'] };
  if (typeof armor !== 'string' || !armor.trim()) return { status: 'UNKNOWN_ARMOR_LABEL', reasons: ['ARMOR_LABEL_MISSING'] };
  const ap = model.ap_values[penetration.trim()];
  const av = model.armor_values[armor.trim()];
  if (!finite(ap)) return { status: 'UNKNOWN_AP_LABEL', reasons: ['AP_LABEL_NOT_IN_MODEL'], label: penetration };
  if (!finite(av)) return { status: 'UNKNOWN_ARMOR_LABEL', reasons: ['ARMOR_LABEL_NOT_IN_MODEL'], label: armor };
  const relation = ap < av ? 'below' : ap === av ? 'equal' : 'greater';
  return {
    status: 'KNOWN',
    relation,
    multiplier: model.multipliers[relation],
    ap,
    av,
    model_state: model.state,
    source_refs: [...model.source_refs],
  };
}

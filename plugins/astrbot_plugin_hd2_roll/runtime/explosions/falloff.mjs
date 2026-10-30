// Explosion falloff between the inner and outer radius.
//
// Rule set (otl_falloff, 7.1.0 code study, second hand — see sources.mjs):
//   d <= inner                    -> full damage, AP unchanged          (curve independent)
//   inner < d < outer             -> one of two curves                 (curve required)
//   d == outer (and beyond)       -> zero damage                        (curve independent)
//   outside the inner radius      -> AP drops one step
//   shockwave ring                -> separate, never a damage circle
//
// The curves are parameterised, not sorted by guesswork: a caller that does not know
// the weapon's curve gets UNKNOWN_FALLOFF_CURVE for the between-radii band and no
// damage number. The distance itself is a claimed, evidenced input — this file does
// no geometry.

export const FALLOFF_CURVES = Object.freeze({
  standard: Object.freeze({
    id: 'standard',
    formula: '1 - sqrt(x), x = (d - r_in) / (r_out - r_in)',
    state: 'live_verified_7_1_0',
    source_ref: 'otl_falloff',
  }),
  wide: Object.freeze({
    id: 'wide',
    formula: '1 - x^2, x = (d - r_in) / (r_out - r_in)',
    state: 'pending',
    source_ref: 'otl_falloff',
    caveat: 'the research page still lists the wide curve as pending; a wide-curve number may be reported but never carries a sourced-verdict',
  }),
});

// A caller must declare the weapon's curve AND how that identity is known. The curve
// identity is not present in the equipment archive, so an absent declaration leaves the
// between-radii band unknown.
export function curveEvidence({ curve, state, source_refs }) {
  if (!FALLOFF_CURVES[curve]) return { status: 'UNKNOWN_DATA', reasons: ['CURVE_NOT_DECLARED'] };
  if (!['sourced', 'assumed'].includes(state)) return { status: 'UNKNOWN_DATA', reasons: ['CURVE_EVIDENCE_STATE_MISSING'] };
  if (state === 'sourced') {
    if (!Array.isArray(source_refs) || !source_refs.length) return { status: 'UNKNOWN_DATA', reasons: ['CURVE_SOURCE_REFS_MISSING'] };
    if (FALLOFF_CURVES[curve].state === 'pending') return { status: 'KNOWN_PENDING', curve, reasons: ['WIDE_CURVE_PENDING'] };
    return { status: 'KNOWN_SOURCED', curve, source_refs: [...source_refs] };
  }
  return { status: 'KNOWN_ASSUMED', curve, reasons: ['CURVE_IDENTITY_ASSUMED'] };
}

const finite = x => typeof x === 'number' && Number.isFinite(x);

// Returns one of
//   {status:'KNOWN', zone, multiplier, ap_delta}
//   {status:'OUT_OF_RADIUS', multiplier:0, ap_delta:null}
//   {status:'UNKNOWN_FALLOFF_CURVE', zone:'between_radii'}
//   {status:'UNKNOWN_DATA', reason}
export function falloff({ distance_m, inner_radius, outer_radius, curve = null }) {
  if (![distance_m, inner_radius, outer_radius].every(finite)) return { status: 'UNKNOWN_DATA', reason: 'RADIUS_OR_DISTANCE_NOT_FINITE' };
  if (distance_m < 0) return { status: 'UNKNOWN_DATA', reason: 'DISTANCE_NEGATIVE' };
  if (inner_radius < 0 || outer_radius < 0) return { status: 'UNKNOWN_DATA', reason: 'RADIUS_NEGATIVE' };
  if (outer_radius < inner_radius) return { status: 'UNKNOWN_DATA', reason: 'OUTER_BELOW_INNER' };
  if (distance_m > outer_radius) return { status: 'OUT_OF_RADIUS', multiplier: 0, ap_delta: null, zone: 'outside' };
  if (distance_m === outer_radius) return { status: 'KNOWN', zone: 'outer_edge', multiplier: 0, ap_delta: -1 };
  if (distance_m <= inner_radius) return { status: 'KNOWN', zone: 'inner', multiplier: 1, ap_delta: 0 };
  const x = (distance_m - inner_radius) / (outer_radius - inner_radius);
  if (curve === null || curve === undefined) return { status: 'UNKNOWN_FALLOFF_CURVE', zone: 'between_radii', ap_delta: -1, x };
  const spec = FALLOFF_CURVES[curve];
  if (!spec) return { status: 'UNKNOWN_DATA', reason: 'UNKNOWN_CURVE_ID', curve };
  const multiplier = curve === 'standard' ? 1 - Math.sqrt(x) : 1 - x * x;
  return { status: 'KNOWN', zone: 'between_radii', multiplier, ap_delta: -1, x, curve, curve_state: spec.state };
}

// Guard for the non-goal: a shockwave ring is not a damage circle.
// Returns null when the blast is a normal damage circle, or a rejection reason.
export function shockwaveGuard(blast) {
  if (!blast || typeof blast !== 'object') return 'BLAST_REQUIRED';
  if (blast.damage_kind === 'shockwave' || blast.damage_from_shockwave === true) return 'SHOCKWAVE_IS_NOT_A_DAMAGE_CIRCLE';
  if (blast.inner_radius === undefined && blast.shockwave_radius !== undefined) return 'SHOCKWAVE_RADIUS_IS_NOT_A_DAMAGE_RADIUS';
  return null;
}

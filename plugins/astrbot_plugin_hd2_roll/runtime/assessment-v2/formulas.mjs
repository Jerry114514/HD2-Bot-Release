// Rebuilt scoring primitives. No legacy scores, ratings or fitted weapon anchors
// are imported. All times are seconds; capacity and reference use the same unit.
// This is a candidate arithmetic layer, not an equipment calibration or pass gate.
const nonnegative = value => Number.isFinite(value) && value >= 0;
const positive = value => Number.isFinite(value) && value > 0;
const unknown = reason => ({status: 'UNKNOWN', value: null, reason});
const known = value => ({status: 'KNOWN', value});

export const REALIZATION = Object.freeze({
  concentrated_strike: 0.85,
  persistent_area: 0.70,
  random_barrage: 0.50,
  automatic_turret: 0.30,
  automatic_backpack: 0.45,
  manned_emplacement: 0.45,
  laser_turret: 0.10,
  gatling_scatter: 0.25,
  ordinary_mines: 0.20,
  gas_mines: 0.55,
  smoke: 0,
});

// Scope of each accepted bundled realization factor. A detailed route must not
// debit the same loss and then apply the full bundled coefficient again.
export const REALIZATION_LOSSES = Object.freeze({
  concentrated_strike: ['aim_reliability'], persistent_area: ['target_retention'],
  random_barrage: ['scatter_hit_rate'], automatic_turret: ['premature_destroy', 'terrain_obstruction'],
  automatic_backpack:['premature_destroy','terrain_obstruction'],manned_emplacement:['premature_destroy','terrain_obstruction'],
  gatling_scatter:['scatter_hit_rate'],
  laser_turret: ['premature_destroy', 'terrain_obstruction', 'overheat_self_destruction'],
  ordinary_mines: ['trigger_probability', 'target_retention'], gas_mines: ['trigger_probability'], smoke: [],
});

// Shape is an engineering candidate. Reference demand must be calibrated from
// a role scenario; a retired score divided by 60 is not a capacity ratio.
export function capacityCurve(ratio) {
  if (!nonnegative(ratio)) throw new TypeError('INVALID_CAPACITY_RATIO');
  // Gentler around the reference: both branches have slope 120 at r=1.
  // Reference remains 60; only ratios above 4 exceed 100; asymptote is 105.
  if (ratio <= 1) return 60 * ratio ** 2;
  return 60 + 45 * ((ratio - 1) / (ratio - 0.625));
}

export function coverageValue(input = {}) {
  const {family, duration_s: duration, cooldown_s: cooldown, delay_s: delay} = input;
  if (!['orbital', 'green', 'eagle'].includes(family)) return unknown('FAMILY_REQUIRED');
  if (family === 'eagle' && input.uses === 0) return known(0);
  if (!nonnegative(cooldown) || !nonnegative(delay)) return unknown('CD_OR_DELAY_REQUIRED');
  if (family === 'eagle') {
    const {uses, small_cd_s: gap, effective_work_s: work} = input;
    if (!Number.isSafeInteger(uses) || uses < 1 || !positive(gap) || !nonnegative(work)) {
      return unknown('EAGLE_USES_INTERVAL_OR_REFERENCE_REQUIRED');
    }
    // Value of finite work saved, not a free full-cohort reference window on
    // every pass. Calls are spent once; call-in and small CD overlap thereafter.
    const totalWork=uses*work,wait=cooldown+delay+(uses-1)*Math.max(gap,delay);
    return {...known(totalWork>0?totalWork/(totalWork+wait):0),
      total_effective_work_s:totalWork,unavailable_s:wait,basis:'FINITE_WORK_SAVED_SHARE; NOT_LITERAL_UPTIME'};
  }
  // An instantaneous strike with no defined coverage has duration=null, not 0.
  if (!nonnegative(duration)) return unknown('COVERAGE_DURATION_REQUIRED');
  if (duration === 0) return known(0);
  if (family === 'orbital') return known(duration / (duration + cooldown + delay));
  return known(duration / Math.max(duration, cooldown + delay));
}

export function strategicContribution(input = {}) {
  // Reject old API inputs explicitly, including a stale value of zero.
  if (['raw_score', 'score', 'legacy_score', 'strategic_multiplier'].some(key => Object.hasOwn(input, key))) {
    return unknown('LEGACY_SCORE_INPUT_FORBIDDEN');
  }
  const {capacity, coverage, realization_class: family, manual = false, operation_factor} = input;
  if (!Object.hasOwn(REALIZATION, family)) return unknown('REALIZATION_CLASS_REQUIRED');
  if (family === 'smoke') return {...known(0), reason: 'USER_SMOKE_ZERO'};
  const accounted = [...(capacity?.applied_losses ?? []), ...(coverage?.applied_losses ?? [])];
  if (accounted.some(loss => REALIZATION_LOSSES[family].includes(loss))) return unknown('REALIZATION_LOSS_ALREADY_APPLIED');
  if (!capacity || !nonnegative(capacity.available) || !positive(capacity.reference)
    || typeof capacity.unit !== 'string' || !capacity.unit.trim()
    || typeof capacity.reference_id !== 'string' || !capacity.reference_id.trim()) {
    return unknown('PHYSICAL_CAPACITY_AND_REFERENCE_REQUIRED');
  }
  const ratio = capacity.available / capacity.reference;
  if (!nonnegative(ratio)) return unknown('CAPACITY_RATIO_OUT_OF_RANGE');
  const availability = coverageValue(coverage);
  if (availability.status !== 'KNOWN') return availability;
  // No default 0.70: the extra manned-emplacement coefficient is still undecided.
  const operation = operation_factor ?? (manual ? null : 1);
  if (!nonnegative(operation) || operation > 1 || (!manual && operation !== 1)) {
    return unknown('OPERATION_FACTOR_REQUIRED_OR_INAPPLICABLE');
  }
  const bare = capacityCurve(ratio), realization = REALIZATION[family];
  return {...known(bare * availability.value * realization * operation),
    ratio, bare_score: bare, coverage_value: availability.value,
    realization_factor: realization, operation_factor: operation,
    calibration_status: 'FORMULA_CANDIDATE', not_a_loadout_verdict: true};
}

// Human operation model, not a reconstruction of the game's animation physics.
// Source ergonomics/recoil are unitless inputs; converting them to time requires
// an explicit engineering policy. No penalty changes damage, AP, ammunition or BTK.
const nonnegative = n => Number.isFinite(n) && n >= 0;
const positive = n => Number.isFinite(n) && n > 0;

export function handlingTiming(input, policy) {
  if (!input || !['ergonomics', 'recoil_horizontal', 'recoil_vertical'].every(k => nonnegative(input[k]))) {
    return {status: 'UNKNOWN', reason: 'HANDLING_SOURCE_FIELDS_REQUIRED'};
  }
  if (!policy || !positive(policy.reference_ergonomics) || !positive(policy.reference_acquire_ms)
    || !['low_ergo_gain', 'horizontal_ms_per_unit', 'vertical_ms_per_unit'].every(k => nonnegative(policy[k]))) {
    return {status: 'UNKNOWN', reason: 'HANDLING_POLICY_REQUIRED'};
  }
  const ergoDeficit = Math.max(0, (policy.reference_ergonomics - input.ergonomics) / policy.reference_ergonomics);
  return {status: 'ESTIMATE', calibration: 'ENGINEERING_PARAMETERS_NOT_MEASURED',
    acquire_ms: policy.reference_acquire_ms * (1 + policy.low_ergo_gain * ergoDeficit),
    recoil_settle_ms: input.recoil_horizontal * policy.horizontal_ms_per_unit
      + input.recoil_vertical * policy.vertical_ms_per_unit,
    source_fields: {...input}, policy: {...policy}};
}

// First neutralization matters more than being ready to repeat it, but slow
// reloading still lowers the repeat term. No universal two-second denominator.
// Weight is a visible scenario parameter; it is not inferred from old scores.
export function mediumResponse(service, {first_weight, rapid_reference_ms} = {}) {
  if (!(first_weight > 0 && first_weight < 1) || !positive(rapid_reference_ms)) {
    return {status: 'UNKNOWN', reason: 'MEDIUM_RESPONSE_POLICY_REQUIRED'};
  }
  if (service?.status !== 'ESTIMATE' || service.handling?.status !== 'ESTIMATE'
    || !positive(service.first_to_last_ms) || !positive(service.sustained?.pattern_ms) && service.sustained?.status !== 'FINITE_ONLY') {
    return {status: 'UNKNOWN', reason: 'POSITIVE_HANDLED_RESPONSE_TIME_REQUIRED'};
  }
  const first = service.effective_response_ms ?? service.first_to_last_ms;
  const repeat = service.sustained.status === 'FINITE_ONLY' ? Infinity : service.sustained.pattern_ms / service.sustained.targets_per_pattern;
  return {status: 'ESTIMATE', value: first_weight * 1000 / first + (1 - first_weight) * 1000 / repeat,
    unit: 'weighted_responses_per_second', first_weight,
    first_part_break_ms: service.first_part_break_ms ?? service.first_to_last_ms,
    last_shot_ms: service.first_to_last_ms,
    effective_response_ms: first,
    full_threat_resolution_ms: service.neutralized_at_ms ?? null,
    repeat_cycle_ms: Number.isFinite(repeat) ? repeat : null, repeat_available: Number.isFinite(repeat), recovery_after_ms: service.recovery_after_ms,
    one_shot: service.shots === 1, rapid_reference_ms, rapid: first <= rapid_reference_ms,
    repeat_basis: 'PERIODIC_FEED_TEMPO; BLEED_AND_STATUS_CAN_OVERLAP_RELOAD_AND_RETARGET',
    caveat: service.effective_response_ms !== undefined
      ? 'THREAT_EQUIVALENT_TIME; CONTROL_IS_NOT_DEATH_OR_GUARANTEED_SAFETY'
      : 'LETHAL_PART_BREAK_VALUE; FULL_THREAT_REMOVAL_NEEDS_RESIDUAL_THREAT_AND_CONTROL_EVIDENCE'};
}

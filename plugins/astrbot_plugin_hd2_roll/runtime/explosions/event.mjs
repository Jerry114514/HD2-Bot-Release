// One finite explosion event against a target part table.
//
// This is still not a geometry simulator: hits exist only as evidenced claims. What
// changed after review:
//   * verification_mode (all / outer_radius / none) is a LOS POLICY, line_of_sight
//     (clear / blocked / unknown) is an OCCLUSION RESULT. They are separate inputs and
//     a "none" policy no longer means "not hit".
//   * penetration uses an injected, sourced model; there is no built-in AP ladder and
//     no built-in equal-case or greater-case damage multiplier.
//   * the durable damage value comes from the equipment archive (inner_durable), so no
//     invented [0, normal] bracket is used.
//   * ExDR participates as an explicit parameter; without it the numbers are reported as
//     "before ExDR" and no verdict is issued.
//   * overflow handling binds to the per-part overflow_cap state and a supplied rule
//     table; unresolved means unresolved, not a fabricated bracket.

import { directDamage, finiteExplosion } from '../engine/damage.mjs';
import { falloff, shockwaveGuard, curveEvidence } from './falloff.mjs';
import { comparePenetration, validatePenetrationModel } from './penetration.mjs';
import { findPart, PART_ROLE } from './parts.mjs';
import { modeContradiction, policyFor } from './los-policy.mjs';

export const EVENT_STATUS = Object.freeze({
  INVALID_INPUT: 'INVALID_INPUT',
  UNKNOWN_DATA: 'UNKNOWN_DATA',
  UNKNOWN_COVERAGE: 'UNKNOWN_COVERAGE',
  CONDITIONAL_DETERMINATE: 'CONDITIONAL_DETERMINATE',
  CONDITIONAL_BOUNDED: 'CONDITIONAL_BOUNDED',
});

export const VERIFICATION_MODES = Object.freeze(['all', 'outer_radius', 'none']);
export const LOS_STATES = Object.freeze(['clear', 'blocked', 'unknown']);

const finite = x => typeof x === 'number' && Number.isFinite(x);

const BASE_ASSUMPTIONS = Object.freeze([
  'per-part model: local part damage first, then ceil(transferable * percent_to_main) summed (7.1.0 relayed rule)',
  'percent_to_main "-" means no transfer step, not 100%',
  'ordinary part panels are wiki snapshot rows; no measured hit list exists',
  'results are conditional and never certified (verified:false)',
]);

export function zoneOf({ distance_m, inner_radius, outer_radius }) {
  if (![distance_m, inner_radius, outer_radius].every(finite)) return null;
  if (distance_m > outer_radius) return 'outside';
  if (distance_m <= inner_radius) return 'inner';
  return 'outer';
}

// Verification policy per the relayed 7.1.0 table.
export function verificationRequired(mode, zone) {
  if (mode === 'all') return true;
  if (mode === 'none') return false;
  if (mode === 'outer_radius') return zone === 'outer';
  return null;
}

function validateBlast(blast) {
  const guard = shockwaveGuard(blast);
  if (guard) return { status: EVENT_STATUS.INVALID_INPUT, reason: guard };
  if (blast.include_ongoing === true) return { status: EVENT_STATUS.INVALID_INPUT, reason: 'ONGOING_DAMAGE_NOT_A_SINGLE_BLAST' };
  const normal = blast.damage?.normal;
  const durable = blast.damage?.durable;
  if (!finite(normal) || normal < 0) return { status: EVENT_STATUS.INVALID_INPUT, reason: 'BLAST_DAMAGE_INVALID' };
  const bounds = blast.durable_bounds;
  if (bounds) {
    if (!finite(bounds.min) || !finite(bounds.max) || bounds.min < 0 || bounds.max < bounds.min || !Array.isArray(bounds.source_refs) || !bounds.source_refs.length) {
      return { status: EVENT_STATUS.INVALID_INPUT, reason: 'DURABLE_BOUNDS_INVALID' };
    }
  } else if (!finite(durable) || durable < 0) {
    // The archive carries "Inner Durable"; without it or explicit sourced bounds the
    // per-part damage is unknown rather than bracketed by an invented maximum.
    return { status: EVENT_STATUS.UNKNOWN_DATA, reason: 'BLAST_DURABLE_UNKNOWN' };
  }
  if (![blast.inner_radius, blast.outer_radius].every(finite) || blast.inner_radius < 0 || blast.outer_radius < blast.inner_radius) {
    return { status: EVENT_STATUS.INVALID_INPUT, reason: 'BLAST_RADII_INVALID' };
  }
  if (typeof blast.pen_inner !== 'string' || !blast.pen_inner.trim()) return { status: EVENT_STATUS.UNKNOWN_DATA, reason: 'BLAST_PEN_INNER_MISSING' };
  return {
    status: 'READY',
    normal,
    durable_min: bounds ? bounds.min : durable,
    durable_max: bounds ? bounds.max : durable,
    bounded: !!bounds,
    inner_radius: blast.inner_radius,
    outer_radius: blast.outer_radius,
    pen_inner: blast.pen_inner,
    pen_outer: typeof blast.pen_outer === 'string' && blast.pen_outer.trim() ? blast.pen_outer : null,
    curve: blast.falloff_curve ?? null,
  };
}

function validateExdr(exdr) {
  if (exdr === undefined || exdr === null) return { status: 'MISSING', reasons: ['EXDR_NOT_PROVIDED'] };
  if (typeof exdr !== 'object' || !finite(exdr.multiplier) || exdr.multiplier < 0) return { status: 'INVALID', reasons: ['EXDR_INVALID'] };
  if (!['sourced', 'assumed'].includes(exdr.state)) return { status: 'INVALID', reasons: ['EXDR_EVIDENCE_STATE_MISSING'] };
  if (!Array.isArray(exdr.source_refs) || !exdr.source_refs.length) return { status: 'INVALID', reasons: ['EXDR_SOURCE_REFS_MISSING'] };
  return { status: 'KNOWN', state: exdr.state, multiplier: exdr.multiplier, source_refs: [...exdr.source_refs] };
}

function overflowRuleFor(capState, rules) {
  if (!rules || typeof rules !== 'object') return { status: 'UNKNOWN', reasons: ['OVERFLOW_RULES_NOT_PROVIDED'] };
  const entry = rules[capState];
  if (!entry) return { status: 'UNKNOWN', reasons: [`OVERFLOW_RULE_MISSING_FOR_${String(capState).toUpperCase()}`] };
  if (!['capped_at_remaining', 'uncapped'].includes(entry.rule)) return { status: 'UNKNOWN', reasons: ['OVERFLOW_RULE_VALUE_INVALID'] };
  if (!Array.isArray(entry.source_refs) || !entry.source_refs.length) return { status: 'UNKNOWN', reasons: ['OVERFLOW_RULE_SOURCE_MISSING'] };
  return { status: 'KNOWN', rule: entry.rule, state: entry.state || 'assumed', source_refs: [...entry.source_refs] };
}

function claimGate(claim, targetId) {
  if (!claim || typeof claim !== 'object' || typeof claim.part_id !== 'string' || !claim.part_id) return { kind: 'INVALID', reason: 'CLAIM_PART_ID_MISSING' };
  if (!VERIFICATION_MODES.includes(claim.verification_mode)) return { kind: 'COVERAGE', reason: 'VERIFICATION_MODE_MISSING' };
  if (!LOS_STATES.includes(claim.line_of_sight)) return { kind: 'COVERAGE', reason: 'LINE_OF_SIGHT_STATE_MISSING' };
  if (![true, false].includes(claim.receives_explosion)) return { kind: 'COVERAGE', reason: 'RECEPTION_NOT_EVIDENCED' };
  if (!Array.isArray(claim.evidence_refs) || !claim.evidence_refs.length) return { kind: 'COVERAGE', reason: 'CLAIM_EVIDENCE_MISSING' };
  if (!finite(claim.distance_m) || claim.distance_m < 0) return { kind: 'COVERAGE', reason: 'DISTANCE_NOT_EVIDENCED' };
  const contradiction = modeContradiction({ targetId, verification_mode: claim.verification_mode });
  if (contradiction) return { kind: 'INVALID', reason: contradiction };
  return null;
}

// Damage for one part under the injected model, before pool/transfer bookkeeping.
export function partBlastDamage({ blast, part, distance_m, model, exdr = null }) {
  const b = validateBlast(blast);
  if (b.status !== 'READY') return { status: b.status, reason: b.reason };
  const modelCheck = validatePenetrationModel(model);
  if (modelCheck.status !== 'KNOWN') return { status: EVENT_STATUS.UNKNOWN_DATA, reasons: modelCheck.reasons, reason: 'PENETRATION_MODEL_UNKNOWN' };
  const exdrCheck = validateExdr(exdr);
  if (exdr !== null && exdrCheck.status === 'INVALID') return { status: EVENT_STATUS.UNKNOWN_DATA, reasons: exdrCheck.reasons, reason: 'EXDR_INVALID' };
  if (!part || typeof part !== 'object' || typeof part.armor_text !== 'string') return { status: EVENT_STATUS.UNKNOWN_DATA, reason: 'PART_ARMOR_MISSING' };
  const zone = zoneOf({ distance_m, inner_radius: b.inner_radius, outer_radius: b.outer_radius });
  if (zone === null) return { status: EVENT_STATUS.INVALID_INPUT, reason: 'DISTANCE_OR_RADIUS_INVALID' };
  if (zone === 'outside') return { status: 'KNOWN', zone, damage: { min: 0, max: 0 }, out_of_radius: true };
  let multiplier = 1;
  let curveState = null;
  if (zone === 'outer') {
    const evidence = curveEvidence({ curve: b.curve, state: blast.falloff_evidence?.state, source_refs: blast.falloff_evidence?.source_refs });
    if (evidence.status === 'UNKNOWN_DATA') return { status: EVENT_STATUS.UNKNOWN_DATA, reasons: evidence.reasons, reason: 'FALLOFF_CURVE_UNKNOWN', zone };
    curveState = evidence.status;
    const fo = falloff({ distance_m, inner_radius: b.inner_radius, outer_radius: b.outer_radius, curve: b.curve });
    if (fo.status !== 'KNOWN') return { status: EVENT_STATUS.UNKNOWN_DATA, reasons: [fo.reason || fo.status], reason: fo.status, zone };
    multiplier = fo.multiplier;
    if (multiplier === 0) return { status: 'KNOWN', zone, damage: { min: 0, max: 0 }, zero_at_outer_edge: true, curve_state: curveState };
  }
  const penLabel = zone === 'inner' ? b.pen_inner : b.pen_outer;
  if (!penLabel) return { status: EVENT_STATUS.UNKNOWN_DATA, reason: 'BLAST_PEN_OUTER_MISSING', zone };
  const pen = comparePenetration({ penetration: penLabel, armor: part.armor_text, model });
  if (pen.status !== 'KNOWN') return { status: EVENT_STATUS.UNKNOWN_DATA, reasons: pen.reasons, reason: pen.status, zone };
  const durability = part.durable && part.durable.status === 'KNOWN' ? part.durable.fraction : null;
  if (durability === null) return { status: EVENT_STATUS.UNKNOWN_DATA, reason: 'PART_DURABLE_UNKNOWN', zone };
  const exdrMultiplier = exdrCheck.status === 'KNOWN' ? exdrCheck.multiplier : 1;
  const base = {
    normal: b.normal,
    durability,
    penetration: pen.ap,
    armor: pen.av,
    equal_multiplier: model.multipliers.equal,
    greater_multiplier: model.multipliers.greater,
    falloff: multiplier,
    multiplier: exdrMultiplier,
  };
  const min = directDamage({ ...base, durable: b.durable_min });
  const max = directDamage({ ...base, durable: b.durable_max });
  if (min.status !== 'KNOWN' || max.status !== 'KNOWN') return { status: EVENT_STATUS.UNKNOWN_DATA, reason: 'DAMAGE_FORMULA_REJECTED', zone };
  return {
    status: 'KNOWN',
    zone,
    damage: { min: min.damage, max: max.damage },
    pen_label: penLabel,
    armor_label: part.armor_text,
    relation: pen.relation,
    ap: pen.ap,
    av: pen.av,
    model_state: pen.model_state,
    falloff_multiplier: multiplier,
    curve_state: curveState,
    exdr_applied: exdrCheck.status === 'KNOWN',
    bounded: b.bounded,
  };
}

export function resolveExplosionEvent({ blast, target, claims, options = {} }) {
  const reasons = [];
  const assumptions = [...BASE_ASSUMPTIONS];
  const b = validateBlast(blast);
  if (b.status !== 'READY') return { status: b.status, reasons: [b.reason], target_id: target?.id ?? null, verified: false };
  if (!target || target.status !== 'KNOWN') return { status: EVENT_STATUS.INVALID_INPUT, reasons: ['TARGET_INVALID'], target_id: null, verified: false };
  if (!Array.isArray(claims) || !claims.length) {
    return {
      status: EVENT_STATUS.UNKNOWN_COVERAGE,
      reasons: ['NO_HIT_CLAIMS', 'RADIUS_ALONE_IS_NOT_COVERAGE'],
      target_id: target.id,
      parts: [],
      verified: false,
    };
  }
  const seen = new Set();
  for (const claim of claims) {
    const key = `${claim?.part_id}#${Number.isInteger(claim?.instance) ? claim.instance : 0}`;
    if (seen.has(key)) return { status: EVENT_STATUS.INVALID_INPUT, reasons: ['DUPLICATE_PART_CLAIM'], duplicate: key, target_id: target.id, verified: false };
    seen.add(key);
  }

  const modelCheck = validatePenetrationModel(options.penetration_model);
  const exdrCheck = validateExdr(options.exdr);
  const policy = policyFor(target.id);
  if (policy.status === 'KNOWN') assumptions.push(`target LOS policy: ${policy.restriction} (${LOS_LABEL(policy)})`);

  const parts = [];
  const transferHits = [];
  const evidenceRefs = [];
  let coverageComplete = true;
  let coverageHole = false;
  let dataComplete = modelCheck.status === 'KNOWN';
  let pendingEvidence = false;
  let fatalCertain = false;
  let fatalPossible = false;
  let fatalUnknown = false;
  let mainDirect = { min: 0, max: 0 };
  const mainPool = target.main && target.main.health.status === 'KNOWN' ? target.main.health.value : null;
  if (target.main && target.main.health.status !== 'KNOWN') { fatalUnknown = true; reasons.push('MAIN_POOL_UNKNOWN'); }
  if (modelCheck.status !== 'KNOWN') reasons.push(...modelCheck.reasons);
  if (exdrCheck.status !== 'KNOWN') reasons.push(...(exdrCheck.reasons || ['EXDR_NOT_PROVIDED']));

  for (const claim of claims) {
    const gate = claimGate(claim, target.id);
    if (gate) {
      if (gate.kind === 'INVALID') return { status: EVENT_STATUS.INVALID_INPUT, reasons: [gate.reason], target_id: target.id, verified: false, certification: 'not_certified' };
      coverageComplete = false;
      coverageHole = true;
      reasons.push(gate.reason);
      parts.push({ part_id: claim?.part_id ?? null, instance: Number.isInteger(claim?.instance) ? claim.instance : 0, state: 'UNRESOLVED', reason: gate.reason });
      continue;
    }
    const lookup = findPart(target, claim.part_id, Number.isInteger(claim.instance) ? claim.instance : 0);
    if (!lookup) {
      coverageComplete = false;
      coverageHole = true;
      reasons.push('PART_NOT_IN_TARGET');
      parts.push({ part_id: claim.part_id, instance: claim.instance ?? 0, state: 'UNRESOLVED', reason: 'PART_NOT_IN_TARGET' });
      continue;
    }
    if (lookup.invalid_instance) {
      return { status: EVENT_STATUS.INVALID_INPUT, reasons: ['PART_INSTANCE_OUT_OF_RANGE'], target_id: target.id, part_id: claim.part_id, declared_instances: lookup.declared_instances, verified: false };
    }
    evidenceRefs.push(...claim.evidence_refs);
    const zone = zoneOf({ distance_m: claim.distance_m, inner_radius: b.inner_radius, outer_radius: b.outer_radius });
    const needsVerification = verificationRequired(claim.verification_mode, zone);
    const base = { part_id: claim.part_id, instance: lookup.instance, zone, verification_mode: claim.verification_mode, line_of_sight: claim.line_of_sight };
    if (zone === 'outside') {
      parts.push({ ...base, state: 'NOT_HIT', reason: 'OUTSIDE_OUTER_RADIUS' });
      continue;
    }
    if (claim.receives_explosion === false) {
      parts.push({ ...base, state: 'NOT_HIT', reason: 'RECEPTION_FALSE' });
      continue;
    }
    if (needsVerification === null) {
      coverageComplete = false;
      reasons.push('VERIFICATION_MODE_UNKNOWN_ZONE');
      parts.push({ ...base, state: 'UNRESOLVED', reason: 'VERIFICATION_MODE_UNKNOWN_ZONE' });
      continue;
    }
    if (needsVerification && claim.line_of_sight === 'blocked') {
      parts.push({ ...base, state: 'NOT_HIT', reason: 'LOS_BLOCKED_VERIFIED' });
      continue;
    }
    if (needsVerification && claim.line_of_sight === 'unknown') {
      coverageComplete = false;
      coverageHole = true;
      reasons.push('LOS_VERIFICATION_MISSING');
      parts.push({ ...base, state: 'UNRESOLVED', reason: 'LOS_VERIFICATION_MISSING' });
      continue;
    }
    if (!needsVerification && claim.line_of_sight !== 'clear') {
      // Policy skips verification in this zone: an occlusion result is recorded but does
      // not decide the hit, and the policy note stays visible.
      assumptions.push(`verification skipped in ${zone} zone (${claim.verification_mode}); LOS state "${claim.line_of_sight}" recorded, not used to exclude`);
      base.policy_skip_note = `LOS_${claim.line_of_sight.toUpperCase()}_ALLOWED_BY_VERIFICATION_POLICY`;
    }
    const part = lookup.part;
    const dealt = partBlastDamage({ blast, part, distance_m: claim.distance_m, model: options.penetration_model, exdr: options.exdr ?? null });
    if (dealt.status !== 'KNOWN') {
      if (dealt.status === EVENT_STATUS.UNKNOWN_DATA) dataComplete = false;
      else coverageHole = true;
      reasons.push(dealt.reason || 'PART_DAMAGE_UNRESOLVED');
      parts.push({ ...base, state: 'UNRESOLVED', reason: dealt.reason });
      continue;
    }
    if (dealt.damage.min !== dealt.damage.max) assumptions.push('per-part damage uses explicit sourced durable bounds');
    if (dealt.curve_state && dealt.curve_state !== 'KNOWN_SOURCED') {
      pendingEvidence = true;
      assumptions.push(`falloff curve identity ${dealt.curve_state === 'KNOWN_PENDING' ? 'pending (wide)' : 'assumed'}`);
    }
    if (!dealt.exdr_applied) assumptions.push('damage reported before ExDR; no verdict is issued without it');

    const record = { ...base, state: 'HIT', role: part.role, damage: dealt.damage, relation: dealt.relation, armor_label: dealt.armor_label, pen_label: dealt.pen_label, falloff_multiplier: dealt.falloff_multiplier, exdr_applied: dealt.exdr_applied };

    if (part.role === PART_ROLE.MAIN) {
      mainDirect = { min: mainDirect.min + dealt.damage.min, max: mainDirect.max + dealt.damage.max };
      if (part.fatal === true && mainPool !== null) {
        if (dealt.damage.min >= mainPool) fatalCertain = true;
        if (dealt.damage.max >= mainPool) fatalPossible = true;
      }
      parts.push(record);
      continue;
    }
    if (part.role === PART_ROLE.DETACHED) {
      if (part.health.status === 'KNOWN') {
        const pool = part.health.value;
        record.pool = pool;
        record.destroyed = dealt.damage.min >= pool ? 'certain' : dealt.damage.max >= pool ? 'possible' : 'no';
        if (part.fatal === true) {
          if (record.destroyed === 'certain') fatalCertain = true;
          if (record.destroyed === 'possible') fatalPossible = true;
        }
      } else {
        record.pool = null;
        if (part.fatal === true) fatalUnknown = true;
      }
      parts.push(record);
      continue;
    }
    if (part.health.status !== 'KNOWN') {
      dataComplete = false;
      reasons.push('PART_POOL_UNKNOWN');
      parts.push({ ...record, state: 'UNRESOLVED', reason: 'PART_POOL_UNKNOWN' });
      if (part.fatal === true) fatalUnknown = true;
      continue;
    }
    if (part.percent_to_main.status !== 'KNOWN') {
      dataComplete = false;
      reasons.push('MAIN_MULTIPLIER_UNKNOWN');
      parts.push({ ...record, state: 'UNRESOLVED', reason: 'MAIN_MULTIPLIER_UNKNOWN' });
      if (part.fatal === true) fatalUnknown = true;
      continue;
    }
    const pool = part.health.value;
    const multiplier = part.percent_to_main.fraction;
    record.pool = pool;
    record.main_multiplier = multiplier;
    record.overflow_cap = part.overflow_cap.state;
    const overflowing = dealt.damage.max > pool;
    record.overflow = { possible: overflowing, certain: dealt.damage.min > pool };
    if (part.fatal === true) {
      if (dealt.damage.min >= pool) fatalCertain = true;
      if (dealt.damage.max >= pool) fatalPossible = true;
    }
    if (!overflowing) {
      record.transfer = { min: Math.ceil(dealt.damage.min * multiplier), max: Math.ceil(dealt.damage.max * multiplier) };
    } else {
      const rule = overflowRuleFor(part.overflow_cap.state, options.overflow_rules);
      record.overflow_rule = rule.status === 'KNOWN' ? { rule: rule.rule, state: rule.state, source_refs: rule.source_refs } : { state: 'UNKNOWN', reasons: rule.reasons };
      record.transfer_candidates = {
        capped_at_remaining: Math.ceil(Math.min(dealt.damage.max, pool) * multiplier),
        uncapped: Math.ceil(dealt.damage.max * multiplier),
      };
      if (rule.status !== 'KNOWN') {
        dataComplete = false;
        reasons.push(...rule.reasons);
        parts.push({ ...record, state: 'UNRESOLVED', reason: 'OVERFLOW_RULE_UNRESOLVED' });
        if (part.fatal === true) fatalUnknown = true;
        continue;
      }
      const capped = rule.rule === 'capped_at_remaining';
      record.transfer = {
        min: Math.ceil((capped ? Math.min(dealt.damage.min, pool) : dealt.damage.min) * multiplier),
        max: Math.ceil((capped ? Math.min(dealt.damage.max, pool) : dealt.damage.max) * multiplier),
      };
      assumptions.push(`overflow rule for "${part.overflow_cap.state}" = ${rule.rule} (${rule.state})`);
    }
    transferHits.push({ part_id: claim.part_id, instance: lookup.instance, damage: record.transfer });
    parts.push(record);
  }

  const sumWith = basis => {
    const hits = transferHits
      .filter(hit => hit.damage[basis] !== undefined)
      .map(hit => ({ part_id: `${hit.part_id}#${hit.instance}`, damage: Math.max(0, Math.ceil(hit.damage[basis])) }));
    if (!hits.length) return 0;
    const result = finiteExplosion(hits, { coverage_verified: true, transfer: 'sum', evidence_refs: evidenceRefs.length ? evidenceRefs : ['explosions:event'] });
    if (result.status !== 'KNOWN') return null;
    return result.main_damage;
  };
  const transferMin = sumWith('min');
  const transferMax = sumWith('max');
  if (transferMin === null || transferMax === null) {
    return { status: EVENT_STATUS.INVALID_INPUT, reasons: ['TRANSFER_AGGREGATION_REJECTED'], target_id: target.id, verified: false };
  }
  const mainDamage = { min: transferMin + mainDirect.min, max: transferMax + mainDirect.max };
  const numericBounded = mainDamage.min !== mainDamage.max || b.bounded;

  const coverageIncomplete = coverageHole || !coverageComplete;
  let status;
  if (coverageIncomplete) status = EVENT_STATUS.UNKNOWN_COVERAGE;
  else if (!dataComplete) status = EVENT_STATUS.UNKNOWN_DATA;
  else status = numericBounded ? EVENT_STATUS.CONDITIONAL_BOUNDED : EVENT_STATUS.CONDITIONAL_DETERMINATE;

  const numeric = status === EVENT_STATUS.CONDITIONAL_DETERMINATE || status === EVENT_STATUS.CONDITIONAL_BOUNDED;
  const conclusionReady = numeric && exdrCheck.status === 'KNOWN';
  // Every overflow rule actually applied with a non-sourced evidence state keeps the
  // whole event at assumed_inputs; a sourced model + ExDR alone must not hide it.
  const assumedOverflowRules = parts.some(record => record.overflow_rule && record.overflow_rule.rule && record.overflow_rule.state !== 'sourced');
  const evidenceGrade = !conclusionReady ? 'incomplete_inputs'
    : (modelCheck.state === 'sourced' && exdrCheck.state === 'sourced' && !pendingEvidence && !assumedOverflowRules) ? 'sourced_inputs' : 'assumed_inputs';

  return {
    status,
    target_id: target.id,
    main_pool: mainPool,
    main_damage: mainDamage,
    main_direct: mainDirect,
    transfer_sum: { min: transferMin, max: transferMax },
    parts,
    bracket_parts: parts.filter(record => record.damage && record.damage.min !== record.damage.max).length,
    fatal_certain: fatalCertain,
    fatal_possible: fatalPossible,
    fatal_unknown: fatalUnknown || undefined,
    reasons: [...new Set(reasons)],
    assumptions: [...new Set(assumptions)],
    inputs: {
      penetration_model: modelCheck.status === 'KNOWN' ? modelCheck.state : 'missing',
      exdr: exdrCheck.status === 'KNOWN' ? exdrCheck.state : 'missing',
      overflow_rules: options.overflow_rules ? 'provided' : 'missing',
      los_policy: policy.status === 'KNOWN' ? policy.restriction : 'unknown',
    },
    conclusion_ready: conclusionReady,
    evidence_grade: evidenceGrade,
    evidence_refs: [...new Set(evidenceRefs)],
    verified: false,
    certification: 'not_certified',
    termination: 'unknown_residual_threat',
  };
}

function LOS_LABEL(policy) {
  return `all=${policy.counts.all}, outer_radius=${policy.counts.outer_radius}, fallback=${policy.counts.fallback}`;
}

// Fallback / default zone: one application to main health; never added to part transfers.
export function resolveDefaultZoneEvent({ blast, target, claim, options = {} }) {
  if (options.also_claimed_parts) return { status: EVENT_STATUS.INVALID_INPUT, reasons: ['FALLBACK_PLUS_PARTS_DOUBLE_COUNT'], target_id: target?.id ?? null, verified: false };
  const gate = claimGate(claim, target?.id ?? null);
  if (gate) return { status: EVENT_STATUS.UNKNOWN_COVERAGE, reasons: [gate, 'FALLBACK_USE_NOT_EVIDENCED'], target_id: target?.id ?? null, verified: false };
  if (!target || target.status !== 'KNOWN') return { status: EVENT_STATUS.INVALID_INPUT, reasons: ['TARGET_INVALID'], target_id: null, verified: false };
  const b = validateBlast(blast);
  if (b.status !== 'READY') return { status: b.status, reasons: [b.reason], target_id: target.id, verified: false };
  const zone = zoneOf({ distance_m: claim.distance_m, inner_radius: b.inner_radius, outer_radius: b.outer_radius });
  if (zone === 'outside') return { status: EVENT_STATUS.UNKNOWN_COVERAGE, reasons: ['FALLBACK_OUTSIDE_RADIUS'], target_id: target.id, verified: false };
  // An explicit receives_explosion:false is evidence the target does NOT take this
  // fallback application: no damage number, no verdict, never a kill.
  if (claim.receives_explosion === false) {
    return { status: EVENT_STATUS.UNKNOWN_COVERAGE, reasons: ['RECEPTION_FALSE', 'FALLBACK_NOT_A_HIT'], target_id: target.id, verified: false, certification: 'not_certified' };
  }
  const needsVerification = verificationRequired(claim.verification_mode, zone);
  if (needsVerification && claim.line_of_sight === 'blocked') return { status: EVENT_STATUS.UNKNOWN_COVERAGE, reasons: ['LOS_BLOCKED_VERIFIED'], target_id: target.id, verified: false };
  if (needsVerification && claim.line_of_sight === 'unknown') return { status: EVENT_STATUS.UNKNOWN_COVERAGE, reasons: ['LOS_VERIFICATION_MISSING'], target_id: target.id, verified: false };
  let main = target.main && target.main.health.status === 'KNOWN' ? target.main : null;
  if (!main) {
    const supplied = options.main_pool;
    if (!supplied || typeof supplied !== 'object' || !finite(supplied.value) || supplied.value <= 0) {
      return { status: EVENT_STATUS.UNKNOWN_DATA, reasons: ['FALLBACK_MAIN_POOL_UNKNOWN'], target_id: target.id, verified: false };
    }
    if (!Array.isArray(supplied.evidence_refs) || !supplied.evidence_refs.length) {
      return { status: EVENT_STATUS.UNKNOWN_DATA, reasons: ['FALLBACK_MAIN_POOL_EVIDENCE_MISSING'], target_id: target.id, verified: false };
    }
    main = {
      health: { status: 'KNOWN', value: supplied.value },
      armor_text: typeof supplied.armor_text === 'string' ? supplied.armor_text : null,
      durable: finite(supplied.durable_fraction) ? { status: 'KNOWN', fraction: supplied.durable_fraction } : { status: 'UNKNOWN' },
    };
  }
  const armorPart = main.armor_text ? main : (options.default_zone_armor_text ? { armor_text: options.default_zone_armor_text, durable: main.durable, health: main.health } : null);
  if (!armorPart) return { status: EVENT_STATUS.UNKNOWN_DATA, reasons: ['FALLBACK_ARMOR_UNKNOWN'], target_id: target.id, verified: false };
  const dealt = partBlastDamage({ blast, part: armorPart, distance_m: claim.distance_m, model: options.penetration_model, exdr: options.exdr ?? null });
  if (dealt.status !== 'KNOWN') return { status: dealt.status, reasons: [dealt.reason || 'FALLBACK_DAMAGE_UNRESOLVED'], target_id: target.id, verified: false };
  const exdrCheck = validateExdr(options.exdr);
  const numericBounded = dealt.damage.min !== dealt.damage.max;
  const conclusionReady = exdrCheck.status === 'KNOWN';
  return {
    status: numericBounded ? EVENT_STATUS.CONDITIONAL_BOUNDED : EVENT_STATUS.CONDITIONAL_DETERMINATE,
    path: 'default_zone_single_application',
    target_id: target.id,
    main_pool: main.health.value,
    main_damage: { ...dealt.damage },
    parts: [{ part_id: 'main', state: 'FALLBACK_SINGLE_APPLICATION', damage: dealt.damage, relation: dealt.relation }],
    fatal_certain: dealt.damage.min >= main.health.value,
    fatal_possible: dealt.damage.max >= main.health.value,
    reasons: [],
    assumptions: ['fallback / default zone applied once and not added to part transfers', `LOS verification mode ${claim.verification_mode}, LOS state ${claim.line_of_sight}`],
    inputs: { penetration_model: dealt.model_state, exdr: exdrCheck.status === 'KNOWN' ? exdrCheck.state : 'missing' },
    conclusion_ready: conclusionReady,
    evidence_grade: conclusionReady ? (dealt.model_state === 'sourced' && exdrCheck.state === 'sourced' ? 'sourced_inputs' : 'assumed_inputs') : 'incomplete_inputs',
    evidence_refs: [...claim.evidence_refs],
    verified: false,
    certification: 'not_certified',
    termination: 'unknown_residual_threat',
  };
}

// Three-state verdict. Verdicts are only issued when the numeric inputs are complete
// (model, ExDR, resolved coverage); they are labelled with their evidence grade and are
// explicitly relative to the current assumptions, never a global or certified result.
export function oneShotVerdict(event) {
  if (!event || !event.status) return { state: 'unknown_evidence', reasons: ['NO_EVENT'], evidence_grade: 'incomplete_inputs', global: false, certification: 'not_certified' };
  if (event.status === EVENT_STATUS.INVALID_INPUT || event.status === EVENT_STATUS.UNKNOWN_DATA || event.status === EVENT_STATUS.UNKNOWN_COVERAGE) {
    return {
      state: 'unknown_evidence',
      reasons: event.reasons?.length ? event.reasons : ['EVENT_UNRESOLVED'],
      evidence_grade: 'incomplete_inputs',
      global: false,
      certification: 'not_certified',
    };
  }
  if (!event.conclusion_ready) {
    return { state: 'unknown_evidence', reasons: event.reasons?.length ? event.reasons : ['CONCLUSION_INPUTS_INCOMPLETE'], evidence_grade: 'incomplete_inputs', global: false, certification: 'not_certified' };
  }
  const relative = { assumptions: event.assumptions, evidence_refs: event.evidence_refs, target_id: event.target_id };
  if (event.fatal_certain) return { state: 'conditional_achieved', basis: 'fatal_part_destroyed', evidence_grade: event.evidence_grade, relative_to: relative, global: false, certification: 'not_certified' };
  if (event.main_pool !== null && event.main_damage.min >= event.main_pool) {
    return { state: 'conditional_achieved', basis: 'main_pool', evidence_grade: event.evidence_grade, relative_to: relative, global: false, certification: 'not_certified' };
  }
  const noFatalLeft = !event.fatal_possible && !event.fatal_unknown;
  if (event.main_pool !== null && event.main_damage.max < event.main_pool && noFatalLeft) {
    return { state: 'not_achieved', basis: 'best_case_below_required', evidence_grade: event.evidence_grade, relative_to: relative, global: false, certification: 'not_certified' };
  }
  return { state: 'unknown_evidence', reasons: ['BRACKET_STRADDLES_REQUIRED_DAMAGE'], evidence_grade: event.evidence_grade, relative_to: relative, global: false, certification: 'not_certified' };
}

// Score summary after physical route/resource evaluation. No parallel summation
// of handheld combat rates; heavy/demolition final scores add by user rule.
import {contributionScore} from './score-scale.mjs';
const defaultRoles = ['chaff', 'close_defense', 'anti_medium'];
const allowedRoles=[...defaultRoles,'anti_heavy','endurance','demolition'];
const requiredGates = ['legality', 'heavy_pair', 'demolition', 'shared_resources', 'close_safety'];
const nn = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;

// Preserve the pass scale through 100. Compress only excess display points.
// This is NOT a limit on summed red/green contributions or a physical capacity.
export function displayScore(value) {
  if (!nn(value)) throw new TypeError('INVALID_AGGREGATE_SCORE');
  return value <= 100 ? value : 100 + 5 * (value - 100) / (value - 95);
}

export function summarizeLoadout({ordinary = [], strategic = [], gates = {},
  thresholds, coverage_complete = {}, calibration_ready = false,score_scale} = {}) {
  const roles=thresholds?Object.keys(thresholds):defaultRoles;
  if (!thresholds ||defaultRoles.some(r=>!Object.hasOwn(thresholds,r))||roles.some(role => !allowedRoles.includes(role)||thresholds[role] !== null && !nn(thresholds[role]))) {
    return {status: 'UNKNOWN', reason: 'ROLE_THRESHOLDS_REQUIRED'};
  }
  const seen = new Set();
  for (const [kind, rows] of [['ordinary', ordinary], ['strategic', strategic]]) for (const row of rows) {
    if (!row.equipment_id || !roles.includes(row.role)) return {status: 'UNKNOWN', reason: 'ROLE_EQUIPMENT_ID_REQUIRED'};
    const key = `${row.equipment_id}:${row.role}`;
    if (seen.has(key)) return {status: 'UNKNOWN', reason: 'DUPLICATE_EQUIPMENT_ROLE'};
    seen.add(key);
    const value = kind === 'ordinary' ? row.score : row.contribution?.value;
    if (value != null && !nn(value)) return {status: 'UNKNOWN', reason: 'INVALID_CONTRIBUTION'};
  }
  ordinary=ordinary.map(r=>({...r,original_score:r.score,score:contributionScore(r.score,r.score_origin,score_scale)}));
  strategic=strategic.map(r=>({...r,contribution:{...r.contribution,original_value:r.contribution?.value,
    value:contributionScore(r.contribution?.value,r.contribution?.score_origin,score_scale)}}));
  const dimensions = {};
  for (const role of roles) {
    const measured = ordinary.filter(x => x.role === role && nn(x.score)
      && ['ENGINEERING_ESTIMATE', 'KNOWN_ZERO','MANUAL_CALIBRATED'].includes(x.state)).sort((a, b) => b.score - a.score);
    const additions = strategic.filter(x => x.role === role && x.contribution?.status === 'KNOWN' && nn(x.contribution.value));
    const sumRole=['anti_heavy','demolition'].includes(role);
    const base = sumRole?measured.reduce((n,x)=>n+x.score,0):measured[0]?.score ?? 0;
    const bonus = additions.reduce((n, x) => n + x.contribution.value, 0), total = base + bonus;
    const threshold = thresholds[role];
    const complete = coverage_complete[role] === true;
    // Missing tools may improve a lower bound. Failing a lower bound is not a
    // proof of failure, but meeting it still requires the physical gates below.
    dimensions[role] = {base_score: base, selected_ordinary: sumRole?null:measured[0]?.equipment_id ?? null,
      ...(defaultRoles.includes(role)?{}:{scope:sumRole?'USER_SUM_FINAL_ITEM_SCORES; JOINT_PHYSICAL_GATE_IS_SEPARATE':'SINGLE_CAPABILITY_PLUS_ADDITIONS; JOINT_PHYSICAL_GATE_IS_SEPARATE'}),
      ...(sumRole?{aggregation:'SUM',ordinary_contributions:measured.map(x=>({equipment_id:x.equipment_id,score:x.score}))}:{}),
      auxiliary_bonus: bonus,
      strategic_bonus: additions.filter(x=>x.contribution.source!=='GRENADE_REFILL_AUXILIARY').reduce((n,x)=>n+x.contribution.value,0),
      grenade_bonus: additions.filter(x=>x.contribution.source==='GRENADE_REFILL_AUXILIARY').reduce((n,x)=>n+x.contribution.value,0),
      contributions: additions.map(x => ({equipment_id: x.equipment_id, ...x.contribution})),
      total_before_display: total, score: sumRole?total:displayScore(total), lower_bound: !complete,
      threshold};
  }
  const gateStates = Object.fromEntries(requiredGates.map(id => [id, ['PASS', 'FAIL'].includes(gates[id]?.status) ? gates[id].status : 'UNKNOWN']));
  return finalizeAssessmentSummary({dimensions, gates: gateStates, score_scale:score_scale??null,
    scoring_rule: 'BEST_EXECUTABLE_ORDINARY_PLUS_CD_AND_REFILL_WEIGHTED_AUXILIARIES; HEAVY_AND_DEMOLITION_SUM_FINAL_SCORES; SHARED_PHYSICAL_GATES',
    scope: 'INITIAL_DRAW_ONLY; POST_OUTPUT_REPLACEMENT_MUST_NOT_CALL_EVALUATOR'}, calibration_ready);
}

// Re-run after final score adjustments (notably the supply endurance floor).
// Admission must agree with the six final displayed scores, not an earlier subtotal.
export function finalizeAssessmentSummary(summary, calibration_ready = false) {
  const dimensions = Object.fromEntries(Object.entries(summary.dimensions).map(([role, dimension]) => {
    const {score, threshold} = dimension;
    // Only tolerate floating-point addition error at the exact threshold.
    const meetsThreshold = threshold !== null && nn(score) && (score >= threshold || threshold - score <=
      Number.EPSILON * Math.max(1, threshold, score) * ((dimension.contributions?.length ?? 0) + 2));
    return [role, {...dimension, status: threshold === null ? 'ADVISORY' : meetsThreshold ? 'PASS'
      : dimension.lower_bound ? 'UNKNOWN' : 'FAIL'}];
  }));
  const gateStates = summary.gates;
  const failed = [...Object.entries(gateStates).filter(([, s]) => s === 'FAIL').map(([id]) => id),
    ...Object.entries(dimensions).filter(([, d]) => d.status === 'FAIL').map(([id]) => id)];
  const missing = [...Object.entries(gateStates).filter(([, s]) => s === 'UNKNOWN').map(([id]) => id),
    ...Object.entries(dimensions).filter(([, d]) => d.status === 'UNKNOWN').map(([id]) => id)];
  const numericStatus = failed.length ? 'FAIL' : missing.length ? 'UNKNOWN' : 'PASS';
  return {...summary, status: failed.includes('legality') ? 'FAIL' : calibration_ready ? numericStatus
    : numericStatus === 'UNKNOWN' ? 'UNKNOWN' : `ESTIMATED_${numericStatus}`,
    numeric_status: numericStatus, dimensions, gates: gateStates, failed, missing,
    eligible_for_draw: calibration_ready && numericStatus === 'PASS'};
}

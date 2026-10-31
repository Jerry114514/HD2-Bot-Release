import {legality, equipmentIDs, canOccupy, withinExosuitLimit} from '../engine/common.mjs';
import {assessMixedEndurance, assessHeavyPairs} from './engagement.mjs';
import {assessFiveHoles} from './demolition.mjs';
import {summarizeLoadout,finalizeAssessmentSummary} from './aggregate.mjs';
import {capacityCurve} from './formulas.mjs';
import {assessStrategic} from './strategic.mjs';
import {suppliedGrenadeAdditions} from './grenade-chaff.mjs';
import {assessAutomatic} from './automatic.mjs';
import {manualValue,contributionKind} from './manual-scores.mjs';
import {originalScore,contributionScore} from './score-scale.mjs';
import {assessLoadoutEndurance,applySupplyEnduranceFloor} from './loadout-endurance.mjs';
import {isMeleeEquipment} from './score-exclusions.mjs';

export function assessmentLegality(loadout, catalog, model, options) {
  const cards = legality(loadout, catalog, options);
  if (cards.status !== 'LEGAL') return {status: 'FAIL', reason: cards.reason};
  return assessmentHoldingLegality(equipmentIDs(loadout),catalog,model);
}

// Shared by initial draws and partial locks. Post-output edits do not call this.
export function assessmentHoldingLegality(ids,catalog,model) {
  const catalogById = new Map(catalog.equipment.map(r => [r.id, r]));
  const modelById = new Map(model.equipment.map(r=>[r.equipment_id,r]));
  // Bullet Storm is a regular support for selection, despite disposable ammo facts.
  const supports = ids.filter(id => (canOccupy(catalogById.get(id), 'weapon')||modelById.get(id)?.facts.holding?.support===true)
    && (id==='support_mgx_42_bullet_storm'||modelById.get(id)?.facts.inventory?.disposable!==true));
  const packs = ids.filter(id => canOccupy(catalogById.get(id), 'backpack')
    && id!=='backpack_b_100_portable_hellbomb');
  if(!withinExosuitLimit(ids))return {status:'FAIL',reason:'MULTIPLE_EXOSUITS'};
  if (supports.length > 1) return {status: 'FAIL', reason: 'MULTIPLE_SUPPORT_WEAPONS'};
  if (packs.length > 1) return {status: 'FAIL', reason: 'MULTIPLE_BACKPACKS'};
  if (supports.length && packs.some(id=>id!==supports[0])) {
    const holding = modelById.get(supports[0])?.facts.holding;
    if (!holding || typeof holding.requires_backpack !== 'boolean') return {status: 'UNKNOWN', reason: 'SUPPORT_BACKPACK_REQUIREMENT_UNKNOWN'};
    if (holding.requires_backpack) return {status: 'FAIL', reason: 'SUPPORT_WEAPON_CARRIES_BACKPACK'};
  }
  return {status: 'PASS'};
}

export function combinedMedium(rows, model) {
  const reference = model.equipment.find(r => r.equipment_id === model.references.anti_medium);
  const witnesses = model.scenario.medium.map(({id: target_id, count}) => {
    const ref = reference.capabilities.modes.flatMap(m => m.anti_medium).find(t => t.target_id === target_id);
    const candidates = rows.filter(row=>contributionKind(row,'anti_medium')!=='AUXILIARY_ADDITION'&&!Number.isFinite(row.manual_calibration?.scores?.anti_medium)).flatMap(row => (row.capabilities.modes ?? []).flatMap(mode => {
      const hit = mode.anti_medium.find(t => t.target_id === target_id);
      if (!(hit?.response?.value > 0) || !(ref?.response?.value > 0)) return [];
      return [{equipment_id: row.equipment_id, mode_id: mode.mode_id, target_id, count,
        measured_target: hit.route?.measured_target ?? target_id, route: hit.route ?? null,
        ratio: hit.response.value / ref.response.value * (row.slot === 'primary' ? 1.25 : 1) / 1.25,
        rounds: hit.shots, first_part_break_ms: hit.response.first_part_break_ms,
        effective_response_ms: hit.response.effective_response_ms,
        threat_resolution: hit.entries?.[0]?.resolution ?? null,
        full_threat_resolution_ms: hit.neutralized_at_ms, rapid: hit.response.rapid}];
    })).sort((a, b) => b.ratio - a.ratio);
    return candidates[0] ?? {target_id, ratio: null};
  });
  if (witnesses.some(w => w.ratio === null)) return {state: 'UNRESOLVED', score: null, witnesses};
  const ratio = witnesses.reduce((n, w) => n + w.ratio * w.count, 0) / witnesses.reduce((n, w) => n + w.count, 0);
  return {state: 'ENGINEERING_ESTIMATE', score: capacityCurve(ratio), ratio, witnesses,
    scope: 'SCENARIO_COUNT_WEIGHTED_MEAN; TARGET_SPECIALIZATION_REQUIRES_SHARED_RESOURCE_PLAN'};
}

function closeSafety(rows, model) {
  const reference = model.equipment.find(r => r.equipment_id === model.references.close_defense);
  const refTime = Math.min(...reference.capabilities.modes.map(m => m.close_defense.neutralized_at_ms).filter(t => t > 0));
  if (!Number.isFinite(refTime)) return {status: 'UNKNOWN', reason: 'CLOSE_REFERENCE_RESOLUTION_REQUIRED'};
  const routes = rows.flatMap(row => (row.capabilities.modes ?? []).flatMap(mode => {
    const close = mode.close_defense;
    if (close?.status !== 'ESTIMATE') return [];
    const time = close.safe_escape_ms ?? close.neutralized_at_ms;
    if (!(time > 0)) return [];
    const sourceMode = (row.facts.service_modes ?? row.facts.modes).find(m=>m.mode_id === mode.mode_id);
    const renewable = row.facts.service_inventory?.renewable === true;
    const inventory = sourceMode?.mounted ? sourceMode.total : (row.facts.service_inventory ?? row.facts.inventory).total;
    return [{equipment_id: row.equipment_id, mode_id: mode.mode_id, response_time_ms: time,
      neutralized_at_ms: close.neutralized_at_ms, safe_escape_ms: close.safe_escape_ms ?? null,
      resolution_basis: close.resolution_basis, control: close.control ?? null,
      ratio: refTime / time,
      rounds: close.shots, inventory, renewable}];
  })).filter(r => r.renewable || r.inventory >= r.rounds).sort((a, b) => b.ratio - a.ratio);
  return routes.some(r => r.ratio >= 1) ? {status: 'PASS', route: routes[0], reference_ms: refTime,
    scope: 'DECLARED_KILL_OR_CONDITIONAL_CONTROL_ESCAPE; NOT_GAMEPLAY_CERTIFIED'}
    : {status: 'UNKNOWN', reason: 'NO_KNOWN_CLOSE_ROUTE_AT_REFERENCE', routes, reference_ms: refTime};
}

// End-to-end diagnostic of real seven-slot inputs. All gates are computed here,
// not supplied as PASS by the caller. Never reads the retired six-dimension table.
export function evaluateAssessment(loadout, model, catalog, options = {}) {
  const legal = assessmentLegality(loadout, catalog, model, options);
  if (legal.status !== 'PASS') return {status: legal.status === 'FAIL' ? 'REJECTED_ILLEGAL' : 'UNKNOWN',
    eligible_for_draw: false, gates: {legality: legal}};
  const byId = new Map(model.equipment.map(r => [r.equipment_id, r]));
  const allRows = equipmentIDs(loadout).map(id => byId.get(id));
  if (allRows.some(r => !r)) return {status: 'UNKNOWN', eligible_for_draw: false, reason: 'ASSESSMENT_ROW_MISSING'};
  const disabled = new Set(options.disabled_contributions ?? []);
  const rows = allRows.filter(r => !disabled.has(r.equipment_id));
  const reserved = new Set(options.role_plan?.reserved_for_heavy ?? []);
  const ordinaryRows = rows.filter(r => !reserved.has(r.equipment_id));
  const ordinary = ordinaryRows.filter(row => !row.strategic_profile&&!row.automatic_assessments?.length)
    .flatMap(row => Object.entries(row.dimensions).filter(([role]) => !(row.category === 'throwables' && role === 'chaff'))
      .map(([role, value]) => ({equipment_id: row.equipment_id, role, ...value})));
  const medium = combinedMedium(ordinaryRows, model);
  ordinary.push({equipment_id: 'plan:target-specialized-medium', role: 'anti_medium', ...medium});
  const ordinary_rates = {
    chaff: Math.max(0, ...ordinaryRows.filter(r => r.category !== 'throwables').flatMap(r => (r.capabilities.modes ?? []).map(m => m.chaff?.sustained?.targets_per_second ?? 0))),
    anti_medium: Math.max(0, ...ordinaryRows.flatMap(r => (r.capabilities.modes ?? []).map(m => {
      const seconds = model.scenario.medium.reduce((n, target) => n + target.count
        * (m.anti_medium.find(t => t.target_id === target.id)?.response?.repeat_cycle_ms ?? Infinity) / 1000, 0);
      return model.scenario.medium.reduce((n, t) => n + t.count, 0) / seconds;
    }))),
  };
  const strategic = rows.flatMap(row => assessStrategic(row, model, {ordinary_rates,
    remaining_mission_uses: options.remaining_mission_uses?.[row.equipment_id]}));
  const automatic=rows.flatMap(row=>assessAutomatic(row,model).map(a=>Number.isFinite(manualValue(row,a.role))
    ?{...a,contribution:{...a.contribution,value:manualValue(row,a.role),status:'KNOWN',score_origin:'MANUAL_CALIBRATED'}}:a));
  const scale=model.scenario.score_scale;
  const chaffBonus = strategic.filter(r => r.role === 'chaff' && r.contribution.status === 'KNOWN'
    &&contributionKind(byId.get(r.equipment_id),'chaff')==='AUXILIARY_ADDITION').reduce((n,r)=>n+
      (r.contribution.score_origin==='MANUAL_CALIBRATED'?originalScore(r.contribution.value,scale):r.contribution.value),0);
  const ordinaryPlan = assessMixedEndurance(ordinaryRows, model, {require_role_floors: true,
    chaff_score_floor: Math.max(0, (scale?.previous_pass??60) - chaffBonus)});
  const grenadeAdditions=suppliedGrenadeAdditions(ordinaryRows,model,ordinaryPlan);
  const gates = {legality: legal, heavy_pair: assessHeavyPairs(rows, model), demolition: assessFiveHoles(rows, model),
    shared_resources: ordinaryPlan, close_safety: closeSafety(ordinaryRows, model)};
  // Every final dimension must pass, in addition to the independent physical gates.
  // Medium route selection still uses physical response, not slot display preference.
  const thresholds = {chaff:60,close_defense:60,anti_medium:60,anti_heavy:60,endurance:60,demolition:60};
  // A grenade's auxiliary display score cannot lower the physical gun-only
  // floor without a paid combined plan. The stock ledger remains conservative.
  const candidates=[...ordinary.map(r=>({...r,default_kind:'BASE_CAPABILITY'})),
    ...[...strategic,...grenadeAdditions,...automatic].map(r=>({equipment_id:r.equipment_id,role:r.role,
      score:r.contribution.value,state:r.contribution.status==='KNOWN'?'ENGINEERING_ESTIMATE':'UNRESOLVED',
      score_origin:r.contribution.score_origin,contribution:r.contribution,default_kind:'AUXILIARY_ADDITION'})),
    ...rows.flatMap(r=>[['anti_heavy',r.heavy_assessment],['endurance',r.endurance],['demolition',r.demolition_assessment]].map(([role,v])=>({
      equipment_id:r.equipment_id,role,score:v.score,score_origin:v.score_origin,
      state:v.score_origin==='MANUAL_CALIBRATED'?'MANUAL_CALIBRATED':Number.isFinite(v.score)?'ENGINEERING_ESTIMATE':'UNRESOLVED',
      default_kind:contributionKind(r,role)})))];
  const baseScores=[],additions=[];
  const enduranceAssessment=assessLoadoutEndurance(rows,model,{reserved_for_heavy:[...reserved]});
  for(const candidate of candidates){
    const row=byId.get(candidate.equipment_id),kind=row?contributionKind(row,candidate.role):candidate.default_kind;
    if(candidate.role==='endurance'&&(row?.category==='throwables'||isMeleeEquipment(row)))continue;
    if(candidate.role==='endurance'&&(row?.manual_calibration?.mode!=='additive'
      ||row.endurance.state==='SUPPLY_PROVIDER'))continue;
    if(kind==='AUXILIARY_ADDITION')additions.push({equipment_id:candidate.equipment_id,role:candidate.role,
      contribution:{...candidate.contribution,value:candidate.score,status:Number.isFinite(candidate.score)?'KNOWN':'UNKNOWN',
        score_origin:candidate.score_origin,source:row?.manual_calibration?.mode==='additive'?'MANUAL_EQUIPMENT_ADDITION':candidate.contribution?.source}});
    else baseScores.push(candidate);
  }
  baseScores.push({equipment_id:'plan:routine-endurance',role:'endurance',score:enduranceAssessment.score,
    score_origin:'CURRENT_DISPLAY_SCALE',state:Number.isFinite(enduranceAssessment.score)?'ENGINEERING_ESTIMATE':'UNRESOLVED'});
  let summary = summarizeLoadout({ordinary:baseScores, strategic:additions, gates, thresholds,score_scale:scale,
    coverage_complete: {chaff: false, close_defense: false, anti_medium: false}, calibration_ready: false});
  summary.dimensions.endurance={...summary.dimensions.endurance,
    scope:enduranceAssessment.scope,lower_bound:enduranceAssessment.lower_bound,
    ...(enduranceAssessment.score===null&&!summary.dimensions.endurance.contributions.length?{score:null}:{}),
    computation:enduranceAssessment};
  summary.dimensions.endurance=applySupplyEnduranceFloor(summary.dimensions.endurance,rows,enduranceAssessment.policy);
  summary=finalizeAssessmentSummary(summary);
  return {...summary, loadout, gates, medium_routes: medium,
    endurance_assessment:enduranceAssessment,
    throwable_endurance:rows.filter(r=>r.category==='throwables').map(r=>({equipment_id:r.equipment_id,
      score:contributionScore(r.endurance.score,r.endurance.score_origin,scale),scope:'SLOT_USES_ONLY; NOT_GUN_ENDURANCE'})),
    strategic_assessments: strategic,
    grenade_assessments: grenadeAdditions,
    automatic_assessments:automatic,
    acceptance_ready: summary.numeric_status === 'PASS',
    acceptance_policy: model.scenario.acceptance_policy ?? null,
    disabled_contributions: [...disabled],
    role_plan: options.role_plan ?? null,
    baseline_policy: {chaff: model.references.chaff, close_defense: model.references.close_defense,
      anti_medium: model.references.anti_medium, medium_gate: 'MEAN_PHYSICAL_RESPONSE_INSIDE_SHARED_PLAN; FINAL_SCORE_AT_LEAST_60',
      threshold_meaning: 'REFERENCE_NORMALIZED; NOT_RETIRED_ABSOLUTE_POINTS'},
    unresolved_strategic_equipment: [...new Set(strategic.filter(r => r.contribution.status !== 'KNOWN').map(r => r.equipment_id))],
    quality: 'SOURCE_DERIVED_DIAGNOSTIC; NOT_GAMEPLAY_CALIBRATED', eligible_for_draw: false};
}

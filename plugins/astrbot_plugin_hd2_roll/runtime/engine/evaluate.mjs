import {VERSION, DIMENSIONS, clone, equipmentIDs, legality, canonical, stable, validSlotsShape, canOccupy} from './common.mjs';
import {actionAllowedInCase} from './common.mjs';
import {beginSearch, searchSlice} from './planner.mjs';
import {replay} from './replay.mjs';
import {evidenceKnown, caseDependencies, eventValid, compileAction} from './proof.mjs';
import {holdingOptions} from './holding.mjs';
import {verifiedBundleCatalog} from './bundles.mjs';
import {predeploymentRows,predeploymentSeed} from './predeployment.mjs';
import {evidenceKnownAt, gradeGate, selectEvidenceGrade} from './grade.mjs';
export {evidenceKnown} from './proof.mjs';

function schemaValid(profile) {
  if (!Array.isArray(profile.cases) || !profile.cases.length) return false;
  const covered = new Set();
  for (const c of profile.cases) {
    if (!c.id || !Array.isArray(c.goals) || !c.goals.length || !Number.isSafeInteger(c.horizon_ms) || c.horizon_ms < 1 || !Number.isSafeInteger(c.max_steps) || c.max_steps < 1) return false;
    const observations = c.observations || [];
    if (observations.some((o, i) => !Number.isSafeInteger(o.at) || o.at < 0 || o.at > c.horizon_ms || i && o.at <= observations[i - 1].at || !o.alternatives?.length || new Set(o.alternatives.map(a => a.id)).size !== o.alternatives.length || o.alternatives.some(a => (a.events || []).some(e => !eventValid(e) || e.offset_ms !== 0 || e.kind === 'disengage')))) return false;
    // Declared slots must be a well-formed plain object when present; a holding selector
    // replaces fixed ids and cannot be combined with them.
    if (!validSlotsShape(c.initial?.slots)) return false;
    if (c.initial?.holding !== undefined && (c.initial.holding !== 'select_from_loadout' || c.initial.slots !== undefined)) return false;
    const alternatives = observations.flatMap(o => o.alternatives.map(a => a.id));
    if (new Set(alternatives).size !== alternatives.length) return false;
    const targetIDs = new Set([...Object.keys(c.initial?.targets || {}), ...observations.flatMap(o => o.alternatives.flatMap(a => (a.events || []).filter(e => e.kind === 'spawn').map(e => e.key)))]);
    for (const g of [...c.goals, ...(c.conditional_goals || [])]) {
      if (!DIMENSIONS.includes(g.dimension) || !['neutralized', 'destroyed', 'resource', 'flag', 'demolition', 'safety'].includes(g.kind)) return false;
      if (['neutralized', 'destroyed', 'demolition', 'safety'].includes(g.kind) && (!Number.isSafeInteger(g.by_ms) || g.by_ms < 0)) return false;
      if (['neutralized', 'destroyed'].includes(g.kind) && !targetIDs.has(g.target)) return false;
      if (g.alternative_id && !alternatives.includes(g.alternative_id)) return false;
      if (g.kind === 'resource' && (!Object.hasOwn(c.initial?.resources || {},g.key) || !Number.isFinite(g.min))) return false;
      // Safety goals bind to scenario targets and to the calibrated window length: the
      // disengage record must cover every bound target and actually last the required
      // duration, so the deadline alone cannot be satisfied by a stub escape.
      if (g.kind === 'safety' && (!Array.isArray(g.targets) || !g.targets.length || g.targets.some(t => typeof t !== 'string' || !t) || new Set(g.targets).size !== g.targets.length || g.targets.some(t => !targetIDs.has(t)))) return false;
      if (g.kind === 'safety' && (!Number.isSafeInteger(g.min_duration_ms) || g.min_duration_ms < 1 || g.by_ms < g.min_duration_ms)) return false;
      covered.add(g.dimension);
    }
  }
  return DIMENSIONS.every(d => covered.has(d));
}
// Cases either fix their held gear or declare select_from_loadout; only the latter seeds
// the search with strategy alternatives derived from the actual loadout and catalog.
// Certification only ever sees the verified bundle view: a bundled weapon's backpack
// slot participates in a certificate solely when its trait is verified for this build.
const holdSeeds = (spec, loadout, catalog) => spec.initial?.holding === 'select_from_loadout' ? holdingOptions(loadout, catalog) : undefined;
export function beginEvaluation(model, loadout, profileID, options = {}) {
  const legal = legality(loadout, model.catalog, options);
  const base = {schema: 1, evaluator_version: VERSION, model_hash: model.model_hash, loadout: clone(loadout), profile_id: profileID, cases: [], index: 0, search: null, result: null};
  if (legal.status !== 'LEGAL') { base.result = legal; return base; }
  // Exactly one grade per evaluation, chosen explicitly by the caller. No selection keeps
  // the historical verified gate; an unknown selection is refused instead of replaced.
  const grade = selectEvidenceGrade(options.evidence_grade);
  base.evidence_grade = grade;
  if (grade === null) { base.result = {status: 'INVALID_INPUT', reason: 'EVIDENCE_GRADE_UNKNOWN', evidence_grade: options.evidence_grade}; return base; }
  const p = model.profiles.profiles.find(p => p.id === profileID);
  // The profile itself must declare this grade and, for a source-reviewed run, be bound to
  // the model's own evidence set: the caller's selection alone never widens the gate.
  const gate = gradeGate(p, grade, model);
  if (!gate.ok) {
    const status = gate.reason === 'REVIEWED_EVIDENCE_SET_MISSING' ? 'UNKNOWN_DATA' : 'INVALID_INPUT';
    base.result = {status, reason: gate.reason, evidence_grade: grade, profile_id: profileID};
    return base;
  }
  base.evidence_set_id = gate.evidence_set_id;
  base.pass_status = gate.pass_status;
  if (!evidenceKnownAt(model, p.calibration.evidence_refs, p.game_build, grade)) { base.result = {status: 'UNKNOWN_DATA', reason: 'CALIBRATION_EVIDENCE_MISSING', evidence_grade: grade}; return base; }
  if (!schemaValid(p)) { base.result = {status: 'INVALID_INPUT', reason: 'INVALID_SCENARIO_SCHEMA'}; return base; }
  if (p.cases.some(c => !caseDependencies(model, c, p))) { base.result = {status: 'UNKNOWN_DATA', reason: 'SCENARIO_FACTS_UNVERIFIED'}; return base; }
  const ids = new Set(equipmentIDs(loadout));
  const catalog = verifiedBundleCatalog(model, p.game_build, grade);
  const byID = new Map(catalog.equipment.map(e => [e.id, e]));
  const held = p.cases.flatMap(c => Object.entries(c.initial?.slots || {}));
  // Initially held gear must come from the evaluated loadout and match the slot's
  // verified capability; a case cannot conjure extra equipment or put a grenade in a
  // backpack slot, and an unverified bundled trait cannot back a declared slot.
  if (held.some(([, id]) => !ids.has(id))) { base.result = {status: 'INVALID_INPUT', reason: 'INITIAL_SLOT_NOT_IN_LOADOUT', equipment_ids: held.filter(([, id]) => !ids.has(id)).map(([, id]) => id)}; return base; }
  if (held.some(([slot, id]) => !canOccupy(byID.get(id), slot))) { base.result = {status: 'INVALID_INPUT', reason: 'INITIAL_SLOT_ROLE_INVALID', slots: Object.fromEntries(held)}; return base; }
  const effects = model.catalog.equipment.filter(e => ids.has(e.id)).flatMap(e => e.unavoidable_effects || []);
  // Unmodeled mandatory effects cannot disappear just because the search did not use this item.
  if (effects.length) { base.result = {status: 'UNKNOWN_DATA', reason: 'UNMODELED_UNAVOIDABLE_EFFECT', equipment_effects: effects}; return base; }
  base.profile = clone(p);
  // The public resupply call is a profile-baseline system action: it joins the usable
  // library only when the profile registers a public_supply policy and the action is a
  // called-kind supply bound to exactly that subject — never a general pass for
  // unselected equipment, and actions outside this rule keep the global filter.
  const supplyPolicy = p.public_supply && typeof p.public_supply === 'object' && !Array.isArray(p.public_supply) ? p.public_supply : null;
  const systemSubject = supplyPolicy && typeof supplyPolicy.subject_id === 'string' && supplyPolicy.subject_id ? supplyPolicy.subject_id : null;
  const isCalledSupply = a => !!systemSubject && !!a.resupply && (a.resupply.kind === 'public_box' || a.resupply.kind === 'backpack_refill') && a.required_equipment?.length === 1 && a.required_equipment[0] === systemSubject;
  const available = (model.actions || p.actions || []).filter(a => a.required_equipment?.every(id => ids.has(id)) || isCalledSupply(a));
  if (new Set(available.map(a => a.id)).size !== available.length) { base.result = {status: 'INVALID_INPUT', reason: 'DUPLICATE_ACTION_ID'}; return base; }
  base.actions = available.map(a => compileAction(model, a, loadout, p)).filter(Boolean);
  base.missing_actions = available.filter(a => !base.actions.some(b => b.id === a.id)).map(a => a.id);
  try { base.prepared_seeds=p.cases.map(c=>predeploymentSeed(c,predeploymentRows(c,base.actions,p))); base.search = beginSearch(p.cases[0], base.actions.filter(a => actionAllowedInCase(a, p.cases[0])), holdSeeds(p.cases[0], loadout, catalog),base.prepared_seeds[0]); }
  catch (e) { base.result = {status: e.message==='PREDEPLOYMENT_ACTION_UNAVAILABLE'&&(p.cases||[]).some(c=>(c.initial?.predeployment||[]).some(r=>base.missing_actions.includes(r.action_id)))?'UNKNOWN_DATA':'INVALID_INPUT', reason:e.message}; }
  return base;
}
export function evaluateSlice(model, evaluation, nodeBudget = 128) {
  if (evaluation.model_hash !== model.model_hash || evaluation.evaluator_version !== VERSION) return {status: 'INVALID_INPUT', reason: 'MODEL_VERSION_CHANGED'};
  if (evaluation.result) return evaluation.result;
  const found = searchSlice(evaluation.search, nodeBudget);
  if (found.status === 'PAUSED_EVALUATION') return {status: 'PAUSED_EVALUATION', case_id: evaluation.profile.cases[evaluation.index].id, expanded: found.expanded};
  const spec = evaluation.profile.cases[evaluation.index];
  if (found.status !== 'WITNESS') {
    evaluation.result = {status: evaluation.missing_actions.length ? 'UNKNOWN_DATA' : 'UNKNOWN_COVERAGE', case_id: spec.id, evidence_grade: evaluation.evidence_grade, evidence_set_id: evaluation.evidence_set_id ?? null, missing_actions: evaluation.missing_actions, reason: 'NO_CERTIFICATE_IN_FINITE_LIBRARY', search: {expanded: found.expanded, truncated: found.truncated, frontier_empty: true}};
    return evaluation.result;
  }
  const checked = replay(spec, evaluation.actions, found.certificate, {model, loadout: evaluation.loadout, profile: evaluation.profile, evidence_grade: evaluation.evidence_grade});
  if (!checked.valid) {
    evaluation.result = {status: 'INVALID_INPUT', reason: 'CERTIFICATE_REPLAY_FAILED', evidence_grade: evaluation.evidence_grade, detail: checked.reason}; return evaluation.result;
  }
  evaluation.cases.push({case_id: spec.id, certificate: found.certificate, replay: checked, provenance: {model_hash: model.model_hash, profile_id: evaluation.profile_id, loadout_key: canonical(evaluation.loadout), case_input: stable(spec), evidence_grade: evaluation.evidence_grade, evidence_set_id: evaluation.evidence_set_id ?? null}}); evaluation.index++;
  if (evaluation.index === evaluation.profile.cases.length) {
    // A finished run may only claim its own grade: source-reviewed results carry the
    // reviewed status and evidence set and never borrow the game-measured 'CERTIFIED'.
    const pass = evaluation.pass_status;
    evaluation.result = {status: pass, evidence_grade: evaluation.evidence_grade, evidence_set_id: evaluation.evidence_set_id ?? null, model_hash: model.model_hash, profile_id: evaluation.profile_id, loadout_key: canonical(evaluation.loadout), dimensions: Object.fromEntries(DIMENSIONS.map(d => [d, {status: pass}])), cases: evaluation.cases};
    return evaluation.result;
  }
  const next = evaluation.profile.cases[evaluation.index];
  evaluation.search = beginSearch(next, evaluation.actions.filter(a => actionAllowedInCase(a, next)), holdSeeds(next, evaluation.loadout, verifiedBundleCatalog(model, evaluation.profile.game_build, evaluation.evidence_grade)),evaluation.prepared_seeds[evaluation.index]);
  return {status: 'PAUSED_EVALUATION', case_id: next.id};
}
export function evaluate(model, loadout, profileID, options) {
  const task = beginEvaluation(model, loadout, profileID, options);
  let result;
  do { result = evaluateSlice(model, task, 1024); } while (result.status === 'PAUSED_EVALUATION');
  return result;
}

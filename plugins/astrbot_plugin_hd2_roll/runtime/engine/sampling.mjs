import {SLOTS, VERSION, stable, clone, canonical, legality, profileReady} from './common.mjs';
import {sample, uniformInt, taskRng} from './rng.mjs';
import {beginEvaluation, evaluateSlice} from './evaluate.mjs';
import {gradeGate, isPassResult, selectEvidenceGrade} from './grade.mjs';
export function pools(catalog, constraints = {}) {
  const excluded = new Set(constraints.excluded || []), unlocked = constraints.unlocked ? new Set(constraints.unlocked) : null;
  const result = Object.fromEntries([...SLOTS, 'stratagem'].map(s => [s, catalog.equipment.filter(e => e.selection_slot === s && !excluded.has(e.id) && (!unlocked || unlocked.has(e.id))).map(e => e.id).sort()]));
  const locks = constraints.locks || {}, fixed = locks.stratagems || [null, null, null, null];
  if (!Array.isArray(fixed) || fixed.length !== 4 || fixed.some(id => id != null && !result.stratagem.includes(id)) || new Set(fixed.filter(Boolean)).size !== fixed.filter(Boolean).length || SLOTS.some(s => locks[s] != null && !result[s].includes(locks[s]))) return {status: 'INVALID_LOCKS'};
  if (SLOTS.some(s => !result[s].length) || result.stratagem.length < 4) return {status: 'EMPTY_SLOT_POOL'};
  return {status: 'READY', values: result, locks: {...locks, stratagems: [...fixed]}};
}
export function propose(prepared, next) {
  const {values, locks} = prepared, loadout = {};
  for (const s of SLOTS) loadout[s] = locks[s] || values[s][uniformInt(values[s].length, next)];
  const free = values.stratagem.filter(id => !locks.stratagems.includes(id));
  const picks = sample(free, locks.stratagems.filter(id => !id).length, next);
  loadout.stratagems = locks.stratagems.map(id => id || picks.shift());
  return loadout;
}
export function inputKey(model, options) {
  const normalized=clone(options), constraints=normalized.constraints;
  if(constraints)for(const key of ['excluded','unlocked'])if(constraints[key])constraints[key]=[...new Set(constraints[key])].sort();
  return stable({model_hash: model.model_hash, evaluator_version: VERSION, options:normalized});
}
export function spaceSize(prepared) {
  const {values:p,locks:l}=prepared,free=p.stratagem.length-l.stratagems.filter(Boolean).length,need=l.stratagems.filter(id=>!id).length;
  let size=1n;for(let i=1;i<=need;i++)size=size*BigInt(free-i+1)/BigInt(i);
  for(const slot of SLOTS)size*=BigInt(l[slot]?1:p[slot].length);
  return size.toString();
}
function preflight(model, options, certification = true, {skipProfileGate = false} = {}) {
  if (!['pure', 'certified'].includes(options.mode)) return {status: 'INVALID_INPUT'};
  // With an injected evaluator the profile gate belongs to the evaluator's
  // context preflight (it reports the production dependency table); the
  // evaluator keeps returning the same PROFILE_NOT_CERTIFIED vocabulary.
  // The draw gate reads the same grade the caller selected: no selection keeps the
  // historical verified profile gate, and a grade the profile does not declare is named
  // rather than silently widened.
  const grade = selectEvidenceGrade(options.evidence_grade);
  if (grade === null) return {status: 'INVALID_INPUT', reason: 'EVIDENCE_GRADE_UNKNOWN'};
  if (certification && !skipProfileGate && options.mode === 'certified') {
    const gate = gradeGate(model.profiles.profiles.find(p => p.id === options.profile_id), grade, model);
    if (!gate.ok) return {status: 'PROFILE_NOT_CERTIFIED', reason: gate.reason, evidence_grade: grade};
  }
  return pools(model.catalog, options.constraints);
}
export function beginDraw(model, options) {
  // The draw evaluator (TASK-04 production chain) is a function-carrying
  // context created by the host. It never participates in the input key: the
  // request identity stays model + evaluator version + user options.
  const {evaluator, ...normalizedOptions} = options || {};
  const hasEvaluator = !!(evaluator && typeof evaluator.evaluate === 'function' && typeof evaluator.begin === 'function');
  const prepared = preflight(model, normalizedOptions, true, {skipProfileGate: hasEvaluator && normalizedOptions.mode === 'certified'});
  const task = {schema: 1, kind: 'draw', input_key: inputKey(model, normalizedOptions), options: clone(normalizedOptions), evidence_grade: selectEvidenceGrade(normalizedOptions.evidence_grade), prepared, completed: 0, counts: {}, candidate: null, evaluation: null, result: null, rng_state:null, rng_kind:null, seen_candidates:[]};
  if (hasEvaluator) {
    task.evaluator = evaluator;
    task.evaluator_key = typeof evaluator.key === 'string' ? evaluator.key : 'unnamed';
  }
  if(prepared.status==='READY')task.space_size=spaceSize(prepared);
  // Large continuous searches must not retain an ever-growing list of rejected
  // loadouts. This only limits exhaustive bookkeeping, never proposal selection.
  task.coverage_tracking = options.until_success === true && task.space_size && BigInt(task.space_size)>100000n ? 'DISABLED_LARGE_SPACE' : 'ENABLED';
  if (prepared.status !== 'READY') task.result = prepared;
  else if (task.evaluator && task.options.mode === 'certified' && typeof task.evaluator.preflight === 'function') {
    // A globally incomplete context (profile/policy/data) ends the draw at the
    // start phase — never an endless rescan of candidates that all fail alike.
    const context = task.evaluator.preflight(model, task.options);
    if (context) task.result = context;
  }
  if (options.until_success === true) {
    if (options.max_attempts != null) task.result={status:'INVALID_INPUT',reason:'CHOOSE_ONE_STOP_POLICY'};
  } else if (options.until_success != null && options.until_success !== false || !Number.isSafeInteger(options.max_attempts) || options.max_attempts < 1) task.result = {status:'INVALID_INPUT',reason:'FIXED_ATTEMPT_LIMIT_REQUIRED'};
  return task;
}
function evaluateCandidate(model, task, candidate, budget, cache) {
  const legal = legality(candidate, model.catalog, task.options.constraints);
  if (legal.status !== 'LEGAL') return legal;
  if (task.options.mode === 'pure') return {status: 'UNVERIFIED', reason: 'PURE_RANDOM'};
  if (task.evaluator) return task.evaluator.evaluate(model, task, candidate, budget, cache);
  const key = stable({model: model.model_hash, profile: task.options.profile_id, loadout: canonical(candidate)});
  if (cache?.has(key)) return clone(cache.get(key));
  if (!task.evaluation) task.evaluation = beginEvaluation(model, candidate, task.options.profile_id, {...(task.options.constraints || {}), evidence_grade: task.options.evidence_grade});
  const result = evaluateSlice(model, task.evaluation, budget);
  if (!['PAUSED_EVALUATION', 'INVALID_INPUT'].includes(result.status)) cache?.set(key, clone(result));
  return result;
}
function drawStep(model, task, {next, node_budget = 128, cache, current_options = task.options} = {}) {
  if (task.input_key !== inputKey(model, current_options)) return task.result = {status: 'REQUEST_INVALIDATED'};
  if (task.result) return task.result;
  if (!task.candidate) task.candidate = propose(task.prepared, next);
  const result = evaluateCandidate(model, task, task.candidate, node_budget, cache);
  if (result.status === 'PAUSED_EVALUATION') return {status: 'PAUSED_EVALUATION', completed: task.completed, candidate_key: canonical(task.candidate), progress: result};
  task.completed++; task.counts[result.status] = (task.counts[result.status] || 0) + 1;
  const key=canonical(task.candidate);if(task.coverage_tracking!=='DISABLED_LARGE_SPACE'&&!task.seen_candidates.includes(key))task.seen_candidates.push(key);
  // A pass is only ever a pass at its own grade: a reviewed result reaches the user as a
  // reviewed pass, never as a game-measured certificate.
  if (isPassResult(result)) return task.result = {...result, loadout: clone(task.candidate), completed: task.completed};
  if (FATAL_RESULT(result)) return task.result = result;
  task.candidate = null; task.evaluation = null; task.evaluation_model = null;
  if(task.coverage_tracking!=='DISABLED_LARGE_SPACE'&&BigInt(task.seen_candidates.length)===BigInt(task.space_size)) return task.result={status:task.options.mode==='pure'?'NO_LEGAL_LOADOUT':'NO_CERTIFIABLE_LOADOUT',completed:task.completed,counts:task.counts,exhaustive:true,scope:task.options.mode==='pure'?'FROZEN_LEGAL_SET_ONLY':'FROZEN_CERTIFIABLE_SET_ONLY'};
  if (!task.options.until_success && task.completed >= task.options.max_attempts) return task.result = {status: 'ATTEMPTS_EXHAUSTED', completed: task.completed, counts: task.counts};
  return {status: 'EVALUATING', completed: task.completed, counts: task.counts};
}
// Fatal results end the whole draw: engine-invalid requests, and the evaluator's
// context-scope reports (global data/policy gaps must never be rescan fuel).
function FATAL_RESULT(result) {
  return result.status === 'INVALID_INPUT' || result.status === 'CONTEXT_BLOCKED' || result.fatal === true;
}
export function stepDraw(model, task, options = {}) {
  const rng=taskRng(task,options.next),result=drawStep(model,task,{...options,next:rng.next});rng.save();return {...result,rng_state:clone(task.rng_state)};
}
export function cancel(task) { task.result = {status: 'CANCELLED'}; return task.result; }
function selectionSlot(slot) { return SLOTS.includes(slot) ? slot : /^stratagem:[0-3]$/.test(slot) ? 'stratagem' : null; }
export function replaceAt(loadout, slot, id) {
  if (!selectionSlot(slot)) throw new TypeError('INVALID_REPLACEMENT_SLOT');
  const value = clone(loadout);
  if (SLOTS.includes(slot)) value[slot] = id; else value.stratagems[Number(slot.split(':')[1])] = id;
  return value;
}
export function beginReplacement(model, options, loadout, slot) {
  const {evaluator, ...normalized} = options || {}; void evaluator; // replacements never evaluate; keep the input key consistent with draws
  const prepared = preflight(model, normalized, false), type = selectionSlot(slot);
  const task = {schema: 1, kind: 'replacement', input_key: inputKey(model, normalized), options: clone(normalized), loadout: clone(loadout), slot, candidates: [], index: 0, rows: [], evaluation: null, result: null, rng_state:null, rng_kind:null};
  if (prepared.status !== 'READY') { task.result = prepared; return task; }
  if (!type || legality(loadout, model.catalog).status !== 'LEGAL') { task.result = {status: 'INVALID_INPUT'}; return task; }
  if ((type === 'stratagem' ? prepared.locks.stratagems[Number(slot.split(':')[1])] : prepared.locks[slot])) { task.result = {status: 'INVALID_LOCKS', reason: 'SLOT_LOCKED'}; return task; }
  if (SLOTS.some(s => prepared.locks[s] && prepared.locks[s] !== loadout[s]) || prepared.locks.stratagems.some((id,i) => id && id !== loadout.stratagems[i])) {
    task.result = {status:'INVALID_LOCKS',reason:'LOCKED_LOADOUT_MISMATCH'}; return task;
  }
  const old = type === 'stratagem' ? loadout.stratagems[Number(slot.split(':')[1])] : loadout[slot];
  task.candidates = prepared.values[type].filter(id => id !== old && (type !== 'stratagem' || !loadout.stratagems.includes(id)));
  return task;
}
function replacementStep(model, task, {next, current_options = task.options} = {}) {
  if (task.input_key !== inputKey(model, current_options)) return task.result = {status: 'REQUEST_INVALIDATED'};
  if (task.result) return task.result;
  if (task.index < task.candidates.length) {
    const id = task.candidates[task.index], candidate = replaceAt(task.loadout, task.slot, id);
    const legal = legality(candidate, model.catalog, task.options.constraints);
    const result = legal.status === 'LEGAL' ? {status:'UNVERIFIED',reason:'USER_ADJUSTMENT'} : legal;
    if (result.status === 'INVALID_INPUT') return task.result = result;
    task.rows.push({id, status: result.status, reason: result.reason, result}); task.index++; task.evaluation = null;
    return {status: 'EVALUATING', index: task.index, total: task.candidates.length};
  }
  const passing = task.rows.filter(r => r.status === 'UNVERIFIED');
  if (!passing.length) return task.result = {status: 'NO_LEGAL_REPLACEMENT', rows: task.rows};
  const chosen = passing[uniformInt(passing.length, next)];
  return task.result = {status:'UNVERIFIED',reason:'USER_ADJUSTMENT',loadout:replaceAt(task.loadout,task.slot,chosen.id),rows:task.rows};
}
export function stepReplacement(model, task, options = {}) {
  const rng=taskRng(task,options.next),result=replacementStep(model,task,{...options,next:rng.next});rng.save();return {...result,rng_state:clone(task.rng_state)};
}
// Small-space oracle only. It enumerates unordered strategy sets once and never serves as a cached success pool.
export function* enumerate(prepared) {
  const {values: p, locks: l} = prepared;
  const free = p.stratagem.filter(id => !l.stratagems.includes(id)), need = l.stratagems.filter(id => !id).length;
  function* choose(start, chosen) {
    if (chosen.length === need) { yield chosen; return; }
    for (let i = start; i <= free.length - (need - chosen.length); i++) yield* choose(i + 1, [...chosen, free[i]]);
  }
  for (const primary of l.primary ? [l.primary] : p.primary) for (const secondary of l.secondary ? [l.secondary] : p.secondary) for (const grenade of l.grenade ? [l.grenade] : p.grenade) for (const chosen of choose(0, [])) {
    let n = 0; yield {primary, secondary, grenade, stratagems: l.stratagems.map(id => id || chosen[n++])};
  }
}

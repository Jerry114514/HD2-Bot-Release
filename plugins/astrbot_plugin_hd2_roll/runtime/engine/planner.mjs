import {clone, stable} from './common.mjs';
import {initialState, startAction, advance, branchState, allGoals} from './ledger.mjs';

export function beginSearch(spec, actions, holdOptions, preparedSeed) {
  return {schema: 1, spec, actions, expanded: 0, truncated: false, stack: [{state: Object.assign(initialState(spec.initial),preparedSeed||{}), depth: 0, holdOptions: Array.isArray(holdOptions) ? holdOptions : undefined}], result: null};
}
function children(frame, search) {
  const {state: s, depth} = frame, {spec, actions} = search;
  // Initial holding is a player strategy choice made once at t=0 and shared by every
  // future branch; it never adds draw tickets and is never re-picked after branching.
  if (frame.holdOptions) {
    frame.mode = 'or';
    return frame.holdOptions.map(slots => {
      const child = clone(s);
      if (Object.keys(slots).length) child.slots = clone(slots);
      child.trace.push({kind: 'hold', at: 0, slots: clone(slots)});
      return {state: child, depth: depth + 1};
    });
  }
  const obs = (spec.observations || [])[s.next_observation];
  if (obs && obs.at === s.time) {
    frame.mode = 'and'; frame.observation_id = obs.id;
    return obs.alternatives.map(alt => ({state: branchState(s, obs, alt), depth: depth + 1, alternative_id: alt.id}));
  }
  frame.mode = 'or';
  const result = [];
  if (s.time >= s.manual_until) for (const a of actions) {
    const ns = startAction(s, a, spec.observations || []);
    if (ns && ns.time <= spec.horizon_ms) result.push({state: ns, depth: depth + 1});
  }
  const deadlines = [s.manual_until, ...s.pending.map(e => e.at), ...Object.values(s.cooldowns), ...Object.values(s.targets).map(t => t.neutralized_at), obs?.at, spec.finish_after_ms, spec.horizon_ms, ...spec.goals.map(g => g.at_ms)].filter(t => Number.isSafeInteger(t) && t > s.time && t <= spec.horizon_ms);
  if (deadlines.length) {
    const until = Math.min(...deadlines), ns = advance(s, until, spec.observations || []);
    if (ns) { ns.trace.push({kind: 'wait', at: s.time, until}); result.push({state: ns, depth: depth + 1}); }
  }
  // Deterministic order, no wall-clock-dependent branch selection.
  const seen = new Set();
  return result.filter(c => { const {trace, used_events, ...value} = c.state; const key = stable(value); if (seen.has(key)) return false; seen.add(key); return true; });
}
function finish(search, result) {
  const child = search.stack.pop();
  if (!search.stack.length) { search.result = result; return; }
  const parent = search.stack.at(-1);
  if (parent.mode === 'or') { if (result) parent.success = result; }
  else {
    if (!result) parent.failed = true;
    else parent.certificates.push({alternative_id: child.alternative_id, certificate: result});
  }
}
export function searchSlice(search, maxNodes = 128) {
  if (!Number.isSafeInteger(maxNodes) || maxNodes < 1) throw new RangeError('positive node budget required');
  for (let work = 0; work < maxNodes && search.stack.length; work++) {
    const f = search.stack.at(-1);
    if (!f.initialized) {
      search.expanded++;
      if (!f.state) { finish(search, null); continue; }
      if (allGoals(f.state, search.spec)) { finish(search, {kind: 'leaf', trace: f.state.trace}); continue; }
      if (f.depth >= search.spec.max_steps) { search.truncated = true; finish(search, null); continue; }
      f.children = children(f, search); f.next = 0; f.certificates = []; f.initialized = true;
      // Empty observations never count as universally solved.
      if (!f.children.length) { finish(search, null); continue; }
    }
    if (f.success) { finish(search, f.success); continue; }
    if (f.failed) { finish(search, null); continue; }
    if (f.next >= f.children.length) {
      finish(search, f.mode === 'and' ? {kind: 'and', observation_id: f.observation_id, prefix: f.state.trace, branches: f.certificates} : null); continue;
    }
    search.stack.push(clone(f.children[f.next++]));
  }
  return search.stack.length ? {status: 'PAUSED_EVALUATION', expanded: search.expanded} : {status: search.result ? 'WITNESS' : 'NO_WITNESS', certificate: search.result, expanded: search.expanded, truncated: search.truncated};
}

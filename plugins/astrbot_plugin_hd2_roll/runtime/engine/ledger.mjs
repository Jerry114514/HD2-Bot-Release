import {clone, validSlotsShape} from './common.mjs';
const validTime = v => Number.isSafeInteger(v) && v >= 0;
export const phase = e => e.kind === 'resource' ? (e.delta < 0 ? 0 : 2) : 1;
const order = (a,b) => a.at-b.at || phase(a)-phase(b) || a.order[0]-b.order[0] || a.order[1]-b.order[1];
export function initialState(spec) {
  const s = {time: 0, manual_until: 0, resources: clone(spec.resources || {}), targets: clone(spec.targets || {}), flags: {}, safety: {}, demolitions: {}, slots: {}, cooldowns: {}, pending: [], trace: [], used_events: [], observations: [], next_observation: 0};
  // Initially held support weapon/backpack is scenario state; using it must not re-pay the first call-in.
  // A declared slots value must be a well-formed plain object; arrays/null/strings are rejected, not coerced.
  if (spec.slots !== undefined) {
    if (!validSlotsShape(spec.slots)) throw new Error('invalid initial slot');
    s.slots = clone(spec.slots);
  }
  // An optional resource_id binds a ledger account to a published resource-model entry;
  // reload actions require it so ownership is checkable where the account is consumed.
  for (const r of Object.values(s.resources)) {
    if (!Number.isFinite(r.value) || !Number.isFinite(r.cap) || r.value < 0 || r.value > r.cap) throw new Error('invalid initial resource');
    if (r.resource_id !== undefined && (typeof r.resource_id !== 'string' || !r.resource_id)) throw new Error('invalid initial resource');
  }
  for (const t of Object.values(s.targets)) if (!Number.isFinite(t.hp) || t.hp <= 0 || !validTime(t.post_lethal_ms ?? 0)) throw new Error('invalid initial target');
  return s;
}
export function applyEvent(s, event) {
  if (!validTime(event.at) || s.used_events.includes(event.id)) return false;
  if((event.deployment_guards||[]).some(k=>s.flag_expiries?.[k]!==undefined&&s.flag_expiries[k]<=event.at))return false;
  const {kind, key} = event;
  if (kind === 'resource') {
    const r = s.resources[key];
    if (!r || !Number.isFinite(event.delta) || r.value + event.delta < 0) return false;
    r.value = Math.min(r.cap, r.value + event.delta);
  } else if (kind === 'spawn') {
    if (s.targets[key] || !event.target || !(event.target.hp > 0) || !validTime(event.target.post_lethal_ms ?? 0)) return false;
    s.targets[key] = clone(event.target);
  } else if (kind === 'damage') {
    const t = s.targets[key];
    if (!t || event.target_id != null && event.target_id !== t.target_id || !Number.isFinite(event.amount) || event.amount < 0 || event.requires_control && !(t.control_until >= event.at)) return false;
    if (event.part_id) {
      const p = t.parts?.[event.part_id];
      if (!p || !(p.hp > 0)) return false;
      const partBefore=p.hp;
      p.hp = Math.max(0, p.hp - event.amount);
      if (p.hp === 0 && p.fatal === true) t.hp = 0;
      if (event.transfer != null) { if (!Number.isFinite(event.transfer) || event.transfer < 0) return false; t.hp = Math.max(0, t.hp - (event.transfer_policy?.rule==='capped_at_remaining' ? Math.ceil(Math.min(event.amount,partBefore)*event.transfer_policy.fraction) : event.transfer)); }
    } else t.hp = Math.max(0, t.hp - event.amount);
    if (t.hp === 0 && t.destroyed_at == null) {
      t.destroyed_at = event.at;
      t.neutralized_at = event.at + (t.post_lethal_ms ?? 0);
    }
  } else if (kind === 'control') {
    const t = s.targets[key];
    if (!t || t.control_immune || !validTime(event.duration_ms)) return false;
    // Control coverage is an interval, not one end number: a stun that starts after a
    // gap must not masquerade as coverage of the earlier time, so the recorded start
    // moves with the application that actually extends the covered interval.
    const until = event.at + event.duration_ms;
    if (until > (t.control_until || 0)) {
      t.control_from = (t.control_until || 0) >= event.at ? (t.control_from ?? 0) : event.at;
      t.control_until = until;
    }
  } else if (kind === 'flag') {
    // Hole closure is recorded only by verified demolition events in the dedicated demolitions
    // map; an ordinary flag under the demolition: namespace is refused outright, whatever
    // value (0/true/past timestamp) it claims to carry.
    if (typeof key === 'string' && key.startsWith('demolition:')) return false;
    s.flags[key] = event.value;
    if(event.expires_after_ms!==undefined)(s.flag_expiries??={})[key]=event.at+event.expires_after_ms;
  }
  else if (kind === 'disengage') {
    // Safety-window proof: the escape declares its threatened target set and window.
    // A target only stops threatening at neutralized_at — hp 0 alone is not death while
    // post_lethal_ms is still running — so every target not yet neutralized at the
    // window start must stay controlled through min(window end, neutralized_at), and
    // that control must have started no later than the window start. The record lives
    // outside flags, so no ordinary flag event can fabricate it.
    if (!Number.isSafeInteger(event.duration_ms) || event.duration_ms < 1 || !Array.isArray(event.targets) || !event.targets.length || event.targets.some(t => typeof t !== 'string' || !t) || new Set(event.targets).size !== event.targets.length) return false;
    if (event.targets.some(k => !s.targets[k])) return false;
    const end = event.at + event.duration_ms;
    if (event.targets.some(k => {
      const t = s.targets[k];
      if (t.neutralized_at != null && t.neutralized_at <= event.at) return false;
      const coverEnd = t.neutralized_at != null && t.neutralized_at < end ? t.neutralized_at : end;
      return (t.control_from ?? 0) > event.at || (t.control_until || 0) < coverEnd;
    })) return false;
    s.safety[key] = {at: event.at, duration_ms: event.duration_ms, until: end, targets: [...event.targets]};
  } else if (kind === 'equip') {
    if (!['weapon', 'backpack'].includes(key) || typeof event.equipment_id !== 'string') return false;
    s.slots[key] = event.equipment_id;
  } else if (kind === 'demolition') {
    if (!event.route_verified || !event.component_id) return false;
    // Dedicated hole-closure record: the first verified event wins and a repeated close
    // of the same hole fails the transition instead of forging a new fact.
    if (s.demolitions[key] != null) return false;
    s.demolitions[key] = event.at;
  } else return false;
  s.used_events.push(event.id);
  return true;
}
export function advance(state, time, observations = [], copyState = clone) {
  const s = copyState(state);
  if (!validTime(time) || time < s.time) return null;
  const obs = observations[s.next_observation];
  const stop = obs && obs.at <= time ? obs.at : time;
  s.pending.sort(order);
  while (s.pending.length && s.pending[0].at <= stop) {
    const e = s.pending.shift();
    if (!applyEvent(s, e)) return null;
  }
  s.time = stop;
  return s;
}
export function startAction(state, action, observations, copyState = clone) {
  if (!validTime(action.busy_ms) || action.busy_ms < 1 || state.time < state.manual_until) return null;
  if (Object.entries(action.requires_slots || {}).some(([slot, id]) => state.slots[slot] !== id)) return null;
  if (Object.entries(action.requires_flags || {}).some(([key, value]) => state.flags[key] !== value || state.flag_expiries?.[key]!==undefined&&state.flag_expiries[key]<=state.time)) return null;
  for(const [key,bounds] of Object.entries(action.requires_resources||{})) {
    const value=state.resources[key]?.value;
    if(!Number.isFinite(value)||bounds.min!==undefined&&value<bounds.min||bounds.max!==undefined&&value>bounds.max)return null;
  }
  if (action.target && (!state.targets[action.target] || state.targets[action.target].hp <= 0)) return null;
  // Fail before spending a shot when a sibling case reuses the same instance key.
  if ((action.events || []).some(e => e.kind === 'damage' && e.target_id != null && state.targets[e.key]?.target_id !== e.target_id)) return null;
  if (action.cooldown && state.time < (state.cooldowns[action.cooldown.key] || 0)) return null;
  // Reload accounts must resolve to the initial-state entry they claim: the ledger key
  // is matched against the entry's resource_id binding, so a self-reported id cannot
  // cover a debit from another weapon's account. A missing binding is unusable, not guessed.
  if (action.reload) {
    const bound = [[action.reload.magazine.key, action.reload.magazine.resource_id]];
    if (action.reload.reserve) bound.push([action.reload.reserve.key, action.reload.reserve.resource_id]);
    if (bound.some(([key, rid]) => state.resources[key]?.resource_id !== rid)) return null;
  }
  // Resupply accounts resolve the same way: every consumed or credited key must name
  // the initial-state entry that carries the binding the action declares.
  if (action.resupply) {
    const bound = [[action.resupply.consumed?.key, action.resupply.consumed?.resource_id], ...(action.resupply.supplies || []).map(s => [s.key, s.resource_id])];
    if (bound.some(([key, rid]) => state.resources[key]?.resource_id !== rid)) return null;
  }
  // Demolition consumption resolves the same way against the case's own accounts: when
  // the initial entry binds the ledger key to a published resource id, the declaration
  // must match that binding (borrowing across accounts is refused); an unbound key has
  // no binding to contradict and ownership is enforced at compile time.
  if (action.demolition?.resource) {
    const entry = state.resources[action.demolition.resource.key];
    if (entry?.resource_id !== undefined && entry.resource_id !== action.demolition.resource.resource_id) return null;
  }
  const s = copyState(state), start = s.time, serial = s.trace.length;
  const events = (action.events || []).map((e, i) => ({...clone(e), deployment_guards:Object.keys(action.requires_flags||{}).filter(k=>s.flag_expiries?.[k]!==undefined), at: start + e.offset_ms, id: `a${serial}:${i}`, order:[serial,i]}));
  if (events.some(e => !validTime(e.at) || e.at < start || e.kind === 'resource' && e.delta < 0 && e.offset_ms !== 0)) return null;
  // TASK-B composite detonation boundaries on the ledger's own view: every
  // blast-bearing event group is one detonation paid once — a duplicate part
  // instance, a second group riding the same ammo, or a fallback mixed with
  // part claims fails the transition instead of settling.
  if (events.some(e => e.kind === 'damage' && e.blast)) {
    const groups = new Map();
    for (const e of events) {
      if (e.kind !== 'damage' || !e.blast) continue;
      if (typeof e.detonation_id !== 'string' || !e.detonation_id) return null;
      if (!groups.has(e.detonation_id)) groups.set(e.detonation_id, []);
      groups.get(e.detonation_id).push(e);
    }
    for (const group of groups.values()) {
      if(new Set(group.map(e=>e.offset_ms)).size!==1||new Set(group.map(e=>e.key)).size!==1||new Set(group.map(e=>e.target_id)).size!==1)return null;
      if(group.filter(e=>e.blast.contact_share==='direct').length>1)return null;
      const partIds = group.filter(e=>e.blast.contact_share!=='direct').map(e => e.part_id ?? null);
      if (new Set(partIds).size !== partIds.length) return null;
      const mains = partIds.filter(p => p === null).length;
      if (mains > 1 || (mains === 1 && partIds.length > 1)) return null;
    }
    const debits = events.filter(e => e.kind === 'resource' && Number.isFinite(e.delta) && e.delta < 0).reduce((n, e) => n + -e.delta, 0);
    if(action.blast_cost?.kind==='call') {if(debits!==0||groups.size!==1||!action.cooldown)return null;}
    else if (debits !== groups.size) return null;
    if(action.blast_cost?.kind==='ammo'&&state.resources[action.blast_cost.key]?.resource_id!==action.blast_cost.resource_id)return null;
  }
  // A declared escape window must actually be executed: the carrying action stays busy
  // through the whole window, so a 1ms action can never stand in for it.
  if (events.some(e => e.kind === 'disengage' && e.offset_ms + e.duration_ms > action.busy_ms)) return null;
  // All start-time costs precede all start-time gains. No clamping negative balances.
  events.sort(order);
  const now = events.filter(e => e.at === start);
  for (const e of now) if (!applyEvent(s, e)) return null;
  s.pending.push(...events.filter(e => e.at > start));
  s.manual_until = start + action.busy_ms;
  if (action.cooldown) {
    if (!validTime(action.cooldown.duration_ms)) return null;
    s.cooldowns[action.cooldown.key] = start + action.cooldown.duration_ms;
  }
  s.trace.push({kind: 'action', action_id: action.id, at: start});
  return advance(s, s.manual_until, observations, copyState);
}
export function branchState(state, observation, alternative) {
  const s = clone(state);
  s.observations.push(alternative.id); s.next_observation++;
  for (const [i, e] of (alternative.events || []).entries()) {
    if (e.offset_ms !== 0 || !applyEvent(s, {...e, at: s.time, id: `o${s.next_observation}:${i}`})) return null;
  }
  s.trace.push({kind: 'observe', observation_id: observation.id, alternative_id: alternative.id, at: s.time});
  return s;
}
export function goalPass(state, goal) {
  if (goal.kind === 'neutralized' || goal.kind === 'destroyed') {
    const t = state.targets[goal.target], at = t?.[goal.kind === 'neutralized' ? 'neutralized_at' : 'destroyed_at'];
    return at != null && at <= goal.by_ms && at <= state.time;
  }
  if (goal.kind === 'resource') return state.time >= (goal.at_ms ?? 0) && (state.resources[goal.key]?.value ?? -Infinity) >= goal.min;
  if (goal.kind === 'flag') return state.time >= (goal.at_ms ?? 0) && state.flags[goal.key] === goal.value;
  if (goal.kind === 'safety') {
    // Proof 1 — the escape window: genuinely completed by now (until <= time), no later
    // than the scenario deadline, and its executed duration meets the scenario-bound
    // calibrated window; a 1ms disengage cannot substitute the declared window.
    const w = state.safety?.[goal.key];
    const escaped = !!w && w.until <= goal.by_ms && w.until <= state.time
      && (!Number.isSafeInteger(goal.min_duration_ms) || w.duration_ms >= goal.min_duration_ms)
      && (goal.targets || []).every(t => w.targets.includes(t));
    // Proof 2 — every bound target is fully neutralized: hp 0 alone is not enough, the
    // post-lethal delay must have elapsed by now and within the deadline. Either
    // genuine proof stands on its own; no dedicated escape gear is required.
    const bound = goal.targets || [];
    const cleared = bound.length > 0 && bound.every(t => {
      const at = state.targets[t]?.neutralized_at;
      return at != null && at <= goal.by_ms && at <= state.time;
    });
    return escaped || cleared;
  }
  if (goal.kind === 'demolition') {
    // Read only the dedicated closure record; ordinary flags can never appear here.
    const at = state.demolitions?.[goal.key];
    return at != null && at <= goal.by_ms;
  }
  return false;
}
export function allGoals(state, spec) {
  const goals = [...spec.goals, ...(spec.conditional_goals || []).filter(g => state.observations.includes(g.alternative_id))];
  return state.next_observation === (spec.observations || []).length && goals.every(g => goalPass(state, g)) && state.time >= (spec.finish_after_ms ?? 0);
}

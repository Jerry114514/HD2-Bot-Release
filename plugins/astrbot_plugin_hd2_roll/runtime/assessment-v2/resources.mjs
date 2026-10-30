// Finite, shared combat resources. Uses the existing event ledger; no score or
// supply guarantee can manufacture ammunition. All durations are explicit inputs.
import {initialState, startAction, advance, applyEvent} from '../engine/ledger.mjs';
import {shotAt, reloadChoice} from './timing.mjs';

const integer = x => Number.isSafeInteger(x) && x >= 0;
const account = (value, cap = value) => ({value, cap});
export const ammoKey = id => `ammo:${id}`;
export const loadedKey = id => `loaded:${id}`;
export const supplyKey = id => `supply:${id}`;

export function combatResources(weapons, providers, targets = {}) {
  const resources = {};
  for (const weapon of weapons) {
    if (!weapon.id || !integer(weapon.loaded) || !integer(weapon.reserve)
      || !integer(weapon.capacity) || weapon.capacity < 1 || weapon.loaded > weapon.capacity
      || !integer(weapon.reserve_capacity) || weapon.reserve > weapon.reserve_capacity) {
      throw new TypeError('INVALID_WEAPON_INVENTORY');
    }
    if (resources[ammoKey(weapon.id)]) throw new TypeError('DUPLICATE_WEAPON_INVENTORY');
    resources[ammoKey(weapon.id)] = account(weapon.reserve, weapon.reserve_capacity);
    resources[loadedKey(weapon.id)] = account(weapon.loaded, weapon.capacity);
  }
  for (const provider of providers) {
    if (!provider.id || !integer(provider.charges) || !integer(provider.capacity)
      || provider.charges > provider.capacity || resources[supplyKey(provider.id)]) {
      throw new TypeError('INVALID_SUPPLY_INVENTORY');
    }
    resources[supplyKey(provider.id)] = account(provider.charges, provider.capacity);
  }
  return initialState({resources, targets});
}

export function receiveSupply(state, provider, weapons, providers = []) {
  if (!Number.isSafeInteger(provider.take_ms) || provider.take_ms < 1) return {status: 'UNKNOWN', reason: 'SUPPLY_TIME_REQUIRED'};
  const key = supplyKey(provider.id);
  if (!(state.resources[key]?.value > 0)) return {status: 'UNAVAILABLE', reason: 'SUPPLY_EXHAUSTED'};
  if (!Number.isSafeInteger(provider.ready_at_ms) || provider.ready_at_ms < 0) return {status: 'UNKNOWN', reason: 'SUPPLY_READY_TIME_REQUIRED'};
  const ready = advance(state, Math.max(state.time, state.manual_until, provider.ready_at_ms));
  const credits = [], unresolved = [];
  for (const weapon of weapons) {
    if (weapon.renewable&&!weapon.finite_heat_sinks) continue;
    const amount = weapon.refill?.[provider.refill_kind];
    const receivingKey = weapon.feed === 'loose' || weapon.feed === 'belt' ? loadedKey(weapon.id) : ammoKey(weapon.id);
    const stock = ready.resources[receivingKey];
    if (!stock || stock.value >= stock.cap) continue;
    if (!integer(amount)) { unresolved.push(weapon.id); continue; }
    if (amount > 0) credits.push({kind: 'resource', key: receivingKey, delta: amount, offset_ms: provider.take_ms});
  }
  // One public box restores the player's supplies and at most one worn pack box.
  // The same public box is debited once, never once per receiving weapon.
  if (provider.refills_pack) {
    for (const pack of providers.filter(p => p.kind === 'backpack')) {
      const stock = ready.resources[supplyKey(pack.id)];
      if (stock && stock.value < stock.cap) credits.push({kind: 'resource', key: supplyKey(pack.id), delta: 1, offset_ms: provider.take_ms});
    }
  }
  if (!credits.length) return unresolved.length
    ? {status: 'UNKNOWN', reason: 'REFILL_AMOUNT_REQUIRED', equipment_ids: unresolved}
    : {status: 'UNAVAILABLE', reason: 'NOTHING_TO_REFILL'};
  const next = startAction(ready, {id: `take:${provider.id}`, busy_ms: provider.take_ms,
    events: [{kind: 'resource', key, delta: -1, offset_ms: 0}, ...credits]}, []);
  return next ? {status: 'TAKEN', state: next, provider_id: provider.id,
    started_at_ms: ready.time, completed_at_ms: next.time, credited_accounts: credits.map(e => e.key),
    unresolved_refills: unresolved}
    : {status: 'INVALID', reason: 'SUPPLY_LEDGER_REFUSED'};
}

export function reloadWeapon(state, weapon) {
  const loaded = state.resources[loadedKey(weapon.id)], reserve = state.resources[ammoKey(weapon.id)];
  if (!loaded || !reserve) return {status: 'UNKNOWN', reason: 'AMMO_ACCOUNT_MISSING'};
  if (weapon.renewable&&!(weapon.finite_heat_sinks&&reserve.value>=loaded.cap-loaded.value)) {
    const cooling=weapon.cooling_ms??weapon.reload_ms;
    if (!integer(cooling)) return {status: 'UNKNOWN', reason: 'COOLING_TIME_REQUIRED'};
    const ready = advance(state, Math.max(state.time, state.manual_until) + cooling);
    applyEvent(ready, {id: `cool:${weapon.id}:${ready.trace.length}`, at: ready.time,
      kind: 'resource', key: loadedKey(weapon.id), delta: loaded.cap - loaded.value});
    ready.trace.push({kind: 'action', action_id: `cool:${weapon.id}`, at: ready.time});
    return {status: 'RELOADED', state: ready};
  }
  const amount = Math.min(loaded.cap - loaded.value, reserve.value);
  if (!amount) return {status: 'UNAVAILABLE', reason: 'CANNOT_RELOAD'};
  if (!Number.isSafeInteger(weapon.reload_ms) || weapon.reload_ms < 1) return {status: 'UNKNOWN', reason: 'RELOAD_TIME_REQUIRED'};
  const next = startAction(state, {id: `reload:${weapon.id}`, busy_ms: weapon.reload_ms,
    events: [{kind: 'resource', key: ammoKey(weapon.id), delta: -amount, offset_ms: 0},
      {kind: 'resource', key: loadedKey(weapon.id), delta: amount, offset_ms: weapon.reload_ms}]}, []);
  return next ? {status: 'RELOADED', state: next} : {status: 'INVALID', reason: 'RELOAD_LEDGER_REFUSED'};
}

// Execute an explicit sequence, with supply acquisition only when ammunition is
// insufficient. The caller may compare sequences; this function does not claim
// that a failed sequence proves every possible fighting strategy impossible.
export function executeResourcePlan({weapons, providers, steps, deadline_ms = Number.MAX_SAFE_INTEGER, targets = {}}) {
  let state = combatResources(weapons, providers, targets);
  // This state is private to this execution. With no pending event there is
  // nothing to replay: do not clone the ever-growing trace for every beam tick.
  const advanceOwned=(s,time)=>{
    if(!integer(time)||time<s.time)return null;
    if(s.pending.length)return advance(s,time);
    s.time=time;return s;
  };
  const byId = new Map(weapons.map(w => [w.id, w]));
  const taken = [], unresolvedRefills = new Set(), stepShots = [], completedStepEffects = [];
  let lastRequiredAt = 0, completed = 0, previous = null;
  // Issued actions and their detached effects are different progress measures.
  // Report only effects committed by shots actually fired, without advancing a
  // failed execution or crediting any future, unissued part of its plan.
  const effectProgress = () => {
    const committed = stepShots.length ? lastRequiredAt : null;
    return {committed_effect_until_ms: committed,
      pending_effect_until_ms: committed !== null && committed > state.time ? committed : null,
      completed_effect_steps: completedStepEffects.filter(at => at <= state.time && at <= deadline_ms).length,
      completed_step_effect_at_ms: [...completedStepEffects]};
  };
  const fail = (status, reason, detail = {}) => ({status, reason, state, supplies_taken: taken,
    unresolved_refills: [...unresolvedRefills], completed_steps: completed, ...effectProgress(), ...detail});
  for (const [index, step] of steps.entries()) {
    let stepEffectAt = 0;
    let dependencyReady = 0, dependencyDeadline = Infinity;
    if (step.after_step) {
      const {index: dependency, delay_ms} = step.after_step;
      if (!integer(dependency) || dependency >= index || !integer(delay_ms) || !integer(stepShots[dependency])) {
        return fail('UNKNOWN', 'ACTION_DEPENDENCY_REQUIRED', {index});
      }
      // A detached effect can run while another weapon is used. Wait only for
      // its still outstanding part; supplies and reloads shift the source time.
      dependencyReady = stepShots[dependency] + delay_ms;
      if (step.after_step.max_delay_ms !== undefined) {
        if (!integer(step.after_step.max_delay_ms) || step.after_step.max_delay_ms < delay_ms) {
          return fail('UNKNOWN', 'ACTION_DEPENDENCY_WINDOW_REQUIRED', {index});
        }
        dependencyDeadline = stepShots[dependency] + step.after_step.max_delay_ms;
      }
    }
    const weapon = byId.get(step.weapon_id);
    if (step.release_ms !== undefined && !integer(step.release_ms)
      || step.damage_per_round !== undefined && (!Number.isFinite(step.damage_per_round)
        || step.damage_per_round <= 0 || !targets[step.target_id])) return fail('UNKNOWN', 'ACTION_DAMAGE_OR_RELEASE_REQUIRED', {index});
    if (!weapon || !integer(step.rounds) || step.rounds < 1
      || !Number.isSafeInteger(step.interval_ms) || step.interval_ms < 1
      || !integer(step.prepare_ms) || !integer(step.effect_delay_ms)) return fail('UNKNOWN', 'ACTION_INPUT_REQUIRED', {index});
    const acquire = step.acquire_ms ?? step.prepare_ms, charge = step.charge_ms ?? 0, warmup = step.warmup_ms ?? 0;
    if (![acquire, charge, warmup, step.work_ms ?? 0].every(integer)) return fail('UNKNOWN', 'ACTION_TIMING_REQUIRED', {index});
    if (!integer(weapon.available_at_ms ?? 0)) return fail('UNKNOWN','WEAPON_AVAILABILITY_REQUIRED',{index});
    state = advanceOwned(state,Math.max(state.time,weapon.available_at_ms ?? 0));
    const switching = previous && previous.weapon_id !== weapon.id;
    if (switching) {
      if (!integer(step.switch_ms)) return fail('UNKNOWN', 'WEAPON_SWITCH_TIME_REQUIRED', {index});
      state = advanceOwned(state, state.time + step.switch_ms);
    }
    // Changing feed mode mid-plan would need a separate mode-switch/heat state.
    if (previous?.weapon_id === weapon.id && previous.mode_id !== step.mode_id) return fail('UNKNOWN', 'MODE_SWITCH_ROUTE_REQUIRED', {index});
    let priorShot = switching ? null : previous?.at ?? null;
    const laterRounds = steps.slice(index + 1).filter(s => s.weapon_id === weapon.id).reduce((n, s) => n + s.rounds, 0);
    const strategy = reloadChoice({...weapon, interval_ms: step.interval_ms, charge_ms: charge}).strategy;
    for (let shot = 0; shot < step.rounds; shot++) {
      let loaded = state.resources[loadedKey(weapon.id)].value;
      if (loaded === 0) {
        if (!weapon.renewable && state.resources[ammoKey(weapon.id)].value === 0) {
          const choices = providers.filter(p => state.resources[supplyKey(p.id)]?.value > 0
            && (weapon.refill?.[p.refill_kind] ?? 0) > 0)
            .sort((a, b) => (a.kind === 'public') - (b.kind === 'public') || a.take_ms - b.take_ms);
          let received = null, unknownReason = null;
          for (const p of choices) {
            const attempt = receiveSupply(state, p, weapons, providers);
            if (attempt.status === 'TAKEN') { received = attempt; break; }
            if (attempt.status === 'UNKNOWN') unknownReason = attempt.reason;
          }
          if (!received) {
            const unknown = unknownReason || unresolvedRefills.has(weapon.id) || providers.some(p =>
              state.resources[supplyKey(p.id)]?.value > 0 && !integer(weapon.refill?.[p.refill_kind]));
            return fail(unknown ? 'UNKNOWN' : 'INSUFFICIENT', unknown ? unknownReason ?? 'REFILL_AMOUNT_REQUIRED' : 'AMMUNITION_EXHAUSTED',
              {index, equipment_id: weapon.id});
          }
          for (const id of received.unresolved_refills) unresolvedRefills.add(id);
          state = received.state; taken.push({provider_id: received.provider_id, at_ms: received.completed_at_ms});
        }
        if (state.resources[loadedKey(weapon.id)].value === 0) {
          const reload = reloadWeapon(state, weapon);
          if (reload.status !== 'RELOADED') return fail(reload.status, reload.reason, {index});
          state = reload.state;
        }
      } else if (priorShot !== null && strategy === 'top_up_one' && loaded < weapon.capacity
        && step.rounds - shot + laterRounds > loaded && state.resources[ammoKey(weapon.id)].value > 0) {
        const partial = weapon.round_reload_profile.partial_one_ms;
        state = startAction(state, {id: `top-up:${weapon.id}`, busy_ms: partial, events: [
          {kind: 'resource', key: ammoKey(weapon.id), delta: -1, offset_ms: 0},
          {kind: 'resource', key: loadedKey(weapon.id), delta: 1, offset_ms: partial}]}, []);
        if (!state) throw new Error('PARTIAL_RELOAD_LEDGER_REFUSED');
      }
      // Throwing takes the player's hands after supply/reload, on every throw.
      // Its detached fuse does not block the next throw or taking supplies.
      // Reload and aim may happen while the detached field is working; only
      // the actual next shot waits for its dependency, not the whole reload.
      const at = Math.max(dependencyReady, shotAt({ready_at: state.time + (step.release_ms ?? 0), previous_at: priorShot, interval_ms: step.interval_ms,
        acquire_ms: acquire, warmup_ms: warmup, charge_ms: charge, work_ms: step.work_ms ?? 0, new_target: shot === 0}));
      if (at > dependencyDeadline) return fail('UNKNOWN', 'ACTION_DEPENDENCY_WINDOW_MISSED', {index});
      if (at > deadline_ms) return fail('INSUFFICIENT', 'ACTION_WINDOW_EXCEEDED', {index});
      state = advanceOwned(state, at);
      // Firing is an instantaneous event. The next firing time is constrained by
      // shotAt; we do not advance through recovery and then charge it again during
      // reload. Supply/reload still use occupied actions in the original ledger.
      const serial = state.trace.length, eventId = `shot:${serial}`;
      if (!applyEvent(state, {id: eventId, at, kind: 'resource', key: loadedKey(weapon.id), delta: -1})) {
        return fail('INVALID', 'SHOT_LEDGER_REFUSED', {index});
      }
      state.trace.push({kind: 'action', action_id: `fire:${weapon.id}:${index}:${shot}`, at});
      state.manual_until = at;
      const last = shot === step.rounds - 1;
      const individualDamage = step.damage_per_round !== undefined;
      const requiredAt = at + (last || individualDamage ? step.effect_delay_ms : 0);
      if ((last || individualDamage) && step.target_id) {
        const event = {id: `${eventId}:effect`, at: requiredAt, kind: 'damage', key: step.target_id,
          amount: individualDamage ? step.damage_per_round : targets[step.target_id]?.hp, order: [serial, 0]};
        if (requiredAt === at) {
          if (!applyEvent(state, event)) return fail('INVALID', 'EFFECT_LEDGER_REFUSED', {index});
        } else state.pending.push(event);
      }
      lastRequiredAt = Math.max(lastRequiredAt, requiredAt);
      stepEffectAt = Math.max(stepEffectAt, requiredAt);
      priorShot = at;
      previous = {weapon_id: weapon.id, mode_id: step.mode_id, at, interval_ms: step.interval_ms};
      stepShots[index] = at;
      if (requiredAt > deadline_ms) return fail('INSUFFICIENT', 'EFFECT_WINDOW_EXCEEDED', {index});
    }
    completed++;
    completedStepEffects.push(stepEffectAt);
  }
  const actionReady = previous ? previous.at + previous.interval_ms : state.time;
  state = advance(state, Math.max(state.time, ...state.pending.map(e => e.at)));
  if (Object.values(state.targets).some(t => t.neutralized_at == null || t.neutralized_at > deadline_ms)) {
    return fail('INSUFFICIENT', 'TARGET_WINDOW_EXCEEDED');
  }
  // A successful targetless plan already reports elapsed_ms at its declared
  // effect horizon. Keep its state clock consistent with that completion time.
  state = advanceOwned(state, Math.max(state.time, lastRequiredAt));
  return {status: 'COMPLETED', state, supplies_taken: taken, elapsed_ms: lastRequiredAt,
    step_last_shot_ms: stepShots,
    action_ready_ms: actionReady, unresolved_refills: [...unresolvedRefills], completed_steps: completed, ...effectProgress()};
}

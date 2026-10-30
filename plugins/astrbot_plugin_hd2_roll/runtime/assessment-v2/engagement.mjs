import {executeResourcePlan} from './resources.mjs';
import {projectileService, ordinaryModes, mediumRequest, statusTargetRequest} from './projectile.mjs';
import {repeatedBlastRoutes} from './blast-routes.mjs';
import {heavyEligibility} from './heavy-eligibility.mjs';
import {originalScore} from './score-scale.mjs';

const finite = n => typeof n === 'number' && Number.isFinite(n);
const PACK = 'backpack_b_1_supply_pack';
const CAR = 'vehicle_m_103_supply_fast_recon_vehicle';

export function resourceWeapons(rows) {
  const mounted = rows.flatMap(row=>[...(row.facts.mounted_accounts ?? []),...(row.facts.extra_accounts??[])]);
  return mounted.concat(rows.map(row => ({...row, inventory: row.facts.service_inventory ?? row.facts.inventory}))
    .filter(row => row.inventory.kind === 'finite' && !row.facts.supply_only)
    .filter(row => ['primary', 'secondary', 'support', 'throwables'].includes(row.category)||row.facts.executable_backpack)
    .filter(row => row.inventory.renewable || Number.isSafeInteger(row.inventory.total) && row.inventory.total > 0)
    .map(row => ({id: row.equipment_id, equipment_id:row.equipment_id, loaded: row.inventory.capacity,
      capacity: row.inventory.capacity, reserve: row.inventory.reserve,
      finite_heat_sinks:row.inventory.finite_heat_sinks,cooling_ms:row.inventory.cooling_ms,
      reserve_capacity: row.inventory.reserve, feed: row.inventory.feed, renewable: row.inventory.renewable === true,
      reload_ms: row.inventory.reload_ms ?? row.facts.reload_ms, refill: row.facts.service_refill ?? row.facts.refill,
      round_reload_profile: row.facts.modes.find(m => m.feed_bound && m.round_reload_profile)?.round_reload_profile})));
}

// Interaction durations are centralized engineering parameters, not wiki facts.
// The user has confirmed a nearby predeployed car and no turret use.
export function supplyProviders(rows, scenario) {
  const timing = scenario.supply.interaction_ms;
  if (!timing) throw new TypeError('SUPPLY_INTERACTION_SCENARIO_REQUIRED');
  const providers = [{id: 'public', kind: 'public', charges: scenario.supply.public_boxes, capacity: scenario.supply.public_boxes,
    refill_kind: 'public', ready_at_ms: 0, take_ms: timing.public, refills_pack: true}];
  if (rows.some(r => r.equipment_id === PACK)) providers.push({id: PACK, kind: 'backpack',
    charges: 4, capacity: 4, refill_kind: 'portable', ready_at_ms: 0, take_ms: timing.backpack});
  if (rows.some(r => r.equipment_id === CAR)) providers.push({id: CAR, kind: 'vehicle',
    charges: 8, capacity: 8, refill_kind: 'portable', ready_at_ms: scenario.supply.vehicle_nearby === true ? 0 : null, take_ms: timing.vehicle});
  return providers;
}

export function actionsFor(row, targets, ap, scenario, targetProfiles = {}) {
  const candidates = [];
  const panels = {...Object.fromEntries(targets.map(({target}) => [`${target.target_id}:${target.part_id}`, target])), ...targetProfiles};
  for (const mode of ordinaryModes(row.facts)) {
    const cloud = row.capabilities?.modes.find(m => m.mode_id === mode.mode_id)?.chaff;
    const request = targets.map(r => ({id: `${r.target.target_id}:${r.target.part_id}`, count: r.count}));
    if (cloud?.action_plan && !cloud.partial_cohort && JSON.stringify(request) === JSON.stringify(cloud.original_target_requests)) {
      candidates.push({mode, service: cloud, actions: cloud.action_plan});
      continue;
    }
    const mediumPlans=request.map(r=>row.capabilities?.modes.find(m=>m.mode_id===mode.mode_id)?.anti_medium.find(m=>m.target_id===r.id));
    if(mediumPlans.length&&mediumPlans.every(p=>p?.action_plan)) {
      const actions=[];
      for(const [i,p] of mediumPlans.entries())for(let n=0;n<targets[i].count;n++) {
        const offset=actions.length;
        actions.push(...p.action_plan.map(a=>({...a,assessment_target:request[i].id,
          ...(a.after_step?{after_step:{...a.after_step,index:a.after_step.index+offset}}:{})})));
      }
      candidates.push({mode,service:{...mediumPlans[0],cycle_ms:mediumPlans.reduce((n,p,i)=>n+p.cycle_ms*targets[i].count,0)},actions});
      continue;
    }
    const resolved = targets.map(({target}) => {
      const id = `${target.target_id}:${target.part_id}`;
      // The score and ledger consume the same selected medium part, BTK and
      // acquisition cost. A blocked head must not reappear in the ammo plan.
      return scenario.medium.some(t => t.id === id) ? mediumRequest(mode, id, panels, ap, scenario)
        : statusTargetRequest(mode,target,1,ap,scenario);
    });
    const service = projectileService(mode, resolved, ap, {handling_policy: scenario.handling});
    if (service.status !== 'ESTIMATE') continue;
    candidates.push({mode, service, resolved});
  }
  candidates.sort((a, b) => a.service.cycle_ms - b.service.cycle_ms);
  const best = candidates[0];
  if (!best) return null;
  if (best.actions) return structuredClone(best.actions);
  return targets.flatMap(({target, count}, index) => {
    const service = best.service, entry = service.entries[index], route = best.resolved[index].route;
    const acquire = entry.acquire_ms ?? service.handling?.acquire_ms ?? scenario.aim_ms;
    return Array.from({length: count}, () => ({
      weapon_id: best.mode.resource_id ?? row.equipment_id, mode_id: best.mode.mode_id, rounds: entry.btk,
      prepare_ms: Math.ceil(acquire + (best.mode.warmup_ms ?? 0) + (best.mode.charge_ms ?? 0)),
      acquire_ms: Math.ceil(acquire),
      charge_ms: Math.ceil(best.mode.charge_ms ?? 0), warmup_ms: Math.ceil(best.mode.warmup_ms ?? 0),
      work_ms: best.mode.work_ms ?? 0,
      switch_ms: scenario.weapon_switch_ms,
      interval_ms: Math.ceil(service.interval_ms), effect_delay_ms: entry.resolution
        ? Math.max(0, entry.resolution.death_ms - entry.resolution.last_shot_ms) : best.mode.detached_delay_ms ?? 0,
      ...(entry.resolution ? {threat_resolution: entry.resolution} : {}),
      assessment_target: `${target.target_id}:${target.part_id}`,
      measured_target: `${entry.target_id}:${entry.part_id}`, measured_part: entry.part_id,
      ...(route ? {route} : {}),
    }));
  });
}

export function assessMixedEndurance(rows, model, {require_role_floors = false, chaff_score_floor = 60} = {}) {
  const {scenario, targets, ap} = model;
  const weapons = resourceWeapons(rows), providers = supplyProviders(rows, scenario);
  const owners = new Set(weapons.map(w => w.equipment_id ?? w.id));
  const shooters = rows.filter(row => owners.has(row.equipment_id) && row.capabilities.status === 'PROJECTILE_ESTIMATE');
  const chaffTargets = scenario.chaff.map(({id, count}) => ({target: targets[id], count}));
  const mediumTargets = scenario.medium.map(({id, count}) => ({target: targets[id], count}));
  const choices = shooters.map(row => ({row,
    chaff: actionsFor(row, chaffTargets, ap, scenario, targets),
    medium: actionsFor(row, mediumTargets, ap, scenario, targets)}));
  const referenceMedium = model.equipment.find(r => r.equipment_id === model.references.anti_medium);
  const mediumRatios = (row, steps) => steps.map(step => {
    const m = row.capabilities.modes.find(m => m.mode_id === step.mode_id);
    const id = step.assessment_target ?? step.measured_target;
    const result = m?.anti_medium.find(t => t.target_id === id);
    const reference = referenceMedium.capabilities.modes.flatMap(m => m.anti_medium).find(t => t.target_id === id);
    return result?.response?.value > 0 && reference?.response?.value > 0
      ? result.response.value / reference.response.value : null;
  });
  const averageAdmitted = ratios => !require_role_floors || ratios.length > 0 && ratios.every(r => r !== null)
    && ratios.reduce((n, r) => n + r, 0) / ratios.length >= 1;
  const mediumAdmitted = (row, steps) => averageAdmitted(mediumRatios(row, steps));
  const profiles = choices.filter(c => c.medium && mediumAdmitted(c.row, c.medium))
    .map(c => ({actions: c.medium, ids: [c.row.equipment_id], policy: 'SINGLE_MEDIUM_WEAPON'}));
  // Keep the search bounded: fixed full-role plans plus two target-specialized
  // plans. Every selected route must be known, and their combined mean must meet
  // the benchmark before the shared ledger runs. No hidden per-enemy floor.
  for (const policy of ['FAST_TARGET_ROUTES', 'AMMO_EFFICIENT_TARGET_ROUTES']) {
    const assigned = mediumTargets.map(request => {
      const options = shooters.flatMap(row => {
        const steps = actionsFor(row, [request], ap, scenario, targets);
        if (!steps || require_role_floors && mediumRatios(row, steps).some(r => r === null)) return [];
        const response = row.capabilities.modes.find(m => m.mode_id === steps[0].mode_id)?.anti_medium.find(t => t.target_id === `${request.target.target_id}:${request.target.part_id}`);
        return [{row, steps, response}];
      });
      options.sort((a, b) => policy === 'FAST_TARGET_ROUTES'
        ? (a.response?.response?.effective_response_ms ?? Infinity) - (b.response?.response?.effective_response_ms ?? Infinity)
        : stockFraction(a) - stockFraction(b));
      return options[0];
    });
    if (assigned.every(Boolean) && averageAdmitted(assigned.flatMap(a => mediumRatios(a.row, a.steps)))) profiles.push({actions: assigned.flatMap(a => a.steps),
      ids: [...new Set(assigned.map(a => a.row.equipment_id))], policy});
  }
  let best = null;
  for (const light of choices.filter(c => c.row.category !== 'throwables' && c.chaff && (!require_role_floors ||
    (c.row.dimensions.chaff.score_origin==='MANUAL_CALIBRATED'?originalScore(c.row.dimensions.chaff.score,scenario.score_scale):c.row.dimensions.chaff.score) >= chaff_score_floor))) for (const medium of profiles) {
    // Interleave light and medium targets so a common inventory is spent once.
    // Supply cannot be optimistically reset between the two role tests.
    const wave = [], a = light.chaff.map((step, index) => ({...step, light_plan_index: index})), b = [...medium.actions];
    while (a.length || b.length) { wave.push(...a.splice(0, 7), ...b.splice(0, 2)); }
    const positions = new Map(wave.flatMap((step, index) => Number.isInteger(step.light_plan_index) ? [[step.light_plan_index, index]] : []));
    for (const step of wave) {
      if (step.after_step) step.after_step = {...step.after_step, index: positions.get(step.after_step.index)};
      delete step.light_plan_index;
    }
    const repeats = scenario.endurance_probe_waves;
    const result = executeResourcePlan({weapons, providers, steps: Array.from({length: repeats}, (_, repeat) => wave.map(step => ({...step,
      ...(step.after_step ? {after_step: {...step.after_step, index: step.after_step.index + repeat * wave.length}} : {})}))).flat()});
    const waves = result.completed_steps / wave.length;
    // In a stock-exhaustion result the last thrown field can still be working.
    // Report when committed damage resolves, not just when throwing stopped.
    const elapsed = Math.max(result.elapsed_ms ?? result.state.time, result.committed_effect_until_ms ?? 0);
    if (!best || waves > best.waves || waves === best.waves && elapsed < best.elapsed_ms) {
      best = {waves, status: waves >= 1 ? 'PASS' : 'UNKNOWN', plan_result: result.status,
        search_scope: 'FIXED_PARTIAL_PLANS; FAILURE_IS_NOT_EXHAUSTIVE',
        test_scope: 'SHARED_LETHAL_PART_AMMUNITION_BUDGET; NOT_FULL_THREAT_OR_LOADOUT_CERTIFICATION',
        score: null, score_scope:'RESOURCE_FEASIBILITY_ONLY; ENDURANCE_USES_TOTAL_AMMO_DAMAGE',
        light_weapon: light.row.equipment_id,
        medium_weapon: medium.ids.length === 1 ? medium.ids[0] : null, medium_weapons: medium.ids,
        plan_policy: medium.policy, role_floor_constrained: require_role_floors, chaff_score_floor,
        first_wave_actions: wave, supplies_taken: result.supplies_taken,
        ammo_remaining: result.state.resources, elapsed_ms: elapsed,
        actions_elapsed_ms: result.state.time, committed_effect_until_ms: result.committed_effect_until_ms ?? null,
        completed_effect_actions_at_stop: result.completed_effect_steps ?? null,
        completed_actions: result.completed_steps, actions_per_wave: wave.length,
        targets_per_wave: [...scenario.chaff, ...scenario.medium].reduce((n, t) => n + t.count, 0),
        probe_limit_waves: repeats, lower_bound: result.status === 'COMPLETED',
        reason: result.reason ?? null, assumptions: ['FIXED_INTERLEAVED_ROLE_PLAN', 'NO_EXTRA_AOE_OR_CONTROL_CREDIT', 'FINITE_MIDFIGHT_RESUPPLY']};
    }
  }
  return best ?? {status: 'UNKNOWN', score: null,
    reason: require_role_floors ? 'SHARED_PLAN_MEETING_ROLE_FLOORS_REQUIRED' : 'MIXED_WAVE_WEAPON_ROUTE_REQUIRED'};
}

function stockFraction(choice) {
  const account = choice.row.facts.mounted_accounts?.find(w=>w.id === choice.steps[0].weapon_id);
  if (account) return choice.steps[0].rounds / (account.loaded + account.reserve);
  const inv = choice.row.facts.service_inventory ?? choice.row.facts.inventory;
  return inv.renewable ? 0 : inv.total > 0 ? choice.steps[0].rounds / inv.total : Infinity;
}

export function heavyRoutes(row, targetId, model, {incidental_only=false}={}) {
  if(!incidental_only&&heavyEligibility(row,model).state==='BELOW_THRESHOLD')return [];
  const target = model.targets[`${targetId}:head`];
  if (!target?.fatal) return [];
  const {facts} = row, id = row.equipment_id, scenario = model.scenario;
  const routes = repeatedBlastRoutes(row, targetId, model);
  if (id === 'gp_20_ultimatum') {
    const mode = facts.modes.find(m => m.feed_bound && m.kind === 'projectile');
    routes.push({weapon_id: id, rounds: 1, prepare_ms: scenario.heavy_aim_ms,
      mode_id: mode?.mode_id, interval_ms: mode?.rpm > 0 ? Math.ceil(60000 / mode.rpm) : 100,
      effect_delay_ms: 0, route: 'USER_STABLE_ABDOMEN_ONE_SHOT', one_shot: true});
  } else if (model.blast_routes?.conditional_one_shots.includes(id)) {
    // This is a sufficient one-shot condition, not a made-up 6000 damage pool.
    // Outside it, a repeatable physical route may still qualify above.
    const body = model.targets[`${targetId}:main`];
    const delay = id === 'g_123_thermite' ? scenario.thermite_effect_ms : facts.fuse_ms;
    // Guided flight and proximity deployment still require their own timeline.
    const timingBound = id === 'g_123_thermite' || facts.fuse_kind === 'impact' && !/Guided/.test(facts.traits);
    if (body?.hp < model.blast_routes.one_shot_domain.main_hp_strictly_below && timingBound
      && Number.isSafeInteger(delay) && delay >= 0) routes.push({weapon_id: id,
      mode_id: facts.areas[0]?.id, rounds: 1,
      prepare_ms: 0, release_ms: scenario.throw_ms, interval_ms: 1, effect_delay_ms: delay,
      route: 'USER_STABLE_EXTRA_PART_ONE_SHOT', one_shot: true,
      condition: 'USER_CONFIRMED_STABLE_EXTRA_PART_HIT', capacity_interpretation: 'KILL_CONDITION_NOT_NUMERIC_DAMAGE'});
  }
  if (routes.length) return routes;
  if (facts.inventory.kind !== 'finite' && !facts.service_modes) return [];
  for (const mode of ordinaryModes(facts)) {
    if (!mode.feed_bound || mode.kind !== 'projectile') continue;
    // A low-AP exposed Impaler head may qualify only through a short actual
    // lethal-part route, never through a universal "can damage heavy" tag.
    const conventional = mode.pen >= scenario.heavy_scoring.minimum_penetration;
    const exposed = targetId === 'impaler';
    if (!conventional && !mode.delayed_heavy && (!incidental_only || !exposed)) continue;
    // High-direct AT uses its lethal direct hit. A low-AP exposed-head route
    // retains the same projectile's bound explosion, never a second body part.
    const routeMode = conventional&&!mode.delayed_heavy ? {...mode, explosion: null, unresolved_impact: false} : mode;
      const mainOnly=mode.target_policy==='MAIN_ONLY';
      const routeTarget=mainOnly?model.targets[`${targetId}:main`]:target;
      if(!routeTarget?.fatal)continue;
      const measure = projectileService(routeMode, [{target:routeTarget, count: 1}], model.ap);
    if (measure.status !== 'ESTIMATE' || exposed && !conventional && measure.first_to_last_ms > 2000) continue;
    if (/Guided/.test(mode.traits)) continue; // lock-on/impact route needs its own evidence
    routes.push({weapon_id: mode.resource_id ?? id, mode_id: mode.mode_id, rounds: measure.shots,
      prepare_ms: scenario.heavy_aim_ms + (mode.charge_ms ?? 0) + (mode.warmup_ms ?? 0),
      acquire_ms: scenario.heavy_aim_ms, charge_ms: mode.charge_ms ?? 0, warmup_ms: mode.warmup_ms ?? 0,
      work_ms: mode.work_ms ?? 0,
      interval_ms: Math.ceil(measure.interval_ms), effect_delay_ms: mode.detached_delay_ms??0,
        ...(mode.delayed_heavy?{damage_per_round:measure.entries[0].per_round_damage,target_hp:routeTarget.hp}:{}),
        route: mainOnly?'DISTRIBUTED_MAIN_HP':mode.delayed_heavy?'USER_BOUND_STICKY_OR_IMPACT_FATAL_PART':conventional ? 'LETHAL_DIRECT_PART' : 'FAST_EXPOSED_IMPALER_HEAD', one_shot: measure.shots === 1,
        condition: mainOnly?'ACCESSIBLE_MAIN_CONTACT; NO_PRECISE_HEAD_FOCUS':exposed && !conventional ? 'EXPOSED_HEAD_VISIBLE' : 'LETHAL_HEAD_VISIBLE',
      per_round_damage: measure.entries[0].per_round_damage});
  }
  return routes;
}

export function assessHeavyPairs(rows, model) {
  const weapons = resourceWeapons(rows), available = new Set(weapons.map(w => w.equipment_id ?? w.id));
  const providers = supplyProviders(rows, model.scenario);
  const roster = model.scenario.heavy_targets;
  const routes = Object.fromEntries(roster.map(id => [id, rows.filter(r => available.has(r.equipment_id)).flatMap(row => heavyRoutes(row, id, model))]));
  const cases = [];
  for (let a = 0; a < roster.length; a++) for (let b = a; b < roster.length; b++) {
    const targets = [roster[a], roster[b]];
    let best = null;
    for (const first of routes[targets[0]]) for (const second of routes[targets[1]]) {
      const pair = [{...first, target_id: 'first'}, {...second, target_id: 'second'}];
      for (const order of [pair, [...pair].reverse()]) {
        const plan = order.map(route => ({...route, switch_ms: model.scenario.weapon_switch_ms}));
        const result = executeResourcePlan({weapons, providers, deadline_ms: model.scenario.heavy_seconds * 1000,
          targets: {first: {hp: first.target_hp ?? 1}, second: {hp: second.target_hp ?? 1}}, steps: plan});
        if (result.status === 'COMPLETED' && (!best || result.elapsed_ms < best.elapsed_ms)) best = {...result, plan};
      }
    }
    cases.push(best ? {targets, status: 'PASS', elapsed_ms: best.elapsed_ms, supplies_taken: best.supplies_taken,
      actions: best.state.trace, ammo_remaining: best.state.resources, plan: best.plan,
      neutralized_at_ms: Object.fromEntries(Object.entries(best.state.targets).map(([id, t]) => [id, t.neutralized_at]))}
      : {targets, status: 'UNKNOWN', reason: 'NO_COMPLETED_RELIABLE_PLAN',
        search_scope: 'PARTIAL_ROUTES; FAILED_SEARCH_IS_NOT_PROVEN_IMPOSSIBILITY'});
  }
  return {status: cases.every(c => c.status === 'PASS') ? 'PASS' : cases.some(c => c.status === 'FAIL') ? 'FAIL' : 'UNKNOWN',
    cases, routes, case_stock_reset: true, resource_model: 'FINITE_RESUPPLY_IN_WINDOW'};
}

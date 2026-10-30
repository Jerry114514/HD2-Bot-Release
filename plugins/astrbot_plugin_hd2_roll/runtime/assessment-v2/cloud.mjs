import {shotDamage} from '../calibration/practical/endurance-capacity.mjs';
import {directDamage} from '../engine/damage.mjs';
import {executeResourcePlan} from './resources.mjs';

// Own-weapon assessment only. The projectile, cloud and every follow-up shot
// belong to this same mode; no second gun receives a pre-damaged target here.
export function cloudService(facts, mode, requests, ap, scenario, directService) {
  const area = facts.areas?.find(a => a.id === mode.area_component_id && a.status === 'Gas');
  const gas = facts.effects.find(e => e.id === area?.status);
  if (!area || !(gas?.normal > 0) || !(gas.duration_s > 0) || !scenario.cloud
    || directService.status !== 'ESTIMATE' || requests.some(r => r.target.part_id !== 'main')) return null;
  const targets = requests.flatMap(({target, count}) => Array.from({length: count}, () => target));
  const policy = scenario.cloud, seeds = [], refreshes = [], cleanup = [], affected = [];
  const fieldOnly=facts.category==='throwables', activation=mode.detached_delay_ms??0;
  // Gas grenades cover a field rather than a projectile's fixed seven-target
  // packet. Use the same declared dense spacing as the blast scene, clipped to
  // the sourced gas radius and this finite cohort. Speargun scenes stay separate.
  const spacing = fieldOnly ? scenario.area?.spacing_samples_m?.[0] : null;
  let radii = policy.contact_radii_m, packetSize = policy.packet_size;
  if (spacing > 0 && area.outer_radius_m > 0) {
    const extent = Math.min(targets.length, Math.ceil(area.outer_radius_m / spacing)), points = [];
    for (let x = -extent; x <= extent; x++) for (let y = -extent; y <= extent; y++) {
      const radius = Math.hypot(x, y) * spacing;
      if (radius <= area.outer_radius_m) points.push(radius);
    }
    radii = points.sort((a, b) => a - b).slice(0, targets.length);
    packetSize = radii.length;
  }
  // Simplified continuous exposure: one non-stacking 25-DPS field for its
  // source lifetime. No extra full lingering dose is appended when it expires.
  const exposureSeconds = area.duration_s > 0 ? area.duration_s : gas.duration_s;
  const duration = Math.ceil(exposureSeconds * 1000);
  const action = (target, hp) => {
    const hit = shotDamage(mode, {...target, hp}, ap);
    if (!(hit.per_shot_damage > 0)) return null;
    return {weapon_id: facts.id, mode_id: mode.mode_id, rounds: Math.ceil(hp / hit.per_shot_damage),
      prepare_ms: Math.ceil(directService.handling.acquire_ms + (mode.charge_ms ?? 0) + (mode.warmup_ms ?? 0)),
      acquire_ms: Math.ceil(directService.handling.acquire_ms), charge_ms: mode.charge_ms ?? 0, warmup_ms: mode.warmup_ms ?? 0,
      interval_ms: directService.interval_ms, effect_delay_ms: 0, switch_ms: scenario.weapon_switch_ms,
      measured_target: `${target.target_id}:${target.part_id}`, measured_part: target.part_id};
  };
  for (let offset = 0; offset < targets.length; offset += packetSize) {
    const packet = targets.slice(offset, offset + packetSize);
    // A declared reachable target is hit; aim difficulty is handled by the small
    // E/recoil timing correction, not a hidden probability of landing this shot.
    const selected = packet.reduce((best, t, i) => t.hp > packet[best].hp ? i : best, 0);
    const seedIndex = seeds.length, seed = action(packet[selected],fieldOnly?1:packet[selected].hp);
    if (!seed) return null;
    seeds.push(seed);
    let requiredExposure = 0;
    for (const [index, target] of packet.entries()) {
      if (index === selected&&!fieldOnly) continue;
      const distance = radii[index], contact = Number.isFinite(distance) && distance <= area.outer_radius_m;
      const tick = directDamage({normal: gas.normal, durable: gas.durable, penetration: gas.pen,
        durability: target.durable, armor: target.armor, equal_multiplier: ap.equal, greater_multiplier: ap.greater, rounding: 'none'});
      if (tick.status !== 'KNOWN') return null;
      // Continue the SAME non-stacking field with paid additional shots when
      // one cloud cannot finish a target. No full lingering dose is invented.
      const killMs = contact && tick.damage > 0 ? Math.ceil(target.hp / tick.damage * 1000) : null;
      if (killMs !== null) requiredExposure = Math.max(requiredExposure, killMs);
      const poison = killMs !== null ? target.hp : 0;
      const remaining = target.hp - poison;
      affected.push({instance_id: `light-${offset + index}`, target_id: target.target_id, initial_hp: target.hp,
        status_damage: poison, remaining_hp: remaining, distance_m: distance, contact,
        required_contact_ms: killMs, seed_index: seedIndex});
      if (remaining > 0) {
        const step = action(target, remaining);
        if (!step) return null;
        cleanup.push(step);
      }
    }
    seed.effect_delay_ms = activation+Math.min(requiredExposure, duration);
    let previousOffset = 0;
    const extraClouds = Math.max(0, Math.ceil(requiredExposure / duration) - 1);
    for (let n = 1; n <= extraClouds; n++) {
      const offset = Math.min(n * duration, requiredExposure - duration);
      const refresh = {...action(packet[selected], 1), rounds: 1, cloud_action: 'REFRESH_SAME_GROUND_FIELD',
        effect_delay_ms: activation+Math.min(duration, requiredExposure - offset),
        after_step: {index: seedIndex, delay_ms: offset, max_delay_ms: previousOffset + duration}};
      // Refresh shots are aimed at the field. Their direct-hit damage is not
      // also credited to one of the already-poisoned targets.
      refreshes.push(refresh); previousOffset = offset;
    }
  }
  const plan = [...seeds, ...refreshes, ...cleanup], shots = plan.reduce((n, a) => n + a.rounds, 0);
  const repeat = count => Array.from({length: count}, (_, cycle) => plan.map(a => ({...a,
    ...(a.after_step ? {after_step: {...a.after_step, index: a.after_step.index + cycle * plan.length}} : {})}))).flat();
  // Cadence probe has enough virtual reserve to measure feed timing. It does not
  // certify ammunition: the loadout's separate shared ledger uses real stock.
  const weapon = {id: facts.id, loaded: mode.capacity, capacity: mode.capacity, reserve: shots * 3,
    reserve_capacity: shots * 3, feed: facts.inventory.feed, reload_ms: mode.reload_ms,
    refill: {public: 0, portable: 0}, round_reload_profile: mode.round_reload_profile};
  if(fieldOnly)Object.assign(weapon,{loaded:shots*3,capacity:shots*3,reserve:0,reserve_capacity:0});
  const twice = executeResourcePlan({weapons: [weapon], providers: [], steps: repeat(2)});
  const thrice = executeResourcePlan({weapons: [weapon], providers: [], steps: repeat(3)});
  const first = executeResourcePlan({weapons: [weapon], providers: [], steps: plan});
  if ([first, twice, thrice].some(r => r.status !== 'COMPLETED')) return null;
  const period = thrice.elapsed_ms - twice.elapsed_ms;
  if (!(period > 0)) return null;
  const confusion=facts.effects.find(e=>e.id===area.second_status),controlPolicy=policy.pressure_control;
  let pressure=null;
  if(confusion?.duration_s>0 && controlPolicy?.residual_threat_fraction>=0 && controlPolicy.residual_threat_fraction<=1
    && controlPolicy.onset_delay_ms>=0) {
    // Compare alive-enemy time, not fake bonus kills. Only continuously covered
    // live targets receive relief. Paid refreshes keep the SAME confusion active;
    // no trailing duration is appended after contact ends or a target dies.
    const uncontrolled=affected.reduce((sum,t)=>sum+(first.step_last_shot_ms[t.seed_index]??0)+activation+(t.required_contact_ms??0),0)
      + (fieldOnly?0:seeds.reduce((sum,_,i)=>sum+(first.step_last_shot_ms[i]??0),0));
    const controlled=affected.reduce((sum,t)=>sum+(t.contact?Math.max(0,(t.required_contact_ms??0)-controlPolicy.onset_delay_ms):0),0);
    const avoided=controlled*(1-controlPolicy.residual_threat_fraction);
    pressure={uncontrolled_enemy_ms:uncontrolled,controlled_enemy_ms:controlled,
      residual_threat_fraction:controlPolicy.residual_threat_fraction,avoided_enemy_ms:avoided,
      score_ratio_gain:uncontrolled>0?uncontrolled/(uncontrolled-avoided):1,
      basis:controlPolicy.basis,application:controlPolicy.application,
      counts_as_kills:false,counts_as_safety:false};
  }
  return {...directService, shots, count: targets.length, inventory_required: shots,
    reloads: first.state.trace.filter(t => t.action_id === `reload:${facts.id}`).length,
    direct_fire_entries: directService.entries, entries: [],
    first_to_last_ms: first.elapsed_ms, neutralized_at_ms: first.elapsed_ms,
    effective_response_ms:Math.ceil(first.elapsed_ms/(pressure?.score_ratio_gain??1)),
    resolution_basis: 'SINGLE_WEAPON_DIRECT_AND_NONSTACKING_CLOUD_ON_MAIN_HP', cycle_ms: period,
    sustained: {status: 'ESTIMATE', pattern_ms: period, targets_per_pattern: targets.length,
      targets_per_second: targets.length * 1000 / period, scope: 'OWN_WEAPON_CADENCE; REAL_STOCK_CHECK_REQUIRED'},
    firing_rate_targets_s: targets.length * 1000 / period, pressure,
    action_plan: plan, original_target_requests: requests.map(r => ({id: `${r.target.target_id}:${r.target.part_id}`, count: r.count})),
    cloud: {source_component: area.id, source_ref: facts.source_ref, radius_m: area.outer_radius_m,
      policy: policy.contact_policy, packet_size: packetSize, spacing_m: spacing,
      geometry: fieldOnly && spacing > 0 ? 'FINITE_DENSE_GRID_CLIPPED_TO_SOURCED_GAS_RADIUS' : 'DECLARED_SEVEN_TARGET_CONTACT_PACKET',
      application:fieldOnly?'ONE_PAID_GRENADE_STARTS_FIELD; NO_DIRECT_KILL_SEED':'DIRECT_KILL_AND_OWN_FIELD',
      activation_ms:activation,gas_multiplier_basis:'UNRESOLVED_ENEMY_MULTIPLIER; ENGINEERING_BASELINE_1',
      damage_per_second: gas.normal, status_duration_s: gas.duration_s, exposure_seconds: exposureSeconds,
      own_ammo_rounds: shots, initial_clouds: seeds.length, refresh_clouds: refreshes.length,
      status_kills: affected.filter(t => t.remaining_hp === 0).length, targets: affected,
      control: {id: area.second_status, duration_s: facts.effects.find(e => e.id === area.second_status)?.duration_s ?? null,
        pressure, counts_as_instant_safety: false},
      conditions: ['DECLARED_CONTINUOUS_CLOUD_CONTACT; GROWTH_NOT_SIMULATED', 'KITE_DURING_STATUS_DELAY', 'NO_OTHER_WEAPON_USED',
        'DECLARED_TARGET_GEOMETRY; FIELD_GROWTH_AND_ENEMY_RETENTION_NOT_MEASURED']},
    assumptions: [...directService.assumptions, 'SAME_WEAPON_GAS_CAPABILITY',
      'PAID_REFRESH_EXTENDS_NONSTACKING_FIELD; NO_EXTRA_LINGER_OR_CROSS_WEAPON_COMBO']};
}

// Conditional close-defense route: gas disruption supports the already-declared
// backward dive. It is not a kill, invulnerability or guaranteed hard stun.
export function cloudCloseService(facts, mode, scenario, direct) {
  const policy = scenario.cloud;
  const area = facts.areas?.find(a => a.id === mode.area_component_id && a.status === 'Gas');
  const confusion = facts.effects?.find(e => e.id === area?.second_status);
  if (!policy?.control_score || direct.status !== 'ESTIMATE' || !(area?.duration_s > 0)
    || !(confusion?.duration_s > 0) || !Number.isSafeInteger(policy.close_activation_delay_ms)
    || policy.close_activation_delay_ms < 0 || !(scenario.dive_ms > 0)) return null;
  const radii = policy.close_contact_radii_m?.slice(0, scenario.close_count);
  // Use the inner region, not the fully-grown maximum cloud radius.
  if (radii?.length !== scenario.close_count || radii.some(r => !(r >= 0 && r <= area.inner_radius_m))) return null;
  const fire = Math.ceil(direct.handling.acquire_ms + (mode.charge_ms ?? 0) + (mode.warmup_ms ?? 0));
  const distance = scenario.close_start_distance_m + (scenario.close_distance_m - scenario.close_start_distance_m)
    * Math.min(1, fire / scenario.dive_ms);
  if (!(distance >= (mode.arming_distance_m ?? 0))) return null;
  const controlAt = fire + policy.close_activation_delay_ms, escaped = scenario.dive_ms;
  if (controlAt >= escaped || controlAt + confusion.duration_s * 1000 < escaped) return null;
  return {...direct, kind: 'CONTROL_ESCAPE', shots: 1, inventory_required: 1,
    first_to_last_ms: null, neutralized_at_ms: null, response_time_ms: escaped, safe_escape_ms: escaped,
    direct_kill_estimate_ms: direct.first_to_last_ms, sustained: null, entries: [], reloads: 0,
    resolution_basis: 'CONDITIONAL_GAS_DISRUPTION_AND_BACKWARD_DIVE; NOT_KILL_OR_HARD_STUN',
    control: {id: confusion.id, duration_s: confusion.duration_s, applied_at_ms: controlAt,
      controlled_retreat_ms: escaped - controlAt, target_count: scenario.close_count,
      contact_radii_m: radii, activation_delay_ms: policy.close_activation_delay_ms,
      basis: policy.close_control_basis, counts_as_instant_safety: false},
    assumptions: [...direct.assumptions, 'DECLARED_COMPACT_HUNTER_GROUP_IN_INNER_GAS_RADIUS',
      'ENGINEERING_ATTACK_SETTLE_DELAY; NOT_MEASURED', 'DIVE_OVERLAPS_SHOT_AND_GAS_ONSET', 'NOT_A_GUARANTEE_AGAINST_ONGOING_ATTACKS']};
}

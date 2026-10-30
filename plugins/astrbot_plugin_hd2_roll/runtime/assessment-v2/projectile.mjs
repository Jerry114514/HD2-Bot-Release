import {combatShotDamage as shotDamage} from './elemental-damage.mjs';
import {shotAt, reloadChoice, sustainedTempo} from './timing.mjs';
import {capacityCurve} from './formulas.mjs';
import {handlingTiming, mediumResponse} from './handling.mjs';
import {cloudService, cloudCloseService} from './cloud.mjs';
import {resolveMediumTarget} from './medium-resolution.mjs';
import {areaChaffService} from './area.mjs';
import {fragmentHit,mainFragmentContacts,blastAt} from './fragments.mjs';
import {boundFire} from './status.mjs';

const finite = n => typeof n === 'number' && Number.isFinite(n);
const round = n => Math.round(n * 100) / 100;

export function projectileService(mode, targets, ap, {handling_policy} = {}) {
  if (mode?.proximity_m>0) {
    const blast=blastAt(mode.explosion,mode.proximity_m);
    if (!blast) return {status:'UNKNOWN',reason:'PROXIMITY_BLAST_FALLOFF_REQUIRED'};
    mode={...mode,explosion:blast};
  }
  if (!mode || mode.kind !== 'projectile' || !mode.feed_bound || !(mode.rpm > 0)
    || !Number.isSafeInteger(mode.capacity) || mode.capacity < 1 || mode.unresolved_impact) {
    return {status: 'UNKNOWN', reason: 'FIRING_MODE_OR_FEED_UNRESOLVED'};
  }
  if (/Continuous|Beam/.test(mode.traits) || /Charge/.test(mode.traits) && !finite(mode.charge_ms)) {
    return {status: 'UNKNOWN', reason: 'REPEATED_FIRING_TIMING_UNRESOLVED'};
  }
  const handling = handling_policy ? handlingTiming(mode.handling, handling_policy) : null;
  if (handling && handling.status !== 'ESTIMATE') return handling;
  const mechanicalInterval = Math.max(60000 / mode.rpm, mode.charge_ms ?? 0);
  const recoilLimited = Math.max(mechanicalInterval, handling?.recoil_settle_ms ?? 0);
  const interval = Math.ceil(handling_policy?.max_recoil_interval_extra_fraction >= 0
    ? Math.min(recoilLimited, mechanicalInterval * (1 + handling_policy.max_recoil_interval_extra_fraction)) : recoilLimited);
  const acquireBase = Math.max(handling?.acquire_ms ?? 0,mode.acquire_floor_ms ?? 0), acquire = Math.ceil(acquireBase);
  const entries = [];
  let shots = 0, count = 0;
  for (const request of targets) {
    if (!request.target || !Number.isSafeInteger(request.count) || request.count < 1) return {status: 'UNKNOWN', reason: 'TARGET_PROFILE_REQUIRED'};
    const multiplier = request.acquire_multiplier ?? 1;
    if (!(finite(multiplier) && multiplier >= 1)) return {status: 'UNKNOWN', reason: 'WEAKPOINT_ACCESS_COST_REQUIRED'};
    // A weakpoint's blast resistance is local to that part. Redirected Main
    // damage is not an extra hit against the small lethal part.
    const resistance = request.explosion_resistance;
    if (resistance !== undefined && !(finite(resistance) && resistance >= 0 && resistance <= 1)) {
      return {status: 'UNKNOWN', reason: 'WEAKPOINT_EXPLOSION_RESISTANCE_REQUIRED'};
    }
    const hitMode = mode.explosion && resistance !== undefined ? {...mode, explosion: resistance === 1 ? null
      : {...mode.explosion, normal: mode.explosion.normal * (1 - resistance), durable: mode.explosion.durable * (1 - resistance)}} : mode;
    let hit = shotDamage(hitMode, request.target, ap), target = request.target;
    const fragment=fragmentHit(mode,target,ap,mainFragmentContacts(mode,target));
    if (fragment.status!=='KNOWN') return {status:'UNKNOWN',reason:'FRAGMENT_CONTACT_REQUIRED'};
    if (fragment.damage>0) hit={...hit,state:'KNOWN',per_shot_damage:(hit.per_shot_damage??0)+fragment.damage};
    const statusOnly=mode.status_only_lower_bound?boundFire(mode,request,ap):null;
    if(mode.direct_damage_unresolved&&!statusOnly)return {status:'UNKNOWN',reason:'DIRECT_DAMAGE_OR_BOUND_STATUS_REQUIRED'};
    let btk = hit.per_shot_damage > 0 ? Math.ceil(target.hp / hit.per_shot_damage)
      :statusOnly?.dps>0&&mode.work_ms>0?Math.ceil((statusOnly.ignition_contact_ms
        +Math.max(0,target.hp/statusOnly.dps*1000-statusOnly.duration_ms))/mode.work_ms):Infinity;
    // 100% ExDR redirects this explosion once to Main; it does not damage the
    // immune head. Direct-to-Main transfer is conservatively omitted here.
    if (mode.explosion && resistance === 1 && request.redirected_explosion_target) {
      const main = request.redirected_explosion_target;
      const redirected = shotDamage({...mode, ...mode.explosion, explosion: null, pellets: 1}, main, ap);
      const mainBtk = redirected.per_shot_damage > 0 ? Math.ceil(main.hp / redirected.per_shot_damage) : Infinity;
      if (mainBtk < btk) { hit = redirected; target = main; btk = mainBtk; }
    }
    if(statusOnly&&finite(btk))hit={...hit,state:'KNOWN',per_shot_damage:0};
    if (hit.state === 'AP_BLOCKED') return {status: 'UNABLE', reason: 'TARGET_AP_BLOCKED', target_id: request.target.target_id};
    if (hit.state !== 'KNOWN' || !(hit.per_shot_damage > 0)&&!statusOnly) return {status: 'UNKNOWN', reason: 'HIT_DAMAGE_UNRESOLVED'};
    if (request.burst_rounds !== undefined) {
      if (!request.resolution || !Number.isSafeInteger(request.burst_rounds) || request.burst_rounds<1
        || request.burst_rounds < btk && !(request.allow_status_burst&&boundFire(mode,request,ap))) {
        return {status: 'UNKNOWN', reason: 'INVALID_CONSTITUTION_FOLLOWUP_BURST'};
      }
      btk = request.burst_rounds;
    }
    entries.push({target_id: target.target_id, part_id: target.part_id, count: request.count,
      aimed_target: `${request.target.target_id}:${request.target.part_id}`,
      damage_route: target === request.target ? 'LOCAL_PART' : 'REDIRECTED_EXPLOSION_TO_MAIN',
      btk, rounds: btk * request.count, per_round_damage: hit.per_shot_damage,
      fragment_damage_per_round:fragment.damage,fragment_contacts:fragment.contacts,
      acquire_ms: Math.ceil(acquireBase * multiplier),
      residual_threat_ms: target.residual_threat_ms ?? null});
    shots += btk * request.count; count += request.count;
  }
  if (!count) return {status: 'UNKNOWN', reason: 'EMPTY_TARGET_SET'};
  if (mode.disposable && shots > mode.capacity) return {status: 'UNABLE', reason: 'FINITE_STOCK_BELOW_SCENARIO_DEMAND'};
  const topUp = reloadChoice({...mode, interval_ms: interval}).strategy === 'top_up_one';
  let elapsed = mode.available_at_ms ?? 0;
  let loaded = mode.capacity, fired = 0, reloads = 0, inserts = 0, neutralizedAt = 0, effectiveAt = 0, partBreakAt = 0;
  // Only the delay that extends readiness is charged. Recoil, the mechanical
  // interval, reloading and selecting the next target are not summed blindly.
  for (const [index, entry] of entries.entries()) for (let n = 0; n < entry.count; n++) {
    const times = [];
    for (let shot = 0; shot < entry.btk; shot++) {
    let ready = elapsed;
    if (fired > 0) {
      if (loaded === 0) {
        if (!finite(mode.reload_ms)) return {status: 'UNKNOWN', reason: 'RELOAD_TIME_REQUIRED'};
        ready += mode.reload_ms;
        loaded = mode.capacity; reloads++;
      } else if (topUp && shots - fired > loaded) {
        ready += mode.round_reload_profile.partial_one_ms;
        loaded++; inserts++;
      }
    }
    elapsed = shotAt({ready_at: ready, work_ms: mode.work_ms ?? 0, previous_at: fired ? elapsed : null, interval_ms: interval,
      acquire_ms: entry.acquire_ms, warmup_ms: mode.warmup_ms ?? 0, charge_ms: mode.charge_ms ?? 0, new_target: shot === 0});
    loaded--; fired++;
    times.push(elapsed);
    }
    if (targets[index].resolution) {
      const delay = mode.detached_delay_ms ?? 0;
      const result = resolveMediumTarget(mode, targets[index], times.map(t => t + delay), ap);
      if (result.status === 'ESTIMATE') result.last_shot_ms -= delay;
      if (result.status !== 'ESTIMATE') return result;
      entry.resolution = result;
      entry.residual_threat_ms = Math.max(0, result.death_ms - elapsed);
      neutralizedAt = neutralizedAt === null ? null : Math.max(neutralizedAt, result.death_ms);
      effectiveAt = Math.max(effectiveAt, result.effective_response_ms);
      partBreakAt = Math.max(partBreakAt, result.part_break_ms ?? result.death_ms);
    } else {
      neutralizedAt = neutralizedAt === null || entry.residual_threat_ms === null ? null
        : Math.max(neutralizedAt, elapsed + entry.residual_threat_ms + (mode.detached_delay_ms ?? 0));
      effectiveAt = Math.max(effectiveAt, elapsed + (mode.detached_delay_ms ?? 0));
      partBreakAt = Math.max(partBreakAt, elapsed);
    }
  }
  const needsReload = loaded === 0;
  const recovery = needsReload && !finite(mode.reload_ms) ? null
    : Math.max(interval, entries[0].acquire_ms, needsReload ? mode.reload_ms + (mode.charge_ms ?? 0) : 0);
  const cycle = recovery === null ? null : elapsed + recovery;
  return {status: 'ESTIMATE', shots, count, first_to_last_ms: elapsed, neutralized_at_ms: neutralizedAt,
    ...(mode.detached_delay_ms > 0 || entries.some(e=>e.resolution?.fire?.ignition_contact_ms)
      ? {response_time_ms: effectiveAt} : {}),
    ...(targets.some(r => r.resolution) ? {effective_response_ms: effectiveAt, first_part_break_ms: partBreakAt} : {}),
    resolution_basis: targets.some(r => r.resolution) ? 'BLEED_MAIN_TRANSFER_BOUND_CLEAVE_AND_CONTROL_ESTIMATE'
      : neutralizedAt === null ? 'LETHAL_PART_BREAK_ONLY' : 'SOURCE_PANEL_NEUTRALIZATION_ESTIMATE',
    cycle_ms: cycle, interval_ms: interval, mechanical_interval_ms: mechanicalInterval,
    recovery_after_ms: recovery, next_round_needs_reload: needsReload,
    reloads, partial_inserts: inserts, entries, handling,
    sustained: mode.disposable ? {status: 'FINITE_ONLY', targets_per_pattern: count, pattern_ms: null, targets_per_second: 0,
      reason: 'NO_REPLACEMENT_WEAPON_CREDITED'} : sustainedTempo({...mode, entries, interval_ms: interval, acquire_ms: acquire}),
    firing_rate_targets_s: cycle > 0 ? 1000 * count / cycle : null,
    inventory_required: shots, overlap: inserts ? 'top_up_one' : null,
    assumptions: ['SOURCE_PART_ALL_HITS', 'ONE_BOUND_EXPLOSION_PER_PRIMARY_TARGET',
      targets.some(r => r.resolution) ? 'ONLY_EXPLICIT_BOUND_CLEAVE; NO_NEIGHBOUR_HITS' : 'NO_EXTRA_PART_OR_NEIGHBOUR_HITS',
      'READINESS_CONSTRAINTS_OVERLAP; CHARGE_AFTER_RELOAD', 'NO_DAMAGE_OR_BTK_HANDLING_PENALTY']};
}

export function ordinaryModes(facts) {
  return (facts.service_modes ?? facts.modes).filter(m => m.kind === 'projectile' && m.feed_bound)
    .filter(m => !['plas_101_purifier', 'plas_15_loyalist'].includes(facts.id)
      || m.charge_ms === Math.max(...facts.modes.map(x => x.charge_ms ?? 0)));
}

// A paid short burst can finish Main through its own guaranteed burn. The
// same request is used in the score and finite-ammunition ledger.
export function statusTargetRequest(mode,target,count,ap,scenario,objective='throughput') {
  const base={target,count};
  if(target?.part_id!=='main'||!boundFire(mode,base,ap))return base;
  const request={...base,explosion_resistance:0,resolution:{main:target,policy:scenario.medium_resolution}};
  const options={handling_policy:scenario.handling};
  const initial=projectileService(mode,[{...request,count:1}],ap,options);
  if(initial.status!=='ESTIMATE')return base;
  let best=request,bestTime=objective==='response'?projectileService(mode,[request],ap,options).neutralized_at_ms:Infinity;
  for(let rounds=1;rounds<initial.shots;rounds++) {
    const candidate={...request,burst_rounds:rounds,allow_status_burst:true};
    const measured=projectileService(mode,[{...candidate,count:objective==='response'?count:1}],ap,options);
    if(measured.status!=='ESTIMATE')continue;
    if(objective==='throughput')return candidate;
    if(measured.neutralized_at_ms<bestTime){best=candidate;bestTime=measured.neutralized_at_ms;}
  }
  return best;
}

// Keep scenario identity (e.g. Hive Guard) separate from the actual lethal part.
// This fallback is evidence-bound, not an arbitrary search over every weak limb.
export function mediumRequest(mode, id, targets, ap, scenario, count = 1) {
  const policy = scenario.medium_weakpoints;
  if (mode.target_policy === 'MAIN_ONLY') {
    const main = targets[`${id.split(':')[0]}:main`];
    return {target:main,count,acquire_multiplier:1,explosion_resistance:0,
      resolution:scenario.medium_resolution ? {main,policy:scenario.medium_resolution} : undefined,
      route:{kind:'AUTOMATIC_CONTACT_MAIN_ONLY',measured_target:`${id.split(':')[0]}:main`,acquire_multiplier:1}};
  }
  // Precision belongs to the route, not to the weapon's AP. A penetrable head
  // still requires aimed acquisition; broad body saturation has its own cost.
  const preciseCost = route => Math.max(route?.acquire_multiplier ?? policy?.acquire_multiplier ?? 1,
    policy?.precise_acquire_minimum_by_enemy?.[id.split(':')[0]] ?? 1,
    policy?.precise_acquire_minimum_by_target?.[route?.target_id ?? id] ?? 1);
  const baseMultiplier = preciseCost();
  const resolution = scenario.medium_resolution ? {main: targets[`${id.split(':')[0]}:main`], policy: scenario.medium_resolution} : undefined;
  const base = {target: targets[id], count, explosion_resistance: policy?.default_explosion_resistance?.[id],
    ...(resolution ? {resolution} : {}),
    acquire_multiplier: baseMultiplier,
    redirected_explosion_target: targets[`${id.split(':')[0]}:main`],
    route: {kind: 'DEFAULT_FRONT_PART', measured_target: id, acquire_multiplier: baseMultiplier,
      aim_class: 'PRECISE_PART'}};
  const candidates = [base];
  for (const [kind, route] of [['FRONTAL_WEAKPOINT', policy?.alternatives?.[id]],
    ['BODY', policy?.body_routes?.[id]],
    ['EXPLOSIVE_BODY', mode.explosion ? policy?.explosive_body_routes?.[id] : null]]) {
    if (!route) continue;
    const target = targets[route.target_id], precise = kind === 'FRONTAL_WEAKPOINT';
    const multiplier = precise ? preciseCost(route) : route.acquire_multiplier;
    candidates.push({target: target?.fatal === true ? target : null, count,
      ...(resolution ? {resolution} : {}),
      acquire_multiplier: multiplier, explosion_resistance: route.explosion_resistance,
      redirected_explosion_target: targets[`${id.split(':')[0]}:main`],
      route: {kind, measured_target: route.target_id, condition: route.condition, acquire_multiplier: multiplier,
        aim_class: precise ? 'PRECISE_PART' : 'BROAD_BODY',
        source_url: route.source_url, blast_policy: 'LOCAL_EXDR; REDIRECT_ONCE_TO_MAIN; NO_DUPLICATE_PART_DAMAGE'}});
  }
  const measure = request => {
    const service = projectileService(mode, [{...request, count: 1}], ap, {handling_policy: scenario.handling});
    return {request, service, response: mediumResponse(service, scenario.medium_response)};
  };
  const measured = candidates.flatMap(request => {
    const initial = measure(request), choices = [initial];
    if (request.resolution&&boundFire(mode,request,ap)&&initial.service.status==='ESTIMATE') {
      for(let rounds=1;rounds<initial.service.shots;rounds++)choices.push(measure({...request,burst_rounds:rounds,allow_status_burst:true,
        route:{...request.route,burst_policy:'GUARANTEED_SINGLE_PACKET_FIRE_TO_MAIN'}}));
    }
    if (request.resolution && request.target?.constitution?.hp > 0 && initial.service.status === 'ESTIMATE'
      && initial.service.entries[0].part_id === request.target.part_id) {
      const damage = initial.service.entries[0].per_round_damage;
      const last = Math.min(mode.total ?? Infinity, Math.ceil((request.target.hp + request.target.constitution.hp) / damage) + 1);
      // Enumerate short follow-up bursts as well as stop-and-bleed. The extra
      // round covers the conservative no-overflow boundary. Each costs ammo.
      for (let rounds = initial.service.shots + 1; rounds <= last; rounds++) {
        choices.push(measure({...request, burst_rounds: rounds,
          route: {...request.route, burst_policy: 'FOLLOWUP_ON_CONSTITUTION_ZONE'}}));
      }
    }
    return choices;
  }).filter(c => c.response.status === 'ESTIMATE').sort((a, b) => b.response.value - a.response.value);
  const best = measured[0];
  if (!best) return candidates.find(c => !c.target) ?? base;
  const entry = best.service.entries[0];
  return {...best.request, route: {...best.request.route, aimed_target: best.request.route.measured_target,
    measured_target: `${entry.target_id}:${entry.part_id}`, damage_route: entry.damage_route}};
}

export function ordinaryCapabilities(facts, targets, ap, scenario) {
  if (facts.supply_only || facts.known_noncombat) return {status: 'UTILITY_ONLY', modes: []};
  if (!['primary', 'secondary', 'support'].includes(facts.category) && !facts.service_modes) return {status: 'OTHER_MECHANISM', modes: []};
  if (facts.inventory.kind !== 'finite' && !facts.service_modes) return {status: 'OTHER_MECHANISM', reason: 'THERMAL_OR_UNLIMITED', modes: []};
  const results = [];
  for (const mode of ordinaryModes(facts)) {
    // The uncharged Purifier/charge-pistol listing is a mechanical maximum, not
    // evidence that the player repeats ten+ taps per second under pressure.
    const options = {handling_policy: scenario.handling};
    const chaffRequests = scenario.chaff.map(({id, count}) => statusTargetRequest(mode,targets[id],count,ap,scenario));
    const knownRequests=mode.status_only_lower_bound?chaffRequests.filter(r=>boundFire(mode,r,ap)):chaffRequests;
    const requestedCount=chaffRequests.reduce((n,r)=>n+r.count,0);
    let directChaff = projectileService(mode, knownRequests, ap, options);
    if(directChaff.status!=='ESTIMATE'&&knownRequests.some(r=>r.allow_status_burst)&&!mode.status_only_lower_bound)
      directChaff=projectileService(mode,knownRequests.map(({target,count})=>({target,count})),ap,options);
    if(directChaff.status==='ESTIMATE'&&knownRequests.length!==chaffRequests.length)directChaff={...directChaff,
      partial_cohort:true,requested_count:requestedCount,status_only_lower_bound:true,
      resolution_basis:'BOUND_FIRE_ONLY; OTHER_TARGETS_AND_DIRECT_DAMAGE_UNRESOLVED'};
    if(directChaff.reason==='FINITE_STOCK_BELOW_SCENARIO_DEMAND') {
      // Measure the finite useful opening stock. A partial cohort must never
      // be reused as a complete 28-enemy physical plan.
      const remaining=knownRequests.map(r=>({...r})),subset=[];
      let best=null;
      while(remaining.some(r=>r.count>0)) {
        let stop=false;
        for(const request of remaining.filter(r=>r.count>0)) {
          subset.push({...request,count:1});request.count--;
          const result=projectileService(mode,subset,ap,options);
          if(result.status!=='ESTIMATE'){stop=true;break;}best=result;
        }
        if(stop)break;
      }
      if(best)directChaff={...best,requested_count:requestedCount,partial_cohort:true,
        resolution_basis:'PAID_FINITE_OPENING_STOCK; PARTIAL_COHORT_NOT_FULL_WAVE'};
    }
    const cloud = cloudService(facts, mode, chaffRequests, ap, scenario, directChaff);
    const area = areaChaffService(facts, mode, chaffRequests, ap, scenario);
    const chaffRate = s => s.status !== 'ESTIMATE' ? -1 : (s.sustained?.status === 'FINITE_ONLY'
      ? 1000*s.count/(s.neutralized_at_ms??s.first_to_last_ms) : s.sustained?.targets_per_second??-1)
      *(s.requested_count?s.count/s.requested_count:1)*(s.pressure?.score_ratio_gain??1);
    const chaffCandidates=[directChaff,cloud,area].filter(Boolean).map(service=>{
      if(facts.category!=='throwables'||service.status!=='ESTIMATE'||!(service.neutralized_at_ms>0)
        ||!(service.sustained?.targets_per_second>0))return service;
      // Finite grenades cannot claim an indefinitely primed pipeline of future
      // explosions. Throws may overlap their fuses, but this cohort still waits
      // for the last damaging effect. Inventory remains a separate paid gate.
      return {...service,sustained:{...service.sustained,
        cadence_targets_per_second:service.sustained.targets_per_second,
        targets_per_second:Math.min(service.sustained.targets_per_second,1000*service.count/service.neutralized_at_ms),
        throughput_basis:'FINITE_THROW_BURST_INCLUDING_LAST_EFFECT'}};
    });
    const chaff = chaffCandidates.sort((a,b)=>chaffRate(b)-chaffRate(a))[0];
    const mediums = scenario.medium.map(({id, count}) => {
      const request = mediumRequest(mode, id, targets, ap, scenario);
      const service = projectileService(mode, [request], ap, options);
      const base={...service,target_id:id,cohort_count:count,route:request.route,
        response:mediumResponse(service,scenario.medium_response)};
      if(facts.category!=='throwables')return base;
      const main=targets[`${id.split(':')[0]}:main`];
      if(!main)return base;
      const cloudRequests=[{target:main,count:1}],direct=projectileService(mode,cloudRequests,ap,options);
      const gas=cloudService(facts,mode,cloudRequests,ap,scenario,direct);
      if(!gas)return base;
      const response=mediumResponse(gas,scenario.medium_response);
      return response.status==='ESTIMATE'&&response.value>(base.response?.value??0)
        ?{...gas,target_id:id,cohort_count:count,response,route:{measured_target:`${main.target_id}:main`,
          kind:'OWN_GAS_CLOUD_MAIN_AND_CONFUSION; PAID_REAPPLICATION'}}:base;
    });
    const unsafe = Math.max(mode.explosion?.outer_radius_m??0,mode.arc_self_risk_m??0) >= scenario.close_distance_m;
    const unarmed = mode.arming_distance_m > scenario.close_distance_m;
    let close = unsafe || unarmed ? {status: 'UNABLE', reason: unsafe ? 'SELF_DAMAGE_DISTANCE' : 'INSIDE_ARMING_DISTANCE'}
      : projectileService(mode, [statusTargetRequest(mode,targets[scenario.close_target],scenario.close_count,ap,scenario,'response')], ap, options);
    if(!unsafe&&!unarmed&&(mode.arc_policy||mode.spread_targets||mode.direct_status_effects?.some(e=>e.id==='Fire')&&!mode.continuous)){
      const group=areaChaffService(facts,mode,[{target:targets[scenario.close_target],count:scenario.close_count}],ap,scenario);
      if(group?.status==='ESTIMATE'&&(!Number.isFinite(close.neutralized_at_ms)||group.neutralized_at_ms<close.neutralized_at_ms))close=group;
    }
    if (close.status === 'ESTIMATE' && mode.explosion) {
      const start = scenario.close_start_distance_m;
      if (!finite(start) || !(scenario.close_distance_m > start) || !(scenario.dive_ms >= 0)) {
        close.status = 'UNKNOWN'; close.reason = 'CLOSE_ESCAPE_GEOMETRY_REQUIRED';
      } else {
        const safeDelay = Math.ceil(scenario.dive_ms * Math.max(0, mode.explosion.outer_radius_m - start) / (scenario.close_distance_m - start));
        const initialPreparation = Math.ceil((close.handling?.acquire_ms ?? 0) + (mode.warmup_ms ?? 0) + (mode.charge_ms ?? 0));
        const extra = Math.max(0, safeDelay - initialPreparation);
        close.first_to_last_ms += extra;
        if (finite(close.neutralized_at_ms)) close.neutralized_at_ms += extra;
        if (finite(close.cycle_ms)) close.cycle_ms += extra;
        close.escape = {safe_fire_delay_ms: safeDelay, preparation_overlap_ms: Math.min(safeDelay, initialPreparation),
          added_delay_ms: extra, start_distance_m: start, final_distance_m: scenario.close_distance_m,
          basis: 'ENGINEERING_LINEAR_RETREAT; NOT_MEASURED_ANIMATION'};
        close.assumptions.push('WAIT_UNTIL_BLAST_SAFE_RADIUS; AIM_CAN_OVERLAP_ESCAPE');
      }
    }
    const gasEscape = cloudCloseService(facts, mode, scenario, close);
    if (gasEscape && gasEscape.response_time_ms < close.first_to_last_ms) close = gasEscape;
    if(facts.category==='throwables'&&facts.effects.some(e=>e.id==='Gas_Confusion'))close.control_assessment={
      earliest_cloud_ms:(mode.detached_delay_ms??0)+(mode.charge_ms??0)+scenario.cloud.close_activation_delay_ms,
      retreat_ms:scenario.dive_ms,reason:'THROWN_FIELD_FUSE_AND_SELF_EXPOSURE_STILL_APPLY; NO_INSTANT_SAFETY_CREDIT'};
    results.push({mode_id: mode.mode_id, chaff, close_defense: close, anti_medium: mediums,
      mixed_ammo: projectileService(mode, [...chaffRequests,
        ...scenario.medium.map(({id, count}) => mediumRequest(mode, id, targets, ap, scenario, count))], ap, options),
      full_stock_standard_damage: finite(mode.total) ? mode.total * (mode.normal * mode.pellets + (mode.explosion?.normal ?? 0)) : null,
      full_stock_durable_damage: finite(mode.total) ? mode.total * (mode.durable * mode.pellets + (mode.explosion?.durable ?? 0)) : null});
  }
  return {status: results.length ? 'PROJECTILE_ESTIMATE' : 'UNRESOLVED', modes: results};
}

// The primary 1.25 is a preference on the comparison index, not physical DPS.
// Reference weapons carry their own slot factor. It is applied BEFORE the new
// bounded display curve, so it cannot turn a 105 ceiling into an unbounded score.
export function scoreOrdinary(capability, reference, {primary = false, reference_primary = false, role} = {}) {
  if (capability.explicit_zero_roles?.includes(role)) return {state:'KNOWN_ZERO',score:0,reason:'UTILITY_HAS_NO_OFFENSIVE_DAMAGE; OTHER_UTILITY_REPORTED_SEPARATELY'};
  if (capability.status === 'UTILITY_ONLY' && ['chaff', 'close_defense', 'anti_medium', 'anti_heavy', 'demolition'].includes(role)) return {state: 'KNOWN_ZERO', score: 0,
    reason: 'SUPPLY_PROVIDER_HAS_NO_COMBAT_CONTRIBUTION'};
  const slot = primary ? 1.25 : 1, refSlot = reference_primary ? 1.25 : 1;
  const candidates = [];
  for (const mode of capability.modes ?? []) {
    for (const ref of reference.modes ?? []) {
      let ratio;
      if (role === 'chaff') {
        if (mode.chaff.status !== 'ESTIMATE' || ref.chaff.status !== 'ESTIMATE') continue;
        const rate = mode.chaff.sustained?.status === 'FINITE_ONLY'
          ? 1000 * mode.chaff.count / (mode.chaff.neutralized_at_ms ?? mode.chaff.first_to_last_ms) : mode.chaff.sustained?.targets_per_second;
        if (!(rate > 0) || !(ref.chaff.sustained?.targets_per_second > 0)) continue;
        ratio = rate / ref.chaff.sustained.targets_per_second
          * (mode.chaff.pressure?.score_ratio_gain??1)/(ref.chaff.pressure?.score_ratio_gain??1)
          *(mode.chaff.requested_count?mode.chaff.count/mode.chaff.requested_count:1);
      } else if (role === 'close_defense') {
        if (mode.close_defense.status !== 'ESTIMATE' || ref.close_defense.status !== 'ESTIMATE') continue;
        const response = mode.close_defense.response_time_ms ?? mode.close_defense.first_to_last_ms;
        const refResponse = ref.close_defense.response_time_ms ?? ref.close_defense.first_to_last_ms;
        if (!(response > 0) || !(refResponse > 0)) continue;
        ratio = refResponse / response;
      } else if (role === 'anti_medium') {
        if (!mode.anti_medium.length || mode.anti_medium.some(x => !['ESTIMATE','UNABLE'].includes(x.status))) continue;
        const comparisons = mode.anti_medium.map(target => {
          const other = ref.anti_medium.find(x => x.target_id === target.target_id);
          if (other?.response?.status !== 'ESTIMATE') return null;
          if (target.status === 'UNABLE') return {ratio: 0, weight: target.cohort_count ?? 1};
          if (target.response?.status !== 'ESTIMATE') return null;
          return {ratio: target.response.value / other.response.value, weight: target.cohort_count ?? 1};
        });
        if (comparisons.some(x => x === null)) continue;
        ratio = comparisons.reduce((n, c) => n + c.ratio * c.weight, 0) / comparisons.reduce((n, c) => n + c.weight, 0);
      } else throw new TypeError('UNKNOWN_ORDINARY_ROLE');
      ratio *= slot / refSlot;
      candidates.push({mode_id: mode.mode_id, ratio, score: capacityCurve(ratio)});
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  const best = candidates[0];
  // A proven unsafe/blocked route is not a data gap. Unknown modes must still
  // prevent a blanket zero, and this verdict applies only to this scenario.
  const measured = (capability.modes ?? []).flatMap(m => role === 'anti_medium' ? m.anti_medium : [m[role]]);
  if (!best && measured.some(m => m?.reason === 'FINITE_STOCK_BELOW_SCENARIO_DEMAND')) return {
    state: 'UNRESOLVED', score: null, reason: 'FINITE_BURST_NOT_MEASURED_BY_FULL_COHORT',
    limitations: ['FAILURE_TO_CLEAR_THE_WHOLE_COHORT_IS_NOT_ZERO_OFFENSIVE_CAPABILITY']};
  if (!best && measured.length && measured.every(m => m?.status === 'UNABLE')) return {
    state: 'KNOWN_ZERO', score: 0, reason: 'NO_USABLE_ROUTE_IN_DECLARED_SCENARIO',
    limitations: [...new Set(measured.map(m => m.reason))]};
  return best ? {...best, score: round(best.score), state: 'ENGINEERING_ESTIMATE', candidates,
    primary_index_factor: slot, reference_index_factor: refSlot}
    : {state: 'UNRESOLVED', score: null, reason: 'NO_COMPARABLE_ROLE_MEASUREMENT'};
}

import {number, field, stat} from './facts.mjs';
import {strategicContribution} from './formulas.mjs';
import {guaranteedFire} from './status.mjs';
import {heavyEligibility} from './heavy-eligibility.mjs';
import {manualValue} from './manual-scores.mjs';
import {combatComponentDamage} from './elemental-damage.mjs';
import {applyStrategicCombatAdjustment} from './strategic-adjustments.mjs';
import {blastAt} from './fragments.mjs';
import {childBurstAt} from './children.mjs';

const known = n => typeof n === 'number' && Number.isFinite(n);
const roles = ['chaff', 'anti_medium'];
const count = (item, key) => number(stat(item, key));
const pick = (item, key, variants) => {
  const rows = item.tables.filter(t => t.kind === 'wikitable').flatMap(t => t.rows).filter(r => r.key === key);
  for (const variant of variants) {
    const matching = rows.filter(r => (r.variant ?? '') === variant), values = [...new Set(matching.map(r => number(r.raw)).filter(known))];
    if (values.length === 1) return {value: values[0], variant, raw: matching[0].raw};
  }
  return null;
};

// Field-specific upgrade choices, preserving the source row. A generic Upgraded
// ammo/uses row is not used to guess an all-module CD or a second independent field.
export function strategicProfile(item, facts, binding) {
  if (!['orbital', 'eagle', 'deployable'].includes(item.category)) return null;
  const family = item.category === 'deployable' ? 'green' : item.category;
  const cooldown = pick(item, 'cooldown', ['With All Upgrades']);
  const call = pick(item, 'call_in_time', ['Upgraded', '', 'Standard']);
  const uses = pick(item, 'uses', ['Upgraded', '', 'Standard']);
  const ammo = pick(item, 'sentry_ammo', ['Upgraded', 'Standard']);
  const sourceRates = item.tables.filter(t => ['wikitable', 'weapon_stats'].includes(t.kind))
    .flatMap(t => t.rows.filter(r => r.key === 'fire_rate').map(r => /([\d,.]+)\s*rpm/i.exec(r.raw)?.[1]))
    .filter(Boolean).map(n => Number(n.replaceAll(',', '')));
  const uniqueRates = [...new Set(sourceRates)];
  const manual = /emplacement|defense_wall/.test(item.id);
  const mine = /mines/.test(item.id), gas = /gas/.test(item.id), smoke = /smoke/.test(item.id);
  const realization = smoke ? 'smoke' : mine ? gas ? 'gas_mines' : 'ordinary_mines'
    : family === 'green' ? manual?'manned_emplacement':/laser_sentry/.test(item.id) ? 'laser_turret' : 'automatic_turret'
    : item.id==='orbital_gatling_barrage'?'gatling_scatter'
    : /barrage|airburst/.test(item.id) ? 'random_barrage' : gas || /laser|napalm/.test(item.id) ? 'persistent_area' : 'concentrated_strike';
  let payload = count(item, 'bombs') * count(item, 'salvos');
  if (!(payload > 0)) payload = null;
  let duration = Math.max(0, ...facts.areas.map(a => a.duration_s ?? 0)) || count(item, 'duration');
  const notes = [];
  if (item.id === 'orbital_120mm_he_barrage') {
    payload = 18; duration = 19;
    notes.push('Wiki Orbital 120mm HE Barrage: More Guns, 18 shells / approximately 19 s');
  }
  if (family === 'eagle') {
    // Per-pass payload, never the aircraft weapon's storage capacity.
    if (item.id === 'eagle_airstrike') { payload = 7; notes.push('Source capacity 6 + XXL Weapons Bay one additional bomb'); }
    else if (item.id === 'eagle_110mm_rocket_pods') payload = 6;
    else if (item.id === 'eagle_strafing_run') payload = 100;
    else if (item.id === 'eagle_500kg_bomb') payload = 1;
  }
  const rearm = family === 'eagle' ? pick(item, 'rearm_time', ['With Pit Crew Hazard Pay']) : null;
  const finiteAmmo = ammo?.value ?? (mine ? count(item, 'capacity') * count(item, 'salvos') : count(item, 'capacity'));
  const profile = {family, realization_class: realization, manual, mine, gas,
    delayed_detonation_seconds: null,
    needs_detonation_delay: facts.modes.some(m => m.delayed_area_component_id && m.delayed_area_component_id !== m.area_component_id),
    cooldown_s: family === 'eagle' ? rearm?.value ?? null : cooldown?.value ?? null,
    small_cd_s: family === 'eagle' ? cooldown?.value ?? null : null,
    delay_s: call?.value ?? null, uses: uses?.value ?? null,
    ammo: finiteAmmo > 0 ? finiteAmmo : null,
    rate_per_second: uniqueRates.length ? Math.min(...uniqueRates) / 60 : null,
    source_rate_range: uniqueRates, rate_selection: uniqueRates.length > 1 ? 'DECLARED_LOWER_SOURCE_BOUND' : 'SOURCE_VALUE',
    payload_per_call: payload, duration_s: duration > 0 ? duration : null,
    lifetime_s: count(item, 'lifetime'), single_call_explosion_fraction: item.id === 'eagle_strafing_run' ? 0.25 : 1,
    timing_rows: {cooldown, call, uses, ammo, rearm}, source_ref: facts.source_ref, notes,
    unresolved: [!call && 'CALL_DELAY', !cooldown && 'FULL_MODULE_COOLDOWN',
      family === 'eagle' && !rearm && 'DEPLETED_EAGLE_REARM'].filter(Boolean)};
  if (binding) {
    for (const key of ['ammo','rate_per_second','payload_per_call','duration_s','payload_mode_id']) {
      if (binding[key] !== undefined) profile[key] = binding[key];
    }
    profile.source_binding = {source_url: binding.source_url, basis: binding.basis};
  }
  return profile;
}

function damage(component, target, ap, multiplier = 1) {
  if (!component || ![component.normal, component.durable, component.pen].every(known)) return null;
  const hit = combatComponentDamage(component,target,ap,{multiplier});
  return hit.status === 'KNOWN' ? hit.damage : null;
}

// Bare capacity is on a declared finite packet, without hit probabilities. The
// classification k alone accounts for its agreed realization losses. No repeated
// damage to a dead target becomes extra kills; the next shot can select a live one.
export function packetEfficiency(row, targets, ap, spacing_m = 1.5, controlPolicy = null) {
  const mode = row.strategic_profile.payload_mode_id
    ? row.facts.modes.find(m => m.mode_id === row.strategic_profile.payload_mode_id)
    : row.facts.modes.find(m => m.feed_bound) ?? row.facts.modes[0];
  const area = row.facts.areas.find(a => a.id === (mode?.delayed_area_component_id ?? mode?.area_component_id))
    ?? (row.facts.areas.length === 1 ? row.facts.areas[0] : null);
  const stream = mode?.kind === 'beam' || mode?.mode_id === 'ORBITAL_LASER_DPS';
  if (row.strategic_profile.gas && !row.facts.effects.some(e => e.id === 'Gas' && e.duration_s > 0 && e.normal > 0)) {
    return {status: 'UNKNOWN', reason: 'GAS_DURATION_AND_APPLICATION_BINDING_REQUIRED'};
  }
  if (!mode && !area) return {status: 'UNKNOWN', reason: 'BOUND_PAYLOAD_COMPONENT_REQUIRED'};
  const sample = targets.flatMap(({target, count}) => Array.from({length: count}, () => target));
  if(mode?.child_pattern){
    const side=Math.ceil(Math.sqrt(sample.length));let work=0,contacts=0;
    for(const [i,target] of sample.entries()){
      const c=childBurstAt(mode,target,ap,[(i%side-(side-1)/2)*spacing_m,(Math.floor(i/side)-(side-1)/2)*spacing_m]);
      if(!c)return {status:'UNKNOWN',reason:'CHILD_PATTERN_REQUIRED'};
      work+=Math.min(1,c.damage/target.hp);contacts+=c.contacts;
    }
    return {status:'ESTIMATE',target_equivalents_per_round:work,stream:false,sample_size:sample.length,
      geometry:'FINITE_CHILD_BLASTS_ON_DISTINCT_MAIN_HP; PARENT_NOT_ADDED',
      children:{emitted:mode.child_payload.count,contacts,pattern:mode.child_pattern},spacing_m,
      assumptions:['DECLARED_DISK_SCATTER_NOT_MEASURED_ACCURACY','CLIPPED_AT_ONE_KILL_PER_TARGET_PER_SALVO']};
  }
  const points=[],extent=Math.ceil(Math.sqrt(sample.length));
  for(let x=-extent;x<=extent;x++)for(let y=-extent;y<=extent;y++)points.push(Math.hypot(x,y)*spacing_m);
  const distances=points.sort((a,b)=>a-b).slice(0,sample.length);
  let rateCapacity = 0,uncontrolled=0,relief=0;
  const confusion=row.facts.effects.find(e=>e.id===area?.second_status);
  for (let index = 0; index < sample.length; index++) {
    const target = sample[index], direct = mode ? damage(mode, target, ap) : 0;
    const blast = area ? damage(area, target, ap) : 0;
    if (direct === null || blast === null) return {status: 'UNKNOWN', reason: 'PAYLOAD_DAMAGE_REQUIRED'};
    let perShot = direct + blast * (row.strategic_profile.single_call_explosion_fraction ?? 1);
    const effect = row.facts.effects.find(e => e.id === area?.status || e.id === mode?.status);
    const fireMode={...mode,pellets:mode?.pellets??1,continuous:stream||mode?.kind==='spray',
      fire_module_multiplier:row.facts.fire_damage_multiplier,
      explosion:area?{...area,status_effects:row.facts.effects.filter(e=>e.id===area.status)}:null};
    const fire=guaranteedFire(fireMode,{target},ap);
    if(fire)perShot+=fire.dps*fire.duration_ms/1000;
    if (row.strategic_profile.gas && target.part_id === 'main' && effect?.normal >= 0 && effect.duration_s > 0) {
      const gasDPS = damage(effect, target, ap);
      perShot += (gasDPS ?? 0) * (area?.duration_s??effect.duration_s);
    }
    const hits = perShot > 0 ? Math.ceil(target.hp / perShot) : Infinity;
    if (stream) rateCapacity += perShot / target.hp;
    else rateCapacity += 1 / hits;
    // Finite neighbours use sourced outer falloff at the declared spacing.
    // Unknown falloff still cannot be replaced with guessed damage or accuracy.
    const radius = area?.outer_radius_m ?? area?.inner_radius_m;
    if (radius >= spacing_m && !stream) {
      const neighbours = distances.filter(d=>d<radius).length-1;
      for (let neighbour = 1; neighbour <= neighbours; neighbour++) {
        const next = sample[(index + neighbour) % sample.length];
        const local=area.normal===0&&area.durable===0?area:blastAt(area,distances[neighbour]);
        if(!local)return {status:'UNKNOWN',reason:'OUTER_BLAST_DAMAGE_OR_PENETRATION_REQUIRED'};
        const splash = damage(local, next, ap) ?? 0;
        const gasDPS = row.strategic_profile.gas && next.part_id === 'main' && effect ? damage(effect, next, ap) : 0;
        const splashFire=area?guaranteedFire({...fireMode,direct_status_effects:[],status_applications:[],
          explosion:{...local,status_effects:fireMode.explosion.status_effects}},{target:next},ap):null;
        const total = splash + (gasDPS ?? 0) * (area?.duration_s??effect?.duration_s??0)
          + (splashFire?splashFire.dps*splashFire.duration_ms/1000:0);
        rateCapacity += Math.min(1, total / next.hp) / Math.max(1, hits);
      }
    }
    // Confusion changes the pressure index, never damage, kill capacity, saved
    // work or the coverage denominator. Only live continuous-contact time counts.
    if(row.strategic_profile.gas&&confusion?.duration_s>0&&controlPolicy&&effect?.normal>0&&target.part_id==='main') {
      const gasDPS=damage(effect,target,ap),hp=Math.max(0,target.hp-direct-blast);
      if(gasDPS>0&&hp>0) {
        const deathMs=hp/gasDPS*1000,fieldMs=(area.duration_s??effect.duration_s)*1000;
        const onset=controlPolicy.onset_delay_ms,q=controlPolicy.residual_threat_fraction;
        if(onset>=0&&q>=0&&q<=1) {
          uncontrolled+=deathMs+(row.strategic_profile.delay_s??0)*1000;
          relief+=Math.max(0,Math.min(deathMs,fieldMs)-onset)*(1-q);
        }
      }
    }
  }
  return {status: 'ESTIMATE', target_equivalents_per_round: rateCapacity / sample.length,
    pressure_ratio_gain:uncontrolled>relief?uncontrolled/(uncontrolled-relief):1,
    pressure:relief>0?{uncontrolled_enemy_ms:uncontrolled,avoided_enemy_ms:relief,
      residual_threat_fraction:controlPolicy.residual_threat_fraction,counts_as_kills:false,
      application:'CURVE_INDEX_ONLY; FINITE_GAS_CONTACT; COVERAGE_USES_DAMAGE_WORK'}:null,
    stream, sample_size: sample.length, geometry: 'FINITE_PACKET_AT_DECLARED_SPACING; NO_PROBABILITY_OR_EXTRA_BODY_PARTS',
    spacing_m, assumptions: ['DECLARED_TARGET_HIT', 'SOURCE_OUTER_LINEAR_FALLOFF_AND_OUTER_AP', 'NO_EXTRA_BODY_PART', 'NO_REPEATED_STATUS_DAMAGE',
      'FIRE_ONLY_GUARANTEED_PACKET; ENEMY_FIRE_MULTIPLIER_APPLIED',
      'GAS_SOURCE_FIELD_LIFETIME_CONTINUOUS_CONTACT; NO_EXTRA_LINGER; ENEMY_GAS_MULTIPLIER_BASELINE_1']};
}

export function assessStrategic(row, model, {ordinary_rates = {}, remaining_mission_uses, selected_roles = roles} = {}) {
  const profile = row.strategic_profile;
  if (!profile) return [];
  const requested=selected_roles===roles&&Number.isFinite(manualValue(row,'close_defense'))?[...roles,'close_defense']:selected_roles;
  return requested.map(role => {
    const fail = reason => ({equipment_id: row.equipment_id, role, contribution: {status: 'UNKNOWN', value: null, reason}});
    if(role==='close_defense')return fail('NO_AUTOMATIC_HELD_CLOSE_TEST');
    if(role==='anti_heavy'&&heavyEligibility(row,model).state==='BELOW_THRESHOLD')return {
      equipment_id:row.equipment_id,role,contribution:{status:'KNOWN',value:0,reason:'BELOW_HEAVY_PENETRATION'}};
    if (row.utility?.combat_damage === false) return {equipment_id:row.equipment_id,role,
      contribution:{status:'KNOWN',value:0,reason:'UTILITY_DAMAGE_ZERO; DEFENSIVE_OR_CONTROL_EFFECT_SEPARATE'}};
    if (profile.realization_class === 'smoke') return {equipment_id: row.equipment_id, role,
      contribution: {status: 'KNOWN', value: 0, reason: 'USER_SMOKE_ZERO'}};
    if (profile.family === 'orbital' && Number.isSafeInteger(profile.uses) && remaining_mission_uses === 0) {
      return {equipment_id: row.equipment_id, role, contribution: {status: 'KNOWN', value: 0, reason: 'FINITE_MISSION_USES_EXHAUSTED'}};
    }
    if (profile.needs_detonation_delay && !known(profile.delayed_detonation_seconds)) return fail('DETONATION_DELAY_REQUIRED');
    const heavy=role==='anti_heavy';
    const ref = heavy?{equipment_id:'scenario:two_heavies_in_20s',capabilities:{modes:[]}}
      :model.equipment.find(r => r.equipment_id === model.references[role]);
    const refModes = ref.capabilities.modes;
    const requests = heavy?model.scenario.heavy_targets.map(id=>({id:`${id}:main`,count:1}))
      :model.scenario[role === 'chaff' ? 'chaff' : 'medium'];
    // Autonomous/area fire uses Main, never an explosion-immune head's small
    // health pool. No precision head aim or post-decapitation instant kill credit.
    const targets = requests.map(({id, count}) => ({target: role === 'anti_medium'
      ? model.targets[`${id.split(':')[0]}:main`] : model.targets[id], count}));
    // The three species are samples of a two-heavy demand, not three enemies
    // that enlarge the Eagle reference window from 20 to 30 seconds.
    const n = heavy?model.scenario.heavy_count:targets.reduce((sum, t) => sum + t.count, 0);
    const refRate = heavy?model.scenario.heavy_count/model.scenario.heavy_seconds
      :role === 'chaff' ? Math.max(...refModes.map(m => m.chaff.sustained.targets_per_second))
      : n / requests.reduce((sum, {id: key, count}) => {
        const fastest = Math.min(...refModes.map(m => m.anti_medium.find(t => t.target_id === key)?.response?.repeat_cycle_ms ?? Infinity));
        return sum + count * fastest / 1000;
      }, 0);
    if (!(refRate > 0)) return fail('REFERENCE_SERVICE_RATE_REQUIRED');
    const efficiency = packetEfficiency(row, targets, model.ap, model.scenario.area?.spacing_samples_m?.[0]
      ?? model.scenario.strategic?.neighbour_spacing_m ?? 1.5,heavy?null:model.scenario.cloud?.pressure_control);
    if (efficiency.status !== 'ESTIMATE') return fail(efficiency.reason);
    let active = profile.duration_s, capacity, rate,uncappedCapacity;
    const q = efficiency.target_equivalents_per_round;
    if (profile.family === 'green') {
      if (efficiency.stream && profile.lifetime_s > 0) {
        // Nominal lifetime here; laser-turret k includes overheat self-destruction.
        active = profile.lifetime_s; rate = q; capacity = rate * active;
      } else {
        rate = profile.mine ? refRate : profile.rate_per_second;
        if (!(profile.ammo > 0) || !(rate > 0)) return fail('FINITE_DEPLOYABLE_AMMO_AND_RATE_REQUIRED');
        active = Math.min(profile.ammo / rate, profile.lifetime_s > 0 ? profile.lifetime_s : Infinity);
        capacity = Math.min(profile.ammo, active * rate) * q;
        rate *= q;
      }
    } else if (efficiency.stream && active > 0) { rate = q; capacity = rate * active; }
    else {
      if (!(profile.payload_per_call > 0)) return fail('PER_CALL_PAYLOAD_REQUIRED');
      capacity = profile.payload_per_call * q;
      if (profile.family === 'eagle') {
        // A pass cannot kill a declared target more than once. Surplus payload
        // and overlapping bombs are not extra fresh waves inside the same pass.
        uncappedCapacity=capacity;capacity=Math.min(n,capacity);
        active = n / refRate; rate = capacity / active;
      } else if (!(active > 0)) {
        if (profile.realization_class === 'random_barrage') return fail('BARRAGE_EFFECT_DURATION_REQUIRED');
        // An instantaneous hit buys the time that the reference gun would need
        // to perform the same finite amount of work. No arbitrary 30-second T.
        active = Math.min(n, capacity) / refRate; rate = active > 0 ? capacity / active : 0;
      } else rate = capacity / active;
    }
    if (capacity === 0) return {equipment_id: row.equipment_id, role, contribution: {
      status: 'KNOWN', value: 0, reason: 'NO_DAMAGE_TO_DECLARED_MAIN_TARGETS; CONTROL_NOT_CONVERTED_TO_KILLS', profile, efficiency}};
    if (!(active > 0) || !known(capacity)) return fail('EFFECTIVE_WORK_REQUIRED');
    const coverage = {family: profile.family, duration_s: active, cooldown_s: profile.cooldown_s,
      delay_s:profile.delay_s,
      uses: profile.uses, small_cd_s: profile.small_cd_s,effective_work_s:capacity/refRate};
    // A manually operated weapon occupies the same hands. Only its improvement
    // over available handheld service is an additional contribution.
    const heldRate = ordinary_rates[role] ?? refRate;
    const operation = profile.manual ? rate > 0 ? Math.max(0, 1 - heldRate / rate) : 0 : 1;
    const contribution = strategicContribution({capacity: {available: capacity*(efficiency.pressure_ratio_gain??1), reference: refRate * active,
      unit: 'target_equivalents_in_declared_work_window', reference_id: ref.equipment_id}, coverage,
      realization_class: profile.realization_class, manual: profile.manual, operation_factor: operation});
    return {equipment_id: row.equipment_id, role, contribution: {...contribution,
      pressure_index_multiplier:efficiency.pressure_ratio_gain??1,
      physical: {capacity,work_seconds: active,target_equivalents_per_second: rate,reference_rate: refRate,
        ...(profile.family==='eagle'?{uncapped_payload_capacity:uncappedCapacity,single_pass_target_limit:n,
          effective_work_s:capacity/refRate,coverage_formula:'N*T/(N*T+BIG_CD+DELAY+(N-1)*max(SMALL_CD,DELAY))'}:{})},
      profile, efficiency, assumptions: ['NO_REAL_HIT_PROBABILITY', 'CLASS_REALIZATION_APPLIED_ONCE',
        ...(heavy?['MAIN_HP_PRESSURE_CAPACITY; NOT_A_TWO_KILL_CERTIFICATE','NO_EXPOSED_WEAKPOINT_OR_MULTIPART_CREDIT']:[]),
        ...(profile.family === 'orbital' && Number.isSafeInteger(profile.uses) ? [`FINITE_MISSION_USES_${remaining_mission_uses ?? profile.uses}; VALUE_WHILE_AVAILABLE`] : []),
        ...(profile.mine ? ['ONE_TRIGGER_PER_REFERENCE_TARGET_INTERVAL'] : []),
        ...(profile.manual ? ['INCREMENTAL_RATE_AFTER_OCCUPYING_PLAYER_HANDS'] : [])]}};
  }).map(result=>({...result,contribution:applyStrategicCombatAdjustment(row,result.role,result.contribution)}))
  .map(result=>Number.isFinite(manualValue(row,result.role))?{...result,contribution:{...result.contribution,
    calculated_status:result.contribution.status,calculated_value:result.contribution.value,
    value:manualValue(row,result.role),status:'KNOWN',score_origin:'MANUAL_CALIBRATED'}}:result);
}

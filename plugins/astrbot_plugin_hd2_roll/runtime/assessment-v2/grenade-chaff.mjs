import {resourceWeapons,supplyProviders} from './engagement.mjs';
import {executeResourcePlan} from './resources.mjs';
// Grenades are finite auxiliary tools. Their burst capability remains visible,
// but only a single player's box of replacement grenades recurs each supply CD.
// The initial pocket stock and portable/car boxes are finite combat resources,
// not stock that magically reappears at every renewal.
export function grenadeChaff(row, reference, scenario) {
  if (row.category !== 'throwables') return null;
  const burst = row.dimensions.chaff;
  const service = row.capabilities.modes.find(m => m.mode_id === burst.mode_id)?.chaff;
  const inventory = row.facts.service_inventory ?? row.facts.inventory;
  const refill = (row.facts.service_refill ?? row.facts.refill)?.public;
  const policy = scenario.supply.grenade_renewal;
  const referenceRate = Math.max(0, ...reference.capabilities.modes.map(m => m.chaff?.sustained?.targets_per_second ?? 0));
  const result = {bare_score: burst.score, score: null, initial_rounds: inventory.total,
    refill_rounds: refill, reference_rate: referenceRate,
    scope: 'REFILL_WEIGHTED_CHAFF_ADDITION; INITIAL_STOCK_ONLY_IN_BURST_AND_PHYSICAL_LEDGER',
    supply_policy: policy};
  if (burst.state === 'KNOWN_ZERO') return {...result, state: 'KNOWN_ZERO', score: 0, coverage_value: 0};
  if (!(service?.shots > 0 && service.count > 0 && referenceRate > 0)
    || !Number.isFinite(burst.score) || !(inventory.total > 0) || !Number.isFinite(refill) || !(refill >= 0)
    || !(policy?.cooldown_ms > 0) || !(policy.delivery_ms >= 0)) {
    return {...result, state: 'UNRESOLVED', reason: 'GRENADE_BURST_OR_REFILL_CYCLE_REQUIRED'};
  }
  const credited = Math.min(inventory.total, refill * scenario.supply.public_boxes);
  // Q is repeatable, paid useful work, not all enemies in an unbounded radius.
  // Longer damage delays cannot increase this value; they only reduce B or V.
  const perGrenade = service.count / service.shots;
  const work = credited * perGrenade / referenceRate;
  const mode = (row.facts.service_modes ?? row.facts.modes).find(m => m.mode_id === burst.mode_id);
  const delay = mode?.detached_delay_ms ?? 0;
  const cycle = policy.cooldown_ms + policy.delivery_ms + scenario.supply.interaction_ms.public + delay;
  const coverage = work / (work + cycle / 1000);
  return {...result, state: 'ENGINEERING_ESTIMATE', score: Math.round(burst.score * coverage * 100) / 100,
    coverage_value: coverage, cycle_ms: cycle, credited_rounds: credited,
    equivalent_work_s: work, targets_per_grenade: perGrenade,
    burst_rounds: service.shots, burst_targets: service.count, burst_effect_ms: service.neutralized_at_ms,
    mode_id: burst.mode_id, formula: 'B * T/(T + SUPPLY_CD + DELIVERY + TAKE + FUSE); T=REFILL*TARGETS_PER_GRENADE/REFERENCE_RATE',
    limitations: ['CONTINUOUS_CONTACT_REQUIRED_FOR_CLOUD', 'NO_FREE_PORTABLE_REFILLS', 'NO_MEASURED_SPAWN_RATE']};
}

export function grenadeChaffAddition(row) {
  if (row.category !== 'throwables' || !row.grenade_chaff) return [];
  const dimension = row.dimensions.chaff;
  return [{equipment_id: row.equipment_id, role: 'chaff', contribution: {
    ...row.grenade_chaff, value: dimension.score,
    status: Number.isFinite(dimension.score) ? 'KNOWN' : 'UNKNOWN',
    source: 'GRENADE_REFILL_AUXILIARY', score_origin: dimension.score_origin ?? 'AUTOMATIC',
  }}];
}

// An extra box is paid in the same first-wave ledger as the held weapons.
// Compare identical plans with/without portable sources; credit only additional
// completed grenade actions. This is a finite opening window, not renewal.
export function suppliedGrenadeAdditions(rows,model,ordinaryPlan) {
  const additions=rows.flatMap(grenadeChaffAddition);
  if(!rows.some(r=>['backpack_b_1_supply_pack','vehicle_m_103_supply_fast_recon_vehicle'].includes(r.equipment_id)))return additions;
  const providers=supplyProviders(rows,model.scenario),baseProviders=providers.filter(p=>p.kind==='public');
  const weapons=resourceWeapons(rows),prefix=ordinaryPlan?.first_wave_actions;
  return additions.map(a=>{
    if(a.contribution.score_origin==='MANUAL_CALIBRATED')return a;
    const row=rows.find(r=>r.equipment_id===a.equipment_id),g=row.grenade_chaff;
    if(!prefix?.length||!(g.bare_score>0&&g.targets_per_grenade>0&&g.cycle_ms>0))return {...a,
      finite_supply:{status:'UNKNOWN',reason:'SHARED_FIRST_WAVE_OR_GRENADE_SERVICE_REQUIRED'}};
    const mode=(row.facts.service_modes??row.facts.modes).find(m=>m.mode_id===g.mode_id);
    const service=row.capabilities.modes.find(m=>m.mode_id===g.mode_id)?.chaff;
    if(!mode||!Number.isFinite(service?.first_to_last_ms))return a;
    const effect=Math.max(mode.detached_delay_ms??0,(service.neutralized_at_ms??0)-service.first_to_last_ms);
    const action={weapon_id:mode.resource_id??row.equipment_id,mode_id:mode.mode_id,rounds:1,
      prepare_ms:Math.ceil((service.handling?.acquire_ms??model.scenario.aim_ms)+(mode.charge_ms??0)+(mode.warmup_ms??0)),
      work_ms:mode.work_ms??0,charge_ms:mode.charge_ms??0,warmup_ms:mode.warmup_ms??0,
      acquire_ms:Math.ceil(service.handling?.acquire_ms??model.scenario.aim_ms),
      interval_ms:Math.ceil(Math.max(60000/mode.rpm,mode.charge_ms??0)),effect_delay_ms:Math.ceil(effect),
      switch_ms:model.scenario.weapon_switch_ms};
    const account=weapons.find(w=>w.id===action.weapon_id);
    if(!account)return a;
    const cap=account.loaded+account.reserve+providers.reduce((n,p)=>n+(p.charges+(p.kind==='backpack'?model.scenario.supply.public_boxes:0))
      *(account.refill?.[p.refill_kind]??0),0);
    const steps=prefix.concat(Array.from({length:cap+1},()=>({...action})));
    const run=ps=>executeResourcePlan({weapons,providers:ps,steps,deadline_ms:g.cycle_ms});
    const baseline=run(baseProviders),supplied=run(providers);
    if(baseline.completed_steps<prefix.length||supplied.completed_steps<prefix.length)return {...a,
      finite_supply:{status:'UNKNOWN',reason:'FIRST_WAVE_NOT_COMPLETED_WITH_BOTH_PROVIDER_SETS'}};
    const extra=Math.max(0,supplied.completed_steps-baseline.completed_steps);
    const publicCredit=Math.min(g.credited_rounds,baseline.completed_steps-prefix.length);
    const work=(publicCredit+extra)*g.targets_per_grenade/g.reference_rate;
    const take=supplied.supplies_taken.filter(p=>providers.find(x=>x.id===p.provider_id)?.kind!=='public')
      .reduce((n,p)=>n+providers.find(x=>x.id===p.provider_id).take_ms,0);
    const coverage=work/(work+(g.cycle_ms+(extra?take:0))/1000);
    const value=Math.round(g.bare_score*coverage*100)/100;
    return {...a,contribution:{...a.contribution,value,coverage_value:coverage,
      source:'GRENADE_REFILL_AUXILIARY',supply_scope:'FINITE_SUPPLIED_GRENADE_FIRST_WINDOW',scope:'OPENING_SUPPLY_WINDOW_ONLY; NOT_RECURRING_PORTABLE_STOCK'},
      finite_supply:{status:'EXECUTED',extra_completed_grenades:extra,credited_public_rounds:publicCredit,
        baseline_completed_grenades:baseline.completed_steps-prefix.length,
        supplied_completed_grenades:supplied.completed_steps-prefix.length,window_ms:g.cycle_ms,
        supplies_taken:supplied.supplies_taken,remaining_resources:supplied.state.resources,
        first_wave_actions:prefix.length,recurs:false,gate_credit:false,
        basis:'IDENTICAL_PAID_FIRST_WAVE_THEN_GRENADE_ACTIONS; NO_OTHER_ROLE_REUSES_A_BOX'}};
  });
}

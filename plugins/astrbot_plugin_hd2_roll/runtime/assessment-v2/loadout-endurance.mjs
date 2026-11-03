import {contributionScore} from './score-scale.mjs';
import {isMeleeEquipment} from './score-exclusions.mjs';

// User-selected role shares. Ability proportions estimate usage, not measured
// shooting time; the finite-ammunition ledger remains a separate admission gate.
export const DEFAULT_LOADOUT_ENDURANCE_POLICY={
  id:'routine-role-additive-weight-v4',role_weights:{chaff:.5,close_defense:.35,anti_medium:.15},
  role_weighting:'ABILITY_PLUS_ENDURANCE; EXCLUDE_ZERO_ROLE_ABILITY',
  throwable_bonus:'ROLE_ADDITION_TIMES_ENDURANCE_OVER_100',
  ceiling:100,pass_threshold:55,supply_final_floor:100,
  scope:'DETERMINISTIC_RESOURCE_INDEX; NO_LLM; FINAL_SCORE_PARTICIPATES_IN_SIX_DIMENSION_GATE',
  parameters_status:'USER_SELECTED_ABILITY_PLUS_ENDURANCE_WEIGHTS_20261103'
};
const finite=n=>Number.isFinite(n)&&n>=0;
const policyFor=model=>model.scenario.loadout_endurance??DEFAULT_LOADOUT_ENDURANCE_POLICY;

export function routineRoleScores(row,model,policy=policyFor(model)) {
  return Object.fromEntries(Object.keys(policy.role_weights).map(role=>{
    const dimension=row.dimensions[role],score=contributionScore(dimension?.score,dimension?.score_origin,model.scenario.score_scale);
    return [role,finite(score)?score:null];
  }));
}

export function aggregateRoutineScores(items,policy=DEFAULT_LOADOUT_ENDURANCE_POLICY) {
  const ordered=[...items].sort((a,b)=>a.equipment_id.localeCompare(b.equipment_id));
  const roles=Object.fromEntries(Object.entries(policy.role_weights).map(([role,weight])=>{
    // A known zero capability cannot gain a role merely by carrying more ammo.
    const active=ordered.filter(i=>i.role_scores[role]!==0);
    const missingAbilities=active.filter(i=>!finite(i.role_scores[role])).map(i=>i.equipment_id);
    const missingEndurance=active.filter(i=>!finite(i.score)).map(i=>i.equipment_id);
    const unknown=missingAbilities.length>0||missingEndurance.length>0;
    const abilityTotal=active.reduce((sum,i)=>sum+(finite(i.role_scores[role])?i.role_scores[role]:0),0);
    const enduranceTotal=active.reduce((sum,i)=>sum+(finite(i.score)?i.score:0),0);
    const total=unknown?null:abilityTotal+enduranceTotal;
    const numerator=unknown?null:active.reduce((sum,i)=>sum+(i.role_scores[role]+i.score)*i.score,0);
    const score=unknown?null:total?numerator/total:0;
    return [role,{weight,score,ability_total:missingAbilities.length?null:abilityTotal,
      endurance_total:unknown?null:enduranceTotal,weight_total:total,
      // Both numerator and denominator depend on unknown endurance. Do not treat
      // missing values as zero or recycle the previous formula's lower bound.
      known_lower_bound:score??0,missing_abilities:missingAbilities,missing_endurance:missingEndurance}];
  }));
  const scored=ordered.map(item=>{
    const abilityShares=Object.fromEntries(Object.entries(roles).map(([role,r])=>[role,
      r.ability_total===null?null:r.ability_total?item.role_scores[role]/r.ability_total:0]));
    const roleShares=Object.fromEntries(Object.entries(roles).map(([role,r])=>[role,
      item.role_scores[role]===0?0:r.weight_total===null?null:
        r.weight_total?(item.role_scores[role]+item.score)/r.weight_total:0]));
    const share=Object.values(roleShares).some(v=>v===null)?null:
      Object.entries(roles).reduce((sum,[role,r])=>sum+r.weight*roleShares[role],0);
    return {...item,ability_shares:abilityShares,role_shares:roleShares,weighted_share:share,
      value:share===0?0:finite(share)&&finite(item.score)?share*item.score:null};
  });
  const knownLowerBound=Math.min(policy.ceiling,Object.values(roles).reduce((sum,r)=>sum+r.weight*r.known_lower_bound,0));
  const unknown=Object.values(roles).some(r=>r.score===null);
  return {score:unknown?null:knownLowerBound,known_lower_bound:knownLowerBound,roles,items:scored,
    lower_bound:unknown||scored.some(item=>item.lower_bound),
    formula:`MIN(${policy.ceiling}, SUM_ROLE(weight * SUM_A_POSITIVE((A+E)*E) / SUM_A_POSITIVE(A+E)))`,
    score_units:'CURRENT_DISPLAY_SCALE'};
}

export const isHeldEnduranceWeapon=row=>!isMeleeEquipment(row)&&
  (['primary','secondary','support'].includes(row.category)||row.facts.holding?.support===true);

export function throwableEnduranceAdditions(rows,model,policy=policyFor(model)) {
  return rows.filter(row=>row.category==='throwables').map(row=>{
    const endurance=baselineWeaponEndurance(row,model),abilities=routineRoleScores(row,model,policy);
    const roles=Object.fromEntries(Object.keys(policy.role_weights).map(role=>{
      const ability=abilities[role],stock=endurance.score;
      return [role,{ability,bonus:ability===0||stock===0?0:finite(ability)&&finite(stock)?ability*stock/100:null}];
    }));
    return {equipment_id:row.equipment_id,endurance_score:endurance.score,roles,
      value:Object.entries(policy.role_weights).reduce((sum,[role,weight])=>sum+weight*(roles[role].bonus??0),0),
      lower_bound:endurance.lower_bound||Object.values(roles).some(r=>r.bonus===null),
      scope:'DISPLAY_ROLE_ADDITION_TIMES_PUBLIC_BOX_ENDURANCE_OVER_100; NO_FREE_AMMO'};
  });
}

export function baselineWeaponEndurance(row,model) {
  if(isMeleeEquipment(row))return {equipment_id:row.equipment_id,score:0,baseline_score:0,
    score_origin:'USER_MELEE_ZERO',lower_bound:false};
  const score=contributionScore(row.endurance.score,row.endurance.score_origin,model.scenario.score_scale);
  return {equipment_id:row.equipment_id,score,baseline_score:score,
    score_origin:row.endurance.score_origin??'AUTOMATIC_PUBLIC_BOX_BASELINE',
    lower_bound:!finite(score)||Boolean(row.endurance.rating?.scenarios?.public?.partial)};
}

export function applySupplyEnduranceFloor(dimension,rows,policy=DEFAULT_LOADOUT_ENDURANCE_POLICY) {
  const equipmentIds=rows.filter(r=>['backpack_b_1_supply_pack','vehicle_m_103_supply_fast_recon_vehicle'].includes(r.equipment_id))
    .map(r=>r.equipment_id).sort();
  if(!equipmentIds.length)return {...dimension,supply_adjustment:null};
  const score=Math.max(policy.supply_final_floor,dimension.score??0);
  return {...dimension,score,
    supply_adjustment:{floor:policy.supply_final_floor,equipment_ids:equipmentIds,before_score:dimension.score,
      added_score:Number.isFinite(dimension.score)?score-dimension.score:null,
      scope:'USER_FINAL_SCORE_FLOOR_ONCE; DOES_NOT_CHANGE_AMMO_OR_PHYSICAL_GATES'}};
}

export function assessLoadoutEndurance(rows,model,{reserved_for_heavy=[]}={}) {
  const policy=policyFor(model),reserved=new Set(reserved_for_heavy);
  const items=[],excluded=[];
  for(const row of rows){
    const reason=isMeleeEquipment(row)?'MELEE_EXCLUDED_FROM_ENDURANCE':row.category==='throwables'?'THROWABLE_AUXILIARY_ENDURANCE':
      ['orbital','eagle','deployable'].includes(row.category)?'RED_GREEN_ENDURANCE_ZERO':
      row.endurance.state==='SUPPLY_PROVIDER'?'FINAL_SCORE_FLOOR_HANDLED_AT_END':
      reserved.has(row.equipment_id)?'RESERVED_FOR_HEAVY':
      !isHeldEnduranceWeapon(row)?'NOT_A_HELD_ROUTINE_WEAPON':null;
    if(reason){excluded.push({equipment_id:row.equipment_id,reason});continue;}
    items.push({...baselineWeaponEndurance(row,model),role_scores:routineRoleScores(row,model,policy)});
  }
  const routine=aggregateRoutineScores(items,policy);
  const grenades=throwableEnduranceAdditions(rows.filter(r=>!reserved.has(r.equipment_id)),model,policy);
  const bonus=grenades.reduce((sum,g)=>sum+g.value,0);
  const roles=Object.fromEntries(Object.entries(routine.roles).map(([role,r])=>{
    const addition=grenades.reduce((sum,g)=>sum+(g.roles[role].bonus??0),0);
    return [role,{...r,routine_score:r.score,grenade_bonus:addition,score:r.score===null?null:r.score+addition}];
  }));
  return {...routine,roles,routine_score:routine.score,grenade_bonus:bonus,grenade_items:grenades,
    score:routine.score===null?null:Math.min(policy.ceiling,routine.score+bonus),
    known_lower_bound:Math.min(policy.ceiling,routine.known_lower_bound+bonus),
    lower_bound:routine.lower_bound||grenades.some(g=>g.lower_bound),excluded,policy,
    formula:'MIN(100, SUM_ROLE(weight * (GUN_ROLE_MEAN + GRENADE_ROLE_ADDITION * GRENADE_ENDURANCE / 100)))',
    scope:'ROLE_WEIGHTED_GUN_MEANS_PLUS_THROWABLE_ENDURANCE; PUBLIC_BOX_BASELINES; PHYSICAL_GATES_UNCHANGED',
    supply_scope:'SUPPLY_PACK_OR_VEHICLE_ADJUSTS_FINAL_SCORE_ONLY; PHYSICAL_LEDGER_REMAINS_FINITE'};
}

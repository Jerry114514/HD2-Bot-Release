import {contributionScore} from './score-scale.mjs';
import {isMeleeEquipment} from './score-exclusions.mjs';

// Engineering weights, not measured usage time or another combat simulation.
export const DEFAULT_LOADOUT_ENDURANCE_POLICY={
  id:'routine-primary-plus-backup-v1',role_weights:{chaff:.6,close_defense:.3,anti_medium:.1},
  role_reference:60,backup_weight:.35,ceiling:100,supply_final_floor:100,
  scope:'DETERMINISTIC_RESOURCE_INDEX; NO_LLM; NO_NEW_PASS_THRESHOLD',
  parameters_status:'ENGINEERING_INITIAL_VALUES_PENDING_PLAY_REVIEW'
};
const finite=n=>Number.isFinite(n)&&n>=0;
const clamp=n=>Math.max(0,Math.min(1,n));
const policyFor=model=>model.scenario.loadout_endurance??DEFAULT_LOADOUT_ENDURANCE_POLICY;

export function routineSuitability(row,model,policy=policyFor(model)) {
  const roles=Object.entries(policy.role_weights).map(([role,weight])=>{
    const dimension=row.dimensions[role],score=contributionScore(dimension?.score,dimension?.score_origin,model.scenario.score_scale);
    return {role,weight,score,fit:finite(score)?clamp(score/policy.role_reference):null};
  });
  return {value:Math.round(roles.reduce((n,r)=>n+(r.fit??0)*r.weight,0)*1e12)/1e12,roles,
    missing_roles:roles.filter(r=>r.fit===null).map(r=>r.role),
    scope:'KNOWN_SUITABILITY_LOWER_BOUND; NOT_SHOOTING_TIME_OR_HIT_RATE'};
}

export function aggregateRoutineScores(items,policy=DEFAULT_LOADOUT_ENDURANCE_POLICY) {
  const scored=items.map(item=>({...item,value:finite(item.score)?item.score*item.suitability.value:null}));
  const measured=scored.filter(item=>finite(item.value)).sort((a,b)=>b.value-a.value||(a.equipment_id<b.equipment_id?-1:1));
  const primary=measured[0],primaryValue=primary?.value??0;
  const backupValue=measured.slice(1).reduce((sum,item)=>sum+item.value*policy.backup_weight,0);
  const knownLowerBound=Math.min(policy.ceiling,primaryValue+backupValue);
  return {score:items.length&&!measured.length?null:knownLowerBound,known_lower_bound:knownLowerBound,primary_id:primary?.equipment_id??null,
    primary_value:primaryValue,backup_value:backupValue,items:scored,
    lower_bound:scored.some(item=>item.value===null||item.lower_bound||item.suitability.missing_roles.length>0),
    formula:`MIN(${policy.ceiling}, MAX(Q*E) + ${policy.backup_weight} * SUM(OTHER_Q*E))`,score_units:'CURRENT_DISPLAY_SCALE'};
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
    const reason=isMeleeEquipment(row)?'MELEE_EXCLUDED_FROM_ENDURANCE':row.category==='throwables'?'THROWABLE_USES_SEPARATE':
      ['orbital','eagle','deployable'].includes(row.category)?'RED_GREEN_ENDURANCE_ZERO':
      row.endurance.state==='SUPPLY_PROVIDER'?'FINAL_SCORE_FLOOR_HANDLED_AT_END':
      row.manual_calibration?.mode==='additive'?'EXPLICIT_ADDITION_HANDLED_ONCE_AT_END':
      reserved.has(row.equipment_id)?'RESERVED_FOR_HEAVY':
      !['primary','secondary','support'].includes(row.category)&&row.facts.holding?.support!==true?'NOT_A_HELD_ROUTINE_WEAPON':null;
    if(reason){excluded.push({equipment_id:row.equipment_id,reason});continue;}
    items.push({...baselineWeaponEndurance(row,model),suitability:routineSuitability(row,model,policy)});
  }
  return {...aggregateRoutineScores(items,policy),excluded,policy,
    scope:'ROUTINE_PRIMARY_PLUS_BACKUP; PUBLIC_BOX_BASELINES; PHYSICAL_GATES_UNCHANGED',
    supply_scope:'SUPPLY_PACK_OR_VEHICLE_ADJUSTS_FINAL_SCORE_ONLY; PHYSICAL_LEDGER_REMAINS_FINITE'};
}

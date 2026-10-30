import {shotDamage as rawShotDamage} from '../calibration/practical/endurance-capacity.mjs';
import {directDamage} from '../engine/damage.mjs';
import {childBurstAt} from './children.mjs';

// Burning ammunition can carry a physical projectile. Only a component whose
// source says Fire receives the enemy fire multiplier; its attached DoT is separate.
export function elementalComponent(component,target) {
  if(!component||component.element!=='Fire')return component;
  if(!Number.isFinite(target.fire_multiplier))return null;
  const ship=component.source_fire_module_multiplier!==undefined?1:component.fire_module_multiplier??1;
  const scale=target.fire_multiplier*ship;
  return {...component,normal:component.normal*scale,durable:component.durable*scale};
}
export function combatShotDamage(mode,target,ap) {
  if(mode.child_pattern){const c=childBurstAt(mode,target,ap);return c?{state:'KNOWN',per_shot_damage:c.damage,children:c}:
    {state:'UNKNOWN_DATA',reason:'CHILD_BLAST_GEOMETRY_REQUIRED'};}
  const direct=elementalComponent(mode,target),blast=elementalComponent(mode.explosion,target);
  if(!direct||mode.explosion&&!blast)return {state:'UNKNOWN_DATA',reason:'FIRE_TARGET_MULTIPLIER_REQUIRED'};
  if(mode.arc_policy&&mode.kind==='projectile'){
    // Realization is an equivalent damage budget, not a fractional physical
    // projectile count. Resolve one source arc first, then apply its budget.
    const one=rawShotDamage({...direct,pellets:1,explosion:null},target,ap);
    return {...one,per_shot_damage:Number.isFinite(one.per_shot_damage)?one.per_shot_damage*mode.pellets:null,
      equivalent_primary_arcs:mode.pellets};
  }
  const hit=rawShotDamage({...direct,explosion:blast},target,ap);
  if(mode.attached_thermite){const t=mode.attached_thermite,extra=combatComponentDamage(t,target,ap).damage*t.duration_s;
    if(Number.isFinite(extra))return {...hit,state:'KNOWN',per_shot_damage:(hit.per_shot_damage??0)+extra,
      attached_thermite_damage:extra,damage_timing:mode.damage_timing};}
  return hit;
}
export function combatComponentDamage(component,target,ap,{multiplier=1,rounding='floor'}={}) {
  const c=elementalComponent(component,target);
  if(!c)return {status:'UNKNOWN_DATA',reason:'FIRE_TARGET_MULTIPLIER_REQUIRED'};
  return directDamage({normal:c.normal,durable:c.durable,penetration:c.pen,durability:target.durable,armor:target.armor,
    equal_multiplier:ap.equal,greater_multiplier:ap.greater,multiplier,rounding});
}

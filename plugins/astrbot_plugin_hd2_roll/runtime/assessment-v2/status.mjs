import {directDamage} from '../engine/damage.mjs';

// User-observed contact time is independent of the numerical beam quantum.
// Only the named light-target sample is bound; do not extrapolate to armour,
// larger fire thresholds, automated target switching or pulsed lasers.
export function continuousFire(mode,request,ap) {
  let policy=mode.ignition_policy;const main=request.resolution?.main??request.target;
  const spray=mode.fire_channel==='spray'||mode.service_conversion?.includes('FUEL_UNIT');
  if(spray&&!policy?.targets.includes(main?.target_id)&&main?.fire_threshold_max>0&&mode.nominal_status_strength_per_second>0)
    policy={targets:[main.target_id],contact_ms:Math.ceil(1000*main.fire_threshold_max/mode.nominal_status_strength_per_second),
      basis:'NOMINAL_CONTINUOUS_STREAM_STRENGTH_INTEGRAL; NO_GAP_OR_DECAY; NOT_MEASURED_PARTICLE_RATE'};
  const e=mode.direct_status_effects?.find(e=>e.id==='Fire');
  if(!mode.continuous||!policy?.targets.includes(main?.target_id)||!e||!(policy.contact_ms>0)
    ||mode.pen<request.target.armor||!Number.isFinite(main.fire_multiplier))return null;
  const hit=directDamage({normal:e.normal,durable:e.durable,penetration:e.pen,durability:main.durable,
    armor:main.armor,equal_multiplier:ap.equal,greater_multiplier:ap.greater,rounding:'none'});
  if(hit.status!=='KNOWN'||!(e.duration_s>0))return null;
  return {id:e.id,dps:hit.damage*main.fire_multiplier*(mode.fire_module_multiplier??1),
    duration_ms:Math.round(e.duration_s*1000),ignition_contact_ms:policy.contact_ms,
    contact_range_ms:policy.contact_range_ms,channel:spray?'SPRAY_CONTACT_ESTIMATE':'USER_LIGHT_BEAM_CONTACT_ESTIMATE',target_pool:'main',
    fire_multiplier:main.fire_multiplier,module_multiplier:mode.fire_module_multiplier??1,
    refresh_policy:'SINGLE_NONSTACKING_BURN; CONTINUOUS_CONTACT_REFRESHES_DURATION_ONLY',basis:policy.basis,
    caveat:'CONTACT_TIME_IS_USER_RANGE_MIDPOINT; NO_MEDIUM_OR_AUTOMATED_SWITCH_EXTRAPOLATION; NOT_ENGINE_TICK'};
}

export const boundFire=(mode,request,ap)=>packetFire(mode,request,ap,true)??continuousFire(mode,request,ap);

// A numerical integration quantum is NOT a source status tick. Only a single
// documented packet that crosses the target's guaranteed threshold binds here.
export function guaranteedFire(mode,request,ap) {
  return packetFire(mode,request,ap,false);
}
function packetFire(mode,request,ap,accumulate) {
  const main=request.resolution?.main??request.target;
  if (!main || !(main.fire_threshold_max>0) || !Number.isFinite(main.fire_multiplier)) return null;
  const directBinding=mode.status_applications?.find(a=>a.id==='Fire');
  const blastBinding=mode.explosion?.status_applications?.find(a=>a.id==='Fire');
  const direct=directBinding?.effect??(mode.direct_status_effects??[]).find(e=>e.id==='Fire');
  const splash=blastBinding?.effect??(mode.explosion?.status_effects??[]).find(e=>e.id==='Fire');
  const choices=[];
  if(direct&&!mode.continuous&&!mode.service_conversion?.includes('FUEL_UNIT')) {
    choices.push({effect:direct,strength:(directBinding?.strength??mode.status_strength??0)*mode.pellets,contact_ap:mode.pen,channel:'PROJECTILE_PACKET'});
  }
  if(splash) choices.push({effect:splash,strength:blastBinding?.strength??mode.explosion.status_strength??0,contact_ap:mode.explosion.pen,channel:'EXPLOSION_CONTACT'});
  const selected=choices.filter(c=>c.strength>0&&c.contact_ap>=request.target.armor
    &&(accumulate||c.strength>=main.fire_threshold_max)).sort((a,b)=>b.strength-a.strength)[0];
  if(!selected) return null;
  const e=selected.effect;
  const hit=directDamage({normal:e.normal,durable:e.durable,penetration:e.pen,durability:main.durable,
    armor:main.armor,equal_multiplier:ap.equal,greater_multiplier:ap.greater,rounding:'none'});
  if(hit.status!=='KNOWN'||!(e.duration_s>0))return null;
  return {id:e.id,dps:hit.damage*main.fire_multiplier*(mode.fire_module_multiplier??1),duration_ms:Math.round(e.duration_s*1000),
    trigger_strength:selected.strength,threshold:main.fire_threshold_max,channel:selected.channel,
    trigger_packets:Math.ceil(main.fire_threshold_max/selected.strength),
    accumulation_basis:'DECLARED_UNINTERRUPTED_BURST; NOMINAL_STRENGTH; UNKNOWN_DECAY_AND_PROBABILITY_NOT_FABRICATED',
    target_pool:'main',fire_multiplier:main.fire_multiplier,module_multiplier:mode.fire_module_multiplier??1,
    refresh_policy:'NONSTACKING; SAME_WEAPON_REFRESH_CANDIDATE',
    caveat:'ACCUMULATION_RESETS_ON_PAUSE; NOT_A_MEASURED_IGNITION_GUARANTEE; NO_DOT_DPS_STACKING'};
}

// User confirms immediate ignition on actual ground contact. Geometry/number
// of contacts is supplied by the caller, never borrowed from explosion radius.
export function groundContactFire(facts,target,ap) {
  const effect=facts.effects.find(e=>e.id==='Fire');
  if(!facts.ground_fire||!effect||!Number.isFinite(target.fire_multiplier))return null;
  const hit=directDamage({normal:effect.normal,durable:effect.durable,penetration:effect.pen,durability:target.durable,
    armor:target.armor,equal_multiplier:ap.equal,greater_multiplier:ap.greater,rounding:'none'});
  if(hit.status!=='KNOWN')return null;
  return {id:'Fire',dps:hit.damage*target.fire_multiplier*(facts.fire_damage_multiplier??1),duration_ms:effect.duration_s*1000,
    channel:'USER_IMMEDIATE_GROUND_CONTACT',target_pool:'main',refresh_policy:'SAME_FIRE_NOT_AN_EXTRA_DOT_STACK',
    contact_condition:'ACTUAL_CONTACT_REQUIRED; GROUND_AREA_GEOMETRY_NOT_INFERRED',independent_ground_dps:0,
    independent_ground_dps_basis:'NO_SOURCED_SEPARATE_CHANNEL; NOT_PROOF_OF_ZERO'};
}

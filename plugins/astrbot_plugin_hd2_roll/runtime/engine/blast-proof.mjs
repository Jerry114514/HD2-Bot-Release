// Independent real-build derivation. Candidate selections and a self-declared
// integration.hit_path are not proof. Geometry/ExDR/overflow remain dependencies
// until their scoped facts are verified for the profile's build.
import {partBlastDamage, verificationRequired} from '../explosions/event.mjs';
import {comparePenetration} from '../explosions/penetration.mjs';
import {directDamage} from './damage.mjs';
import {stable} from './common.mjs';
export function verifiedBlastAmount(model, a, e, build, read, evidence, grade = 'verified') {
  const b=e.blast, c=model.facts.components?.find(x=>x.id===b.component_id);
  if(!c)return false;
  const row=id=>model.facts.facts.find(x=>x.id===id);
  const scoped=(id,subject,property,component)=>{
    const f=row(id),v=read(model,id,build);
    return v.known&&f?.subject_id===subject&&f.property===property&&
      (component===undefined||f.component_id===component)?v.value:undefined;
  };
  const part=model.targets.targets.find(t=>t.id===e.target_id)?.parts?.find(p=>p.id===`${e.target_id}.${e.part_id??'main'}`);
  if(!part)return false;
  const pf=name=>read(model,part.properties?.[name],build);
  const armor=pf('armor_level'),durable=pf('durable');
  if(!armor.known||!durable.known||typeof durable.value!=='string')return false;
  const pct=/^(\d+(?:\.\d+)?)\s*%$/.exec(durable.value.trim());
  if(!pct||Number(pct[1])>100)return false;
  const durability=Number(pct[1])/100;
  const calibration=model.profiles.mechanism_calibration;
  // The AP/armor table is a mechanism producer: it is read at the grade of this run, so a
  // source-reviewed run can never lean on a verified table (and the reverse).
  const mechanismState=grade==='source_reviewed'?'source_reviewed':'verified';
  if(calibration?.state!==mechanismState||calibration.game_build!==build||
     calibration.pen_vs_armor_multiplier_table?.state!==mechanismState||!evidence(model,calibration.evidence_refs,build,grade))return false;
  const ap_values={},armor_values={};
  for(const r of calibration.armor_tier_ladder?.rows||[]){
    if(r.ap_en)ap_values[r.ap_en]=r.value;
    if(r.av_en)armor_values[r.av_en]=r.value;
    if(r.zh)armor_values[r.zh]=r.value;
  }
  const rules=calibration.pen_vs_armor_multiplier_table.rules;
  if(!rules||!Number.isFinite(rules.equal_multiplier)||!Number.isFinite(rules.greater_multiplier))return false;
  const penModel={ap_values,armor_values,multipliers:{below:rules.less??0,equal:rules.equal_multiplier,greater:rules.greater_multiplier},state:'sourced',source_refs:calibration.evidence_refs};
  const get=(key,prop)=>typeof b.facts?.[key]==='string'&&b.facts[key]===c.properties?.[prop]?scoped(b.facts[key],c.equipment_id,prop,c.id):undefined;
  // The observed aimed claim is a target/part/component-scoped fact, not a
  // radius-only inference. Keep the exact claim in this fact's value.
  const claim=b.claim;
  const claimFact=row(b.claim_fact_ref),knownClaim=read(model,b.claim_fact_ref,build);
  if(!knownClaim.known||claimFact.subject_id!==e.target_id||claimFact.component_id!==c.id||claimFact.property!=='explosion_hit_claim'||
     !claim||stable(knownClaim.value)!==stable(claim)||claim.part_id!==(e.part_id??'main')||claim.receives_explosion!==true||!Number.isFinite(claim.distance_m)||claim.distance_m<0||!evidence(model,claim.evidence_refs,build))return false;
  let amount;
  if(b.contact_share==='direct'){
    if(c.kind!=='projectile')return false;
    const normal=get('damage_standard','damage_standard'),dmgDurable=get('damage_durable','damage_durable'),pen=get('pen_direct','pen_direct');
    const p=comparePenetration({penetration:pen,armor:armor.value,model:penModel});
    if(p.status!=='KNOWN')return false;
    if(c.properties?.pellets&&read(model,c.properties.pellets,build).value!==1)return false;
    const r=directDamage({normal,durable:dmgDurable,durability,penetration:p.ap,armor:p.av,equal_multiplier:rules.equal_multiplier,greater_multiplier:rules.greater_multiplier});
    if(r.status!=='KNOWN')return false;
    amount=r.damage;
  }else{
    if(c.kind!=='explosion')return false;
    const exdr=b.exdr;
    if(exdr?.state!=='sourced'||scoped(exdr.fact_ref,e.target_id,'explosion_damage_multiplier',part.id)!==exdr.multiplier||!evidence(model,exdr.source_refs,build))return false;
    const blast={damage:{normal:get('damage_inner','damage_inner_radius'),durable:get('inner_durable','inner_durable')},
      inner_radius:get('inner_radius','area_of_effect_inner_radius'),outer_radius:get('outer_radius','area_of_effect_outer_radius'),
      pen_inner:get('pen_inner','pen_inner'),pen_outer:get('pen_outer','pen_outer')};
    if(claim.distance_m>blast.inner_radius){
      const curve=b.curve;
      if(curve?.state!=='sourced'||scoped(curve.fact_ref,c.equipment_id,'explosion_falloff_curve',c.id)!==curve.curve||!evidence(model,curve.source_refs,build))return false;
      blast.falloff_curve=curve.curve;blast.falloff_evidence=curve;
    }
    const r=partBlastDamage({blast,part:{armor_text:armor.value,durable:{status:'KNOWN',fraction:durability}},distance_m:claim.distance_m,model:penModel,exdr});
    if(r.status!=='KNOWN'||r.damage.min!==r.damage.max||r.zone!==b.zone)return false;
    const los=verificationRequired(claim.verification_mode,r.zone);
    if(los===null||los&&claim.line_of_sight!=='clear')return false;
    amount=r.damage.min;
  }
  if(e.amount!==amount)return false;
  if(e.part_id!=null){
    const t=pf('percent_to_main');
    if(!t.known||typeof t.value!=='string')return false;
    if(t.value.trim()==='-')return e.transfer==null||e.transfer===0;
    const m=/^(\d+(?:\.\d+)?)\s*%$/.exec(t.value.trim());if(!m)return false;
    const overflow=b.overflow_rule;
    if(!['uncapped','capped_at_remaining'].includes(overflow?.rule)||overflow.state!=='sourced'||
      scoped(overflow.fact_ref,e.target_id,'explosion_overflow_rule',part.id)!==overflow.rule||!evidence(model,overflow.source_refs,build))return false;
    const hp=pf('health');if(!hp.known||!Number.isFinite(hp.value))return false;
    const expected=Math.ceil((overflow.rule==='capped_at_remaining'?Math.min(amount,hp.value):amount)*Number(m[1])/100);
    if((e.transfer??0)!==expected)return false;
  }
  return true;
}

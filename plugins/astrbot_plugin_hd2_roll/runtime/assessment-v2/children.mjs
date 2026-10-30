// Explicit child components only. A payload is one conditional contact, not a
// promise that all submunitions land on one enemy or one body part.
import {projectileMechanics} from '../calibration/practical/attack-mode-core.mjs';
import {directDamage} from '../engine/damage.mjs';
import {blastAt} from './fragments.mjs';

// All child centres share one declared disk. A victim receives only blasts that
// reach its position; neither the parent nor all child direct hits are added.
export function childBurstAt(mode,target,ap,position=[0,0]) {
  const p=mode.child_payload,pattern=mode.child_pattern;
  if(!p||!pattern)return null;
  let total=0,contacts=0;
  for(let i=0;i<p.count;i++){
    const radius=pattern.radius_m*Math.sqrt((i+.5)/p.count),angle=i*2.399963229728653;
    const distance=Math.hypot(position[0]-radius*Math.cos(angle),position[1]-radius*Math.sin(angle));
    const b=blastAt(p.explosion,distance);
    if(!b)return null;
    const d=directDamage({normal:b.normal,durable:b.durable,penetration:b.pen,durability:target.durable,
      armor:target.armor,equal_multiplier:ap.equal,greater_multiplier:ap.greater});
    if(d.status!=='KNOWN')return null;
    total+=d.damage;if(d.damage>0)contacts++;
  }
  return {damage:total,contacts,emitted:p.count,pattern,component_id:p.component_id,
    scope:'DECLARED_SCATTER_GEOMETRY; CHILD_BLAST_ONLY; NOT_ALL_CHILDREN_HIT_ONE_ENEMY'};
}
const field=(rows,key)=>rows?.find(row=>row.key===key)?.raw??null;
const number=raw=>{const match=/^\s*([\d,]+(?:\.\d+)?)(?=\s|$|[a-z%°])/i.exec(String(raw??''));
  return match?Number(match[1].replaceAll(',','')):null;};

export function bindExplodingChildren(item,facts,tiers) {
  const modes=facts.modes??[];
  for(const mode of modes) {
    delete mode.child_payload;
    const child=mode.unbound_child_projectiles;
    if(!child?.component_id||!Number.isSafeInteger(child.count)||child.count<1)continue;
    const found=item.tables.filter(t=>t.kind==='attack_projectile'&&t.id===child.component_id);
    if(found.length!==1)continue;
    const projectile=found[0],mechanics=projectileMechanics(item,projectile,tiers);
    const blast=mechanics.explosion;
    // A named, independently resolved impact blast is required; no fabricated
    // explosion from a projectile lifetime or an unrelated source table.
    if(!blast)continue;
    const explosion=Object.fromEntries(Object.entries(blast).map(([key,value])=>[key,value??null]));
    const payload={component_id:child.component_id,count:child.count,
      normal:number(field(projectile.rows,'damage_standard')),
      durable:number(field(projectile.rows,'damage_durable')),
      pen:tiers[field(projectile.rows,'pen_direct')]??null,explosion,delay_ms:null,
      source_ref:facts.source_ref??`wiki:${item.id}@${item.provenance?.revid??'unknown'}`,
      contact_policy:'ONE_CHILD_CONTACT_LOWER_BOUND; FULL_SCATTER_UNRESOLVED'};
    if(item.id==='heavy_support_sta_x3_wasp_launcher'&&mode.mode_id==='StA-X3_P1') {
      payload.ammo_cost=7;payload.requires_full_magazine=true;
      payload.partial_magazine_distribution=null;
    }
    mode.child_payload=payload;
  }
  return facts;
}

// Diagnostics only: counts and blast distance are caller-declared conditions.
// The all-children value is an unattainability-agnostic arithmetic ceiling, not
// an expected hit count, damage to a fatal part, or a time-based score.
export function diagnoseExplodingChildren(mode,target,ap,{direct_contacts=null,blast_contacts=null,blast_distance_m=null}={}) {
  const p=mode?.child_payload;
  if(!p)return {status:'UNKNOWN',reason:'CHILD_PAYLOAD_REQUIRED'};
  if(!Number.isSafeInteger(p.count)||p.count<1)return {status:'UNKNOWN',reason:'CHILD_COUNT_REQUIRED'};
  if([direct_contacts,blast_contacts].some(n=>n!==null&&(!Number.isSafeInteger(n)||n<0||n>p.count)))
    return {status:'UNKNOWN',reason:'CONTACT_COUNT_OUT_OF_RANGE'};
  if(blast_distance_m!==null&&(!Number.isFinite(blast_distance_m)||blast_distance_m<0))
    return {status:'UNKNOWN',reason:'BLAST_DISTANCE_INVALID'};
  const damage=(normal,durable,pen)=>directDamage({normal,durable,penetration:pen,
    durability:target?.durable,armor:target?.armor,equal_multiplier:ap?.equal,greater_multiplier:ap?.greater});
  const hit=damage(p.normal,p.durable,p.pen);
  const inner=p.explosion?damage(p.explosion.normal,p.explosion.durable,p.explosion.pen):{status:'UNKNOWN_DATA'};
  const blast=p.explosion;
  let atDistance=null;
  // blastAt assumes valid geometry; do not interpret missing radii as zero.
  if(blast_distance_m!==null&&blast&&Number.isFinite(blast.inner_radius_m)&&Number.isFinite(blast.outer_radius_m)
    &&blast.inner_radius_m>=0&&blast.outer_radius_m>=blast.inner_radius_m){
    const value=blastAt(blast,blast_distance_m);
    if(value)atDistance=damage(value.normal,value.durable,value.pen);
  }
  const known=x=>x?.status==='KNOWN'?x.damage:null;
  const direct=known(hit),innerDamage=known(inner),blastDamage=known(atDistance);
  const declaredDirect=direct_contacts===null||direct===null?null:direct_contacts*direct;
  const declaredBlast=blast_contacts===0?0:blast_contacts===null||blastDamage===null?null:blast_contacts*blastDamage;
  return {status:'DIAGNOSTIC',component_id:p.component_id,source_ref:p.source_ref,
    emitted_count:p.count,ammo_cost:p.ammo_cost??1,requires_full_magazine:p.requires_full_magazine??false,
    partial_magazine_distribution:p.partial_magazine_distribution??null,
    direct:{per_contact:direct,contacts:direct_contacts,declared_damage:declaredDirect},
    explosion:{inner_per_contact:innerDamage,distance_m:blast_distance_m,per_contact_at_distance:blastDamage,
      contacts:blast_contacts,declared_damage:declaredBlast},
    declared_combined_damage:declaredDirect===null||declaredBlast===null?null:declaredDirect+declaredBlast,
    theoretical_all_emitted_upper_bound:direct===null||innerDamage===null?null:p.count*(direct+innerDamage),
    upper_bound_scope:'ALL_CHILDREN_DIRECT_AND_INNER_BLAST_CONTACT_SAME_PART; NOT_ASSUMED',
    delay_ms:p.delay_ms??null,scatter:'UNKNOWN',contact_policy:p.contact_policy,
    scoring_scope:'DIAGNOSTIC_ONLY; NOT_ADDED_TO_PARENT_SCORE'};
}

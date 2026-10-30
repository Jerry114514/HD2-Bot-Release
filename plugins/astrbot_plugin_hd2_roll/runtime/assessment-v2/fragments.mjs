import {directDamage} from '../engine/damage.mjs';

// Declared contact counts, never all emitted fragments hitting one enemy.
export function fragmentHit(mode,target,ap,contacts=0) {
  const f=mode.fragments;
  if (!f || contacts===0) return {status:'KNOWN',damage:0,contacts:0};
  if (!Number.isSafeInteger(contacts)||contacts<0||contacts>f.count) return {status:'UNKNOWN',reason:'FRAGMENT_CONTACT_BUDGET'};
  const hit=directDamage({normal:f.normal,durable:f.durable,penetration:f.pen,
    durability:target.durable,armor:target.armor,equal_multiplier:ap.equal,greater_multiplier:ap.greater});
  return hit.status==='KNOWN'?{status:'KNOWN',damage:hit.damage*contacts,per_fragment:hit.damage,contacts,
    component_id:f.component_id,scope:'DECLARED_CONTACTS; OWN_ARMOR_AND_DURABILITY; NOT_EXPLOSIVE_DAMAGE'}:hit;
}

export function mainFragmentContacts(mode,target) {
  return target.part_id==='main' ? mode.fragment_policy?.main_contacts_per_shot ?? 0 : 0;
}

export function blastAt(blast,distance=0) {
  if (!blast || distance<=blast.inner_radius_m) return blast;
  if (distance>=blast.outer_radius_m) return {...blast,normal:0,durable:0};
  if (![blast.outer_normal,blast.outer_durable,blast.outer_pen].every(Number.isFinite)) return null;
  const fraction=(blast.outer_radius_m-distance)/(blast.outer_radius_m-blast.inner_radius_m);
  return {...blast,normal:blast.outer_normal*fraction,durable:blast.outer_durable*fraction,pen:blast.outer_pen};
}

import {combatComponentDamage} from './elemental-damage.mjs';
import {handlingTiming} from './handling.mjs';
import {shotAt, sustainedTempo} from './timing.mjs';
import {fragmentHit,blastAt} from './fragments.mjs';
import {boundFire,groundContactFire} from './status.mjs';
import {childBurstAt} from './children.mjs';

// Finite 3x3 packets. Each enemy owns one Main HP pool; subsequent blasts only
// receive credit for remaining HP. Fragment contacts are a declared scenario,
// with an explicit emitted budget, not a probability of hitting a crowd.
export function areaChaffService(facts, mode, requests, ap, scenario) {
  const blast = mode.explosion, spacings=scenario.area?.spacing_samples_m??[scenario.strategic?.neighbour_spacing_m];
  const minimum=Math.min(...spacings);
  const directFire=mode.direct_status_effects?.some(e=>e.id==='Fire')&&!mode.continuous;
  if (!((blast?.outer_radius_m > minimum || blast?.inner_radius_m >= minimum || mode.fragments||directFire||mode.spread_targets||mode.arc_policy||mode.child_pattern)
    && spacings.length&&spacings.every(s=>s>0)) || !mode.feed_bound
    || requests.some(r => r.target.part_id !== 'main') || !(mode.rpm > 0)
    || !(mode.capacity > 0) || !Number.isFinite(mode.reload_ms)) return null;
  const handling = handlingTiming(mode.handling, scenario.handling);
  if (handling.status !== 'ESTIMATE') return null;
  const hit = (part, target) => combatComponentDamage(part,target,ap).damage;
  const victims = requests.flatMap(r => Array.from({length: r.count}, () => r.target));
  const placements = [[0,0],[-1,0],[1,0],[0,-1],[0,1],[-1,-1],[1,-1],[-1,1],[1,1]];
  const audit = [], actions = [], fragmentAudit=[];
  // Split each species evenly, so dense packets do not get all the easiest
  // enemies. These are declared scenarios, not measured spawn probabilities.
  const packets=spacings.flatMap((spacing,index)=>{
    const sample=requests.flatMap(r=>Array.from({length:Math.floor(r.count/spacings.length)+(index<r.count%spacings.length?1:0)},()=>r.target));
    const chunks=[];
    for(let offset=0;offset<sample.length;offset+=placements.length)chunks.push({spacing,targets:sample.slice(offset,offset+placements.length)});
    return chunks;
  });
  let offset=0,secondaryBlastContacts=0,burnDamage=0,fireContacts=0,groundContacts=0;
  let elapsed=mode.available_at_ms??0,loaded=mode.capacity,reloads=0,neutralized=0,completed=0,previousCenter=null;
  const interval = Math.ceil(Math.max(60000 / mode.rpm, mode.charge_ms ?? 0));
  const acquire = Math.ceil(handling.acquire_ms);
  for (const packet of packets) {
    const spacing=packet.spacing;
    const group = packet.targets.map((t,i) => ({target:t,hp:t.hp,pos:placements[i],at:0,until:0,dps:0,death:0,strength:0,lastStatus:null}));
    const advance=(v,at)=>{
      const span=Math.max(0,Math.min(at,v.until)-v.at),dealt=Math.min(v.hp,span*v.dps/1000);
      if(v.hp>0&&dealt>=v.hp&&v.dps>0)v.death=v.at+v.hp/v.dps*1000;
      v.hp-=dealt;burnDamage+=dealt;v.at=at;
    };
    let shots = 0;
    while (group.some(v => v.hp > 0)) {
      if(mode.disposable&&actions.length>=mode.capacity){for(const v of group)advance(v,Math.max(v.at,v.until));break;}
      const center = group.find(v => v.hp > Math.max(0,v.until-v.at)*v.dps/1000+1e-7);
      if(!center){for(const v of group)advance(v,Math.max(v.at,v.until));break;}
      let ready=elapsed;
      if(!loaded){ready+=mode.reload_ms;loaded=mode.capacity;reloads++;}
      const newTarget=center!==previousCenter,shotAcquire=newTarget?acquire:0;
      elapsed=shotAt({ready_at:ready,previous_at:actions.length?elapsed:null,interval_ms:interval,
        acquire_ms:shotAcquire,charge_ms:mode.charge_ms??0,warmup_ms:mode.warmup_ms??0,work_ms:mode.work_ms??0,new_target:newTarget});
      previousCenter=center;
      loaded--;
      const impact=elapsed+(mode.detached_delay_ms??0);
      for(const v of group)advance(v,impact);
      let dealt = 0;
      let remainingFragments=mode.fragments?.count??0, contacts=0;
      const live=group.filter(v=>v.hp>0).sort((a,b)=>Math.hypot(a.pos[0]-center.pos[0],a.pos[1]-center.pos[1])-Math.hypot(b.pos[0]-center.pos[0],b.pos[1]-center.pos[1]));
      const directPackets=new Map();
      if(mode.arc_policy){
        const arcs=mode.arc_policy;
        for(let pulse=0;pulse<(arcs.pulses??1);pulse++)for(let bolt=0;bolt<arcs.bolts;bolt++){
          const visited=new Set();
          for(let step=0;step<arcs.chains.length;step++){
            const v=live[(bolt+step)%live.length];if(visited.has(v))break;visited.add(v);
            const loss=(arcs.live_target_fraction??1)*(arcs.pulse_location_factors?.[pulse]??1);
            directPackets.set(v,(directPackets.get(v)??0)+arcs.chains[step]*loss);
          }
        }
      }else if(mode.spread_targets){
        const n=Math.min(mode.spread_targets,live.length);
        for(let p=0;p<mode.pellets;p++){const v=live[p%n];directPackets.set(v,(directPackets.get(v)??0)+1);}
      }else directPackets.set(center,mode.pellets);
      for (const v of group.filter(v => v.hp > 0)) {
        const planar = Math.hypot(v.pos[0]-center.pos[0],v.pos[1]-center.pos[1]) * spacing;
        const distance=Math.hypot(planar,mode.proximity_m??0);
        const fragmentCount=mode.fragments&&distance<=(scenario.fragments?.contact_radius_m??0)
          ? Math.min(remainingFragments,mode.fragment_policy.max_per_target):0;
        const shard=fragmentHit(mode,v.target,ap,fragmentCount);
        remainingFragments-=fragmentCount;contacts+=fragmentCount;
        const localBlast=blastAt(blast,distance);
        if(blast&&distance<blast.outer_radius_m&&!localBlast)return null;
        const blastDamage=localBlast?hit(localBlast,v.target):0;
        if(v!==center&&blastDamage>0)secondaryBlastContacts++;
        const children=mode.child_pattern?childBurstAt(mode,v.target,ap,[(v.pos[0]-center.pos[0])*spacing,(v.pos[1]-center.pos[1])*spacing]):null;
        const damage = children?children.damage:(hit(mode,v.target)*(directPackets.get(v)??0)
          + blastDamage+shard.damage+(v===center&&mode.attached_thermite?hit(mode.attached_thermite,v.target)*mode.attached_thermite.duration_s:0));
        if (!Number.isFinite(damage)) return null;
        const paid = Math.min(v.hp,damage); dealt += paid; v.hp -= paid;
        if(v.hp<=0){v.death=impact;continue;}
        const contactMode={...mode,pellets:directPackets.get(v)??0,direct_status_effects:directPackets.has(v)?mode.direct_status_effects:[],
          status_applications:directPackets.has(v)?mode.status_applications:[],
          explosion:distance<blast?.outer_radius_m?localBlast:null};
        let fire=boundFire(contactMode,{target:v.target},ap);
        if(fire){
          v.strength=(v.lastStatus===null||impact-v.lastStatus<=interval+1?v.strength:0)+fire.trigger_strength;
          v.lastStatus=impact;
          if(v.strength<fire.threshold)fire=null;
        }
        // Only the selected ground-impact point is a known ground contact.
        // The surrounding blast radius never supplies an invented fire-pool radius.
        const ground=facts.category==='throwables'&&v===center?groundContactFire(facts,v.target,ap):null;
        const burning=[fire,ground].filter(Boolean).sort((a,b)=>b.dps-a.dps)[0];
        if(burning?.dps>0){v.dps=Math.max(v.dps,burning.dps);v.until=Math.max(v.until,impact+burning.duration_ms);
          fireContacts++;if(ground)groundContacts++;}
      }
      if ((!(dealt > 0)&&!group.some(v=>v.dps>0&&v.until>v.at)) || ++shots > 1000) return null;
      if(mode.fragments)fragmentAudit.push({packet_offset:offset,shot:shots,emitted:mode.fragments.count,contacts,unallocated:remainingFragments});
      actions.push({weapon_id:mode.resource_id??facts.id,mode_id:mode.mode_id,rounds:1,prepare_ms:shotAcquire+(mode.charge_ms??0),
        acquire_ms:shotAcquire,charge_ms:mode.charge_ms??0,warmup_ms:mode.warmup_ms??0,
        interval_ms:interval,effect_delay_ms:mode.detached_delay_ms??0,switch_ms:scenario.weapon_switch_ms,
        measured_target:`finite-packet-${offset}`,measured_part:'main',area_action:true});
    }
    const done=Math.max(...group.map(v=>v.death));neutralized=Math.max(neutralized,done);
    if(actions.length)actions.at(-1).effect_delay_ms=Math.max(mode.detached_delay_ms??0,Math.ceil(done-elapsed));
    audit.push({offset,spacing_m:spacing,targets:group.length,shots,remaining_hp:group.map(v=>v.hp)});
    completed+=group.filter(v=>v.hp<=0).length;
    offset+=group.length;
    if(mode.disposable&&actions.length>=mode.capacity)break;
  }
  if(!completed||!actions.length)return null;
  const entries=actions.map(a=>({btk:1,count:1,acquire_ms:a.acquire_ms}));
  const tempo=sustainedTempo({...mode,entries,interval_ms:interval,acquire_ms:acquire});
  if(tempo.status!=='ESTIMATE') return null;
  const ended=Math.max(neutralized,elapsed+(mode.detached_delay_ms??0));
  return {status:'ESTIMATE',shots:actions.length,count:completed,inventory_required:actions.length,
    ...(completed<victims.length?{partial_cohort:true,requested_count:victims.length}:{}),
    first_to_last_ms:elapsed,neutralized_at_ms:ended,cycle_ms:tempo.pattern_ms,reloads,
    handling,interval_ms:interval,entries:[],sustained:mode.disposable
      ? {status:'FINITE_ONLY',targets_per_pattern:completed,targets_per_second:0,reason:'ONLY_INITIAL_AMMO'}
      : {...tempo,targets_per_pattern:victims.length,targets_per_second:1000*victims.length/tempo.pattern_ms},
    action_plan:actions,original_target_requests:requests.map(r=>({id:`${r.target.target_id}:${r.target.part_id}`,count:r.count})),
    area:{geometry:'FINITE_3X3_MAIN_HP_PACKETS; SPECIES_SPLIT_ACROSS_DENSITIES',spacing_samples_m:spacings,
      inner_radius_m:blast?.inner_radius_m??null,outer_radius_m:blast?.outer_radius_m??null,secondary_blast_contacts:secondaryBlastContacts,audit,
      fire:{damage:burnDamage,applications:fireContacts,explicit_ground_contacts:groundContacts,
        policy:'ONE_NONSTACKING_FIRE_POOL; PAID_REFRESH; GROUND_ONLY_AT_EXPLICIT_IMPACT_CONTACT'},
      spread:mode.spread_targets?{max_targets:mode.spread_targets,pellets_per_shot:mode.pellets,basis:'DECLARED_DENSE_CONE; TOTAL_PELLETS_CONSERVED'}:null,
      arc:mode.arc_policy??null,children:mode.child_pattern?{...mode.child_pattern,emitted:mode.child_payload.count}:null,
      fragments:mode.fragments?{component:mode.fragments,policy:mode.fragment_policy,
        contact_radius_m:scenario.fragments.contact_radius_m,audit:fragmentAudit}:null},
    resolution_basis:'FINITE_MAIN_HP; NO_MULTIPART_OR_DEAD_TARGET_DAMAGE_CREDIT',
    assumptions:['DECLARED_DENSITY_SAMPLES_NOT_SPAWN_PROBABILITIES','SOURCE_OUTER_LINEAR_FALLOFF_AND_OUTER_AP',
      'SOURCED_FIRE_ON_SAME_MAIN_HP; NO_INDEPENDENT_GROUND_DPS_OR_MULTIPART_DAMAGE']};
}

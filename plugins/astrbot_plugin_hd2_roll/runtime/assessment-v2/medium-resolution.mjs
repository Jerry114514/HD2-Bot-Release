import {combatShotDamage as shotDamage} from './elemental-damage.mjs';
import {directDamage} from '../engine/damage.mjs';
import {fragmentHit} from './fragments.mjs';
import {boundFire} from './status.mjs';

const finite = n => typeof n === 'number' && Number.isFinite(n);
const unknown = reason => ({status: 'UNKNOWN', reason});
const damage = (mode, target, ap) => shotDamage({...mode, explosion: null, pellets: mode.pellets ?? 1}, target, ap).per_shot_damage;

// This is a bound collision route, not a generic AP threshold or free extra hit.
export function mediumPacket(mode, request, ap) {
  if(mode.child_pattern)mode={...mode,explosion:null}; // Child blast already replaces the parent packet.
  const target = request.target, {main, policy} = request.resolution;
  if (!main || !target.constitution || !finite(target.transfer_to_main) || target.transfer_cap === null) {
    return unknown('MEDIUM_MAIN_TRANSFER_AND_CONSTITUTION_REQUIRED');
  }
  const direct = damage(mode, target, ap);
  const resistance = request.explosion_resistance;
  if (!finite(direct) || mode.explosion && !finite(resistance)) return unknown('MEDIUM_PART_DAMAGE_REQUIRED');
  const blastPart = mode.explosion ? damage(mode.explosion, target, ap) * (1 - resistance) : 0;
  const blastMain = mode.explosion && resistance === 1 ? damage(mode.explosion, main, ap) : 0;
  const fragments=fragmentHit(mode,main,ap,mode.fragment_policy?.main_contacts_per_shot??0);
  if (fragments.status!=='KNOWN') return unknown('FRAGMENT_CONTACT_BUDGET');
  const chain = policy.cleave_routes?.[`${mode.mode_id}:${target.target_id}:${target.part_id}`];
  let cleave = {damage: 0, status: 'NO_BOUND_CHAIN'};
  if (chain && chain.next_target === `${main.target_id}:${main.part_id}`) {
    // Only zero slowdown is presently bound: no invented speed-to-damage curve.
    if (mode.pen > chain.penetration_cost && mode.penetration_slowdown === 0) {
      const remainingAP = mode.pen - chain.penetration_cost;
      cleave = {status: 'BOUND_ROUTE_ESTIMATE', damage: damage({...mode, pen: remainingAP}, main, ap),
        penetration_after: remainingAP, next_target: chain.next_target, basis: chain.basis,
        source_conflict: chain.source_conflict, no_additional_ammunition: true};
    } else cleave = {damage: 0, status: 'UNRESOLVED_FOLLOW_THROUGH', basis: chain.basis};
  }
  return {status: 'ESTIMATE', direct, blast_part: blastPart, local: direct + blastPart+(target.part_id==='main'?fragments.damage:0),
    fragment_main:target.part_id==='main'?0:fragments.damage,fragments,
    redirected_blast_main: blastMain, cleave,
    transfer_fraction: target.transfer_to_main,
    transfer_limit: target.transfer_cap ? (target.hp + target.constitution.hp) * target.transfer_to_main : null};
}

// Fire only the selected burst. Natural decay and one non-stacking direct gas
// status continue afterward. No second weapon, extra shot or repeated cloud tick
// is invented. First-part break, death and threat-equivalent time stay separate.
export function resolveMediumTarget(mode, request, shotTimes, ap) {
  const packet = mediumPacket(mode, request, ap);
  if (packet.status !== 'ESTIMATE') return packet;
  const {target, resolution: {main, policy}} = request;
  const gas = mode.direct_status_effects?.find(e => e.id === 'Gas');
  const fire=boundFire(mode,request,ap);
  const confusion = mode.direct_status_effects?.find(e => e.id === 'Gas_Confusion');
  const gasDamage = gas ? directDamage({normal: gas.normal, durable: gas.durable, penetration: gas.pen,
    durability: main.durable, armor: main.armor, equal_multiplier: ap.equal, greater_multiplier: ap.greater,
    rounding: 'none'}) : null;
  if (gas && (gasDamage.status !== 'KNOWN' || !(gas.duration_s > 0))) return unknown('MEDIUM_GAS_DAMAGE_REQUIRED');
  const dps = gasDamage?.damage ?? 0;
  let mainHP = main.hp, partHP = target.hp, transferred = 0, partBreak = null;
  let naturalDeath = Infinity, death = Infinity, gasUntil = 0, fireUntil=0, prior = shotTimes[0];
  let mainDirect = 0, mainBlast = 0, mainCleave = 0, gasDealt = 0, remainingConstitution = null, initialConstitution = null;
  let fireDealt=0,fragmentDealt=0,contactMs=0,lastContact=null,ignitionAt=null,firePackets=0;
  const advanceTo = end => {
    const stop = Math.max(prior, Math.min(end, death, naturalDeath));
    let cursor=prior;
    while(cursor<stop) {
      const next=Math.min(stop,...[gasUntil,fireUntil].filter(t=>t>cursor));
      const poisonRate=gasUntil>cursor?dps:0,fireRate=fireUntil>cursor?fire?.dps??0:0;
      const rate=poisonRate+fireRate,span=next-cursor;
      if(rate>0&&mainHP/rate*1000<=span)death=Math.min(death,cursor+mainHP/rate*1000);
      const applied=Math.min(mainHP,span*rate/1000);
      mainHP-=applied;
      if(rate>0){gasDealt+=applied*poisonRate/rate;fireDealt+=applied*fireRate/rate;}
      cursor=next;
    }
    if (remainingConstitution !== null) remainingConstitution = Math.max(0,
      remainingConstitution - Math.max(0, stop - prior) * target.constitution.decay_per_second / 1000);
    death = Math.min(death, naturalDeath);
    prior = stop;
  };
  for (const at of shotTimes) {
    advanceTo(at);
    if (death <= at) break;
    const before = Math.max(0, partHP);
    partHP -= packet.local;
    if (partBreak !== null) {
      remainingConstitution = Math.max(0, remainingConstitution - packet.local);
      naturalDeath = remainingConstitution === 0 ? at : target.constitution.decay_per_second > 0
        ? at + remainingConstitution / target.constitution.decay_per_second * 1000 : Infinity;
    }
    const transfer = target.part_id === 'main' ? packet.local : Math.min(packet.local * packet.transfer_fraction,
      packet.transfer_limit === null ? Infinity : Math.max(0, packet.transfer_limit - transferred));
    transferred += transfer; mainDirect += transfer;
    mainBlast += packet.redirected_blast_main; mainCleave += packet.cleave.damage;
    fragmentDealt+=packet.fragment_main;
    mainHP = Math.max(0, mainHP - transfer - packet.redirected_blast_main - packet.cleave.damage - packet.fragment_main);
    if (partHP <= 0 && partBreak === null) {
      partBreak = at;
      remainingConstitution = Math.max(0, target.constitution.hp - (policy.overflow_to_constitution ? Math.max(0, packet.local - before) : 0));
      initialConstitution = remainingConstitution;
      naturalDeath = remainingConstitution === 0 ? at : target.constitution.decay_per_second > 0
        ? at + remainingConstitution / target.constitution.decay_per_second * 1000 : Infinity;
    }
    if (mainHP === 0) death = Math.min(death, at);
    death = Math.min(death, naturalDeath);
    if (gas) gasUntil = at + gas.duration_s * 1000;
    if (fire?.ignition_contact_ms) {
      // Pauses/cooling do not accumulate contact, and each integration unit
      // neither delivers a full status packet nor starts a second burn.
      contactMs=lastContact===null||at-lastContact<=mode.work_ms+1 ? contactMs+mode.work_ms : mode.work_ms;
      lastContact=at;
      if(ignitionAt===null&&contactMs>=fire.ignition_contact_ms) {
        ignitionAt=at;fireUntil=at+fire.duration_ms;
      }
      else if(ignitionAt!==null&&contactMs>=fire.ignition_contact_ms)fireUntil=at+fire.duration_ms;
    } else if (fire) {
      firePackets=lastContact===null||at-lastContact<=Math.ceil(Math.max(60000/mode.rpm,mode.charge_ms??0)*1.15)+1?firePackets+1:1;
      lastContact=at;
      if(firePackets>=(fire.trigger_packets??1)){ignitionAt??=at;fireUntil=at+fire.duration_ms;}
    }
  }
  const lastShot = shotTimes.at(-1), mainAfterShots = mainHP, constitutionAfterShots = remainingConstitution;
  advanceTo(Math.min(naturalDeath, Math.max(prior, gasUntil,fireUntil)));
  if (!finite(death)) return unknown('MEDIUM_FATAL_ROUTE_AFTER_BURST_UNRESOLVED');
  const controlPolicy = policy.gas_control;
  let control = null, effective = death;
  if (partBreak !== null && confusion?.duration_s > 0 && mode.status_strength > 0
    && controlPolicy?.targets?.includes(target.target_id)) {
    const q = controlPolicy.residual_threat_fraction, delay = controlPolicy.onset_delay_ms;
    if (!(finite(q) && q >= 0 && q <= 1 && finite(delay) && delay >= 0)) return unknown('MEDIUM_CONTROL_POLICY_REQUIRED');
    const windows = shotTimes.map(at => ({start: at + delay, end: at + confusion.duration_s * 1000}));
    let covered = 0, until = partBreak;
    for (const w of windows) {
      const start = Math.max(partBreak, until, w.start), end = Math.min(death, w.end);
      if (end > start) { covered += end - start; until = end; }
    }
    effective = death - (1 - q) * covered;
    control = {kind: 'GAS_CONFUSION', windows, covered_residual_ms: covered, residual_threat_fraction: q,
      onset_delay_ms: delay, duration_ms: confusion.duration_s * 1000, basis: controlPolicy.basis,
      stagger_force: mode.stagger_force, target_stagger_threshold: target.stagger_threshold,
      is_hard_stun: false, is_death: false};
  }
  return {status: 'ESTIMATE', part_break_ms: partBreak, death_ms: Math.ceil(death),
    effective_response_ms: Math.ceil(effective), last_shot_ms: lastShot,
    natural_bleed_death_ms: finite(naturalDeath) ? Math.ceil(naturalDeath) : null,
    constitution: {...target.constitution, after_break_hp: initialConstitution, after_burst_hp: constitutionAfterShots,
      overflow_enabled: policy.overflow_to_constitution},
    main_after_shots: mainAfterShots, main_damage: {transferred: mainDirect, redirected_blast: mainBlast,
      cleave: mainCleave, separate_fragment_main:fragmentDealt,gas_before_death: gasDealt,fire_before_death:fireDealt}, packet, control,
    fire:fire?{...fire,ignition_at_ms:ignitionAt}:null,
    basis: 'SOURCE_POOLS; BOUND_COLLISION_AND_STATUS_ROUTE; ENGINEERING_THREAT_EQUIVALENT',
    assumptions: ['STOP_AFTER_SELECTED_BURST', 'BLEED_AND_GAS_DO_NOT_OCCUPY_HANDS', 'GAS_MAIN_ONLY_NONSTACKING',
      'CONTROL_DOES_NOT_REMOVE_HP', 'EFFECTIVE_RESPONSE_IS_NOT_A_DEATH_OR_SAFETY_TIMESTAMP']};
}

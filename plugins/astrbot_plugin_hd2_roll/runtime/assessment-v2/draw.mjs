import {SLOTS, equipmentIDs, withinExosuitLimit} from '../engine/common.mjs';
import {pools, propose, spaceSize, enumerate, replaceAt} from '../engine/sampling.mjs';
import {seeded, sample, uniformInt} from '../engine/rng.mjs';
import {assessmentLegality, assessmentHoldingLegality, evaluateAssessment} from './evaluate.mjs';

export const EVIDENCE_GRADE = 'assessment_estimate';
// Only small spaces are enumerated, to terminate impossible locked draws.
// Large draws keep no candidate history and never fall back to curated kits.
const SMALL_SPACE = 512n;
const lockAt = (locks, slot) => slot.startsWith('stratagem:') ? locks.stratagems?.[Number(slot.split(':')[1])] : locks[slot];
const at = (loadout, slot) => slot.startsWith('stratagem:') ? loadout.stratagems[Number(slot.split(':')[1])] : loadout[slot];
const validSlot = slot => SLOTS.includes(slot) || /^stratagem:[0-3]$/.test(slot);

export function assessmentView(value) {
  return {...value, evidence_grade:EVIDENCE_GRADE,
    dimensions:value.dimensions ? {...value.dimensions, sustain:value.dimensions.endurance} : undefined};
}

export function beginAssessmentDraw(model, catalog, options = {}) {
  const mode=options.mode ?? 'assessment', constraints=options.constraints ?? {};
  const seed=options.seed ?? crypto.getRandomValues(new Uint32Array(1))[0];
  if (!['assessment','certified','pure'].includes(mode) || !Number.isInteger(seed) || seed<0 || seed>0xffffffff)
    return {result:{status:'INVALID_INPUT'}};
  const prepared=pools(catalog,constraints);
  const task={model,catalog,mode,constraints,seed,rng:seeded(seed),prepared,completed:0,counts:{},result:null};
  if(prepared.status!=='READY'){task.result=prepared;return task;}
  // Fixed holding conflicts cannot be repaired by drawing the free slots.
  const fixed=[...SLOTS.map(s=>prepared.locks[s]),...prepared.locks.stratagems].filter(Boolean);
  const fixedLegality=assessmentHoldingLegality(fixed,catalog,model);
  if(fixedLegality.status==='FAIL'){
    task.result={status:'INVALID_LOCKS',reason:fixedLegality.reason};return task;
  }
  task.space_size=spaceSize(prepared);
  if(BigInt(task.space_size)<=SMALL_SPACE){
    const candidates=[...enumerate(prepared)];
    task.candidates=sample(candidates,candidates.length,task.rng);
  }
  return task;
}

export function stepAssessmentDraw(task, evaluate=evaluateAssessment) {
  if(task.result)return task.result;
  if(task.candidates && task.completed===task.candidates.length){
    return task.result={status:task.mode==='pure'?'NO_LEGAL_LOADOUT':'NO_ACCEPTABLE_LOADOUT',
      completed:task.completed,counts:task.counts,seed:task.seed,exhaustive:true};
  }
  const loadout=task.candidates?.[task.completed] ?? propose(task.prepared,task.rng);
  const legal=assessmentLegality(loadout,task.catalog,task.model,task.constraints);
  const value=legal.status!=='PASS' ? {status:legal.status==='FAIL'?'REJECTED_ILLEGAL':'UNKNOWN',gates:{legality:legal}}
    :task.mode==='pure' ? {status:'UNVERIFIED'} : evaluate(loadout,task.model,task.catalog,task.constraints);
  task.completed++;
  task.counts[value.status]=(task.counts[value.status]??0)+1;
  if(value.status==='UNVERIFIED'||(value.status==='ESTIMATED_PASS'&&value.acceptance_ready===true)){
    return task.result={...assessmentView(value),status:value.status==='UNVERIFIED'?'UNVERIFIED':'ASSESSMENT_PASS',
      assessment_status:value.status,loadout,completed:task.completed,counts:task.counts,seed:task.seed};
  }
  return {status:'SEARCHING',completed:task.completed,counts:task.counts,seed:task.seed};
}

// Output edits skip ability and holding checks, except the always-on mech limit.
// Keep the other six slots fixed; preserve slot identity, uniqueness and user locks.
export function assessmentReplacements(loadout,slot,model,catalog,constraints={}) {
  if(!loadout||!validSlot(slot))return {status:'INVALID_INPUT'};
  const prepared=pools(catalog,constraints);
  if(prepared.status!=='READY')return prepared;
  if(lockAt(prepared.locks,slot))return {status:'INVALID_LOCKS',reason:'SLOT_LOCKED'};
  for(const other of [...SLOTS,...[0,1,2,3].map(n=>'stratagem:'+n)]){
    if(lockAt(prepared.locks,other)&&lockAt(prepared.locks,other)!==at(loadout,other))
      return {status:'INVALID_LOCKS',reason:'LOCKED_LOADOUT_MISMATCH'};
  }
  const ids=prepared.values[slot.startsWith('stratagem:')?'stratagem':slot];
  const rows=ids.filter(id=>id!==at(loadout,slot)).map(id=>{
    const nextIds=equipmentIDs(replaceAt(loadout,slot,id));
    const reason=new Set(nextIds).size!==7?'DUPLICATE_SELECTION':!withinExosuitLimit(nextIds)?'MULTIPLE_EXOSUITS':null;
    return {id,status:reason?'REJECTED_ILLEGAL':'UNVERIFIED',...(reason?{reason}:{})};
  });
  return {status:'REPLACEMENTS',rows};
}

export function chooseAssessmentReplacement(loadout,slot,rows,next) {
  const legal=rows.filter(r=>r.status==='UNVERIFIED');
  if(!legal.length)return {status:'NO_LEGAL_REPLACEMENT'};
  return {status:'UNVERIFIED',loadout:replaceAt(loadout,slot,legal[uniformInt(legal.length,next)].id),reason:'USER_ADJUSTMENT'};
}

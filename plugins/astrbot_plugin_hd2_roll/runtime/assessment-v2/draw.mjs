import {SLOTS, equipmentIDs, withinExosuitLimit} from '../engine/common.mjs';
import {pools, propose, spaceSize, enumerate, replaceAt} from '../engine/sampling.mjs';
import {seeded, sample, uniformInt} from '../engine/rng.mjs';
import {assessmentLegality, assessmentHoldingLegality, evaluateAssessment, previewAssessment} from './evaluate.mjs';
import {REPAIR_POLICY,repairMeasure,contributionLoss,weakestContributions,candidateWeight,preferredTrials,bottleneckTrials} from './repair.mjs';

export const EVIDENCE_GRADE = 'assessment_estimate';
// Only small spaces are enumerated, to terminate impossible locked draws.
// Large draws keep no candidate history and never fall back to curated kits.
const SMALL_SPACE = 512n;
const lockAt = (locks, slot) => slot.startsWith('stratagem:') ? locks.stratagems?.[Number(slot.split(':')[1])] : locks[slot];
const at = (loadout, slot) => slot.startsWith('stratagem:') ? loadout.stratagems[Number(slot.split(':')[1])] : loadout[slot];
const validSlot = slot => SLOTS.includes(slot) || /^stratagem:[0-3]$/.test(slot);
const allSlots = [...SLOTS,...[0,1,2,3].map(n=>'stratagem:'+n)];

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
  const task={model,catalog,mode,constraints,seed,rng:seeded(seed),prepared,completed:0,counts:{},result:null,
    // Unexposed diagnostic switch for a same-model comparison with whole-kit rejection.
    local_repair:options.local_repair!==false,repair:null,
    search_stats:{whole_draws:0,local_trials:0,ablations:0,previews:0,improvements:0,restarts:0},
    modelById:new Map(model.equipment.map(row=>[row.equipment_id,row]))};
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

const searching=task=>({status:'SEARCHING',completed:task.completed,counts:task.counts,seed:task.seed});

function repairParent(task,loadout,value,round=0) {
  const measure=repairMeasure(value);
  if(!measure)return null;
  const choices=allSlots.filter(slot=>!lockAt(task.prepared.locks,slot)).map(slot=>{
    const pool=task.prepared.values[slot.startsWith('stratagem:')?'stratagem':slot];
    const ids=pool.filter(id=>id!==at(loadout,slot)&&
      assessmentLegality(replaceAt(loadout,slot,id),task.catalog,task.model,task.constraints).status==='PASS');
    return {slot,ids};
  }).filter(row=>row.ids.length);
  return choices.length?{loadout,measure,round,choices,contributions:[],queue:null,ranked:[],verify:null,rankMeasure:null,trials:[]}:null;
}

function evaluateCandidate(task,loadout,evaluate) {
  const legal=assessmentLegality(loadout,task.catalog,task.model,task.constraints);
  const value=legal.status!=='PASS' ? {status:legal.status==='FAIL'?'REJECTED_ILLEGAL':'UNKNOWN',gates:{legality:legal}}
    :task.mode==='pure' ? {status:'UNVERIFIED'} : evaluate(loadout,task.model,task.catalog,task.constraints);
  task.completed++;
  task.counts[value.status]=(task.counts[value.status]??0)+1;
  if(value.status==='UNVERIFIED'||(value.status==='ESTIMATED_PASS'&&value.acceptance_ready===true)){
    return task.result={...assessmentView(value),status:value.status==='UNVERIFIED'?'UNVERIFIED':'ASSESSMENT_PASS',
      assessment_status:value.status,loadout,completed:task.completed,counts:task.counts,seed:task.seed};
  }
  return value;
}

export function stepAssessmentDraw(task, evaluate=evaluateAssessment, preview=evaluate===evaluateAssessment?previewAssessment:evaluate) {
  if(task.result)return task.result;
  if(task.repair) {
    const parent=task.repair;
    if(parent.contributions.length<parent.choices.length) {
      const choice=parent.choices[parent.contributions.length];
      const disabled=[...new Set([...(task.constraints.disabled_contributions??[]),at(parent.loadout,choice.slot)])];
      // Keep all seven slot IDs: ablation removes contribution, not slot legality.
      // These previews do not run resource simulations; full trials yield separately.
      if(!parent.rankMeasure) {
        parent.rankMeasure=repairMeasure(preview(parent.loadout,task.model,task.catalog,task.constraints));
        task.search_stats.previews++;
      }
      const without=preview(parent.loadout,task.model,task.catalog,{...task.constraints,disabled_contributions:disabled});
      parent.contributions.push({...choice,loss:contributionLoss(parent.rankMeasure,repairMeasure(without))});
      task.search_stats.ablations++;
      return searching(task);
    }
    if(!parent.queue) {
      const weak=weakestContributions(parent.contributions);
      const selected=sample(weak,Math.min(REPAIR_POLICY.weakest_choices,weak.length),task.rng);
      // Probe every unlocked slot once: low present contribution does not imply
      // the best replacement opportunity. Weak slots receive one extra guided draw.
      parent.queue=[];
      for(const choice of parent.choices) {
        const available=[...choice.ids];
        const count=selected.some(s=>s.slot===choice.slot)?REPAIR_POLICY.candidates_per_slot:1;
        for(let n=0;n<count&&available.length;n++) {
          let index;
          if(n===0)index=uniformInt(available.length,task.rng);
          else {
            const weights=available.map(id=>candidateWeight(task.modelById.get(id),parent.measure));
            let pick=(task.rng()/0x100000000)*weights.reduce((a,b)=>a+b,0);index=weights.length-1;
            for(let i=0;i<weights.length;i++){pick-=weights[i];if(pick<0){index=i;break;}}
          }
          const [id]=available.splice(index,1);
          parent.queue.push(replaceAt(parent.loadout,choice.slot,id));
        }
      }
    }
    if(parent.queue.length) {
      const loadout=parent.queue.shift();task.search_stats.previews++;
      const measure=repairMeasure(preview(loadout,task.model,task.catalog,task.constraints));
      if(measure)parent.ranked.push({loadout,measure});
      return searching(task);
    }
    if(!parent.verify) {
      // Shuffle ties before ranking; this bounded shortlist is only a search hint.
      const shortlist=bottleneckTrials(parent.rankMeasure,
        sample(parent.ranked,parent.ranked.length,task.rng),parent.measure.needs);
      parent.directions=shortlist.directions;
      // Give either layer the first full trial; ties cannot favor slot/group order.
      parent.verify=sample(shortlist.candidates,shortlist.candidates.length,task.rng);
    }
    if(parent.verify.length) {
      const {loadout}=parent.verify.shift();task.search_stats.local_trials++;
      const value=evaluateCandidate(task,loadout,evaluate);
      if(task.result)return task.result;
      parent.trials.push({loadout,value,measure:repairMeasure(value)});
      return searching(task);
    }
    const improvements=preferredTrials(parent.measure,parent.trials);
    if(improvements.length&&parent.round+1<REPAIR_POLICY.max_rounds) {
      const chosen=improvements[uniformInt(improvements.length,task.rng)];
      task.search_stats.improvements++;
      task.repair=repairParent(task,chosen.loadout,chosen.value,parent.round+1);
    } else {
      task.repair=null;task.search_stats.restarts++;
    }
    return searching(task);
  }
  if(task.candidates && task.completed===task.candidates.length){
    return task.result={status:task.mode==='pure'?'NO_LEGAL_LOADOUT':'NO_ACCEPTABLE_LOADOUT',
      completed:task.completed,counts:task.counts,seed:task.seed,exhaustive:true};
  }
  const loadout=task.candidates?.[task.completed] ?? propose(task.prepared,task.rng);
  task.search_stats.whole_draws++;
  const value=evaluateCandidate(task,loadout,evaluate);
  if(task.result)return task.result;
  // Exact small-space enumeration retains its termination proof. Pure draws never repair.
  if(task.local_repair&&!task.candidates&&task.mode!=='pure'&&value.gates?.legality?.status==='PASS')
    task.repair=repairParent(task,loadout,value);
  return searching(task);
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

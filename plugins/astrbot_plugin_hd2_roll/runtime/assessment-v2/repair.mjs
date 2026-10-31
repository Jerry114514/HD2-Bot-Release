// Search preferences only: never change the six admission scores or physical gates.
export const REPAIR_POLICY = Object.freeze({max_rounds:2, weakest_choices:2, candidates_per_slot:2, retained_choices:2, full_checks_per_round:2, surplus_weight:0.15, surplus_cap:40});
export const REPAIR_ROLES = Object.freeze(['chaff','close_defense','anti_medium','anti_heavy','endurance','demolition']);
const physicalGates = ['heavy_pair','demolition','shared_resources','close_safety'];
const epsilon = 1e-9;
const state = gate => typeof gate === 'string' ? gate : gate?.status;

export function repairMeasure(value) {
  if (!REPAIR_ROLES.every(role => value.dimensions?.[role]) || !physicalGates.every(gate => value.gates?.[gate])) return null;
  let deficit=0, surplus=0;
  const needs={},groups={routine:{},addition:{}};
  const passed=[];
  for (const role of REPAIR_ROLES) {
    const dimension=value.dimensions[role];
    // Unknown contributes only its known lower bound to search priority; it cannot pass.
    const score=Number.isFinite(dimension.score)?Math.max(0,dimension.score):0;
    const base=Number.isFinite(dimension.base_score)?dimension.base_score:score;
    const addition=Number.isFinite(dimension.auxiliary_bonus)?dimension.auxiliary_bonus:0;
    // Supply changes the final endurance floor, not an endlessly additive bonus.
    // Attribute only its actual adjustment to routine capability for this ranking.
    const supply=role==='endurance'&&dimension.supply_adjustment
      ?Math.max(0,score-(dimension.supply_adjustment.before_score??0)):0;
    groups.routine[role]=Math.max(0,base)+supply;
    groups.addition[role]=Math.max(0,addition);
    needs[role]=Math.max(0,60-score);
    deficit+=needs[role];
    surplus+=Math.min(REPAIR_POLICY.surplus_cap,Math.max(0,score-60));
    if(dimension.status==='PASS')passed.push('score:'+role);
  }
  let pending_physical=0;
  for(const gate of physicalGates) {
    if(state(value.gates[gate])==='PASS')passed.push('physical:'+gate);
    else {
      pending_physical++;
      for(const role of ({heavy_pair:['anti_heavy'],demolition:['demolition'],shared_resources:['chaff','anti_medium','endurance'],close_safety:['close_defense']}[gate]))needs[role]=Math.max(needs[role],30);
    }
  }
  return {deficit,surplus,utility:-deficit+REPAIR_POLICY.surplus_weight*surplus,pending_physical,passed,needs,groups};
}

// Compare the weakest routine and auxiliary dimensions separately. A 5-point
// auxiliary improvement is not competing directly with a 50-point gun upgrade.
// Only unresolved loadout needs guide repair: a zero in one layer is legitimate
// when the complete kit covers that role. Unimprovable zeros consume no trial.
export function bottleneckTrials(parent, candidates, needs=parent?.needs??{}) {
  if(!parent||!candidates.length)return {candidates:[],directions:[]};
  const ranked=[...candidates].sort((a,b)=>b.measure.utility-a.measure.utility);
  const safe=ranked.filter(c=>c.measure.utility>=parent.utility-epsilon);
  const selected=[],directions=[];
  for(const group of ['routine','addition']) {
    const roles=REPAIR_ROLES.filter(role=>needs[role]>0);
    const levels=[...new Set(roles.map(role=>parent.groups[group][role]))].sort((a,b)=>a-b);
    for(const level of levels) {
      const weakest=roles.filter(role=>parent.groups[group][role]===level);
      const improvements=safe.map(candidate=>({candidate,gain:weakest.reduce((sum,role)=>
        sum+candidate.measure.groups[group][role]-parent.groups[group][role],0)/weakest.length}))
        .filter(c=>c.gain>epsilon).sort((a,b)=>b.gain-a.gain||b.candidate.measure.utility-a.candidate.measure.utility);
      if(!improvements.length)continue;
      const best=improvements[0].candidate;
      directions.push({group,roles:weakest,before:level,gain:improvements[0].gain});
      if(!selected.includes(best))selected.push(best);
      break;
    }
  }
  // An unavailable group, or one candidate covering both directions, leaves room
  // for the best remaining whole-kit candidate. All still need full evaluation.
  for(const candidate of ranked)if(!selected.includes(candidate)&&selected.length<REPAIR_POLICY.full_checks_per_round)selected.push(candidate);
  return {candidates:selected.slice(0,REPAIR_POLICY.full_checks_per_round),directions};
}

export function contributionLoss(before, without) {
  if(!before||!without)return null;
  const remaining=new Set(without.passed);
  return {lost_checks:before.passed.filter(key=>!remaining.has(key)),utility_loss:before.utility-without.utility};
}

export function improvesRepair(before, after) {
  if(!before||!after)return false;
  // Intermediate scores may cross a threshold when the net deficit shrinks.
  // Physical failures may not increase; admission still uses every real gate.
  return after.pending_physical<before.pending_physical ||
    after.pending_physical===before.pending_physical && after.utility>before.utility+epsilon;
}

export function weakestContributions(rows) {
  const ranked=rows.filter(row=>row.loss).sort((a,b)=>a.loss.lost_checks.length-b.loss.lost_checks.length
    ||a.loss.utility_loss-b.loss.utility_loss);
  if(!ranked.length)return [];
  // Preserve essential roles when another slot can be changed without losing a pass.
  const leastCritical=ranked.filter(row=>row.loss.lost_checks.length===ranked[0].loss.lost_checks.length);
  const cutoff=leastCritical[Math.min(REPAIR_POLICY.weakest_choices,leastCritical.length)-1].loss.utility_loss;
  // Equal contributions remain equally selectable, independent of slot order.
  return leastCritical.filter(row=>row.loss.utility_loss<=cutoff+epsilon);
}

// Sampling hint only, never an admission score. Keep nonzero probability for every
// legal item. One of each slot's two candidates is completely uniform as well.
export function candidateWeight(row, measure) {
  if(!row||!measure)return 1;
  let useful=0,total=0;
  for(const [role,need] of Object.entries(measure.needs)) {
    const value=role==='anti_heavy'?row.heavy_assessment:role==='endurance'?row.endurance
      :role==='demolition'?row.demolition_assessment:row.dimensions?.[role];
    const score=role==='endurance'&&value?.state==='SUPPLY_PROVIDER'?100:value?.score;
    total+=need;useful+=need*Math.min(100,Math.max(0,Number.isFinite(score)?score:0))/100;
  }
  return 1+3*(total?useful/total:0);
}

export function preferredTrials(parent, trials) {
  const improved=trials.filter(t=>improvesRepair(parent,t.measure)).sort((a,b)=>
    a.measure.pending_physical-b.measure.pending_physical||b.measure.utility-a.measure.utility);
  if(!improved.length)return [];
  const candidates=improved.filter(t=>t.measure.pending_physical===improved[0].measure.pending_physical);
  const cutoff=candidates[Math.min(REPAIR_POLICY.retained_choices,candidates.length)-1].measure.utility;
  return candidates.filter(t=>t.measure.utility>=cutoff-epsilon);
}

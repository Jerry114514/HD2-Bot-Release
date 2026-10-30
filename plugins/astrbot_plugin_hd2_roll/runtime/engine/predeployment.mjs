// A bounded, explicit pre-fight setup plan. Receipts refer to actual compiled
// deploy actions from the seven slots; their costs/CD/lifetime start before t=0.
// No damage, supply credits or arbitrary initial flags are admitted as setup.
import {actionAllowedInCase} from './common.mjs';
export function predeploymentRows(spec, actions, profile) {
  const plan=spec.initial?.predeployment;
  if(plan===undefined)return [];
  if(profile.predeployment?.enabled!==true||!Array.isArray(plan))throw Error('PREDEPLOYMENT_NOT_ENABLED');
  const seen=new Set(),resolved=[];
  for(const request of plan){
    if(!request||typeof request.action_id!=='string'||!Number.isSafeInteger(request.started_before_ms)||request.started_before_ms<0)throw Error('INVALID_PREDEPLOYMENT_PLAN');
    const a=actions.find(a=>a.id===request.action_id),d=a?.deployment;
    if(!a||!d||!actionAllowedInCase(a,spec)||seen.has(d.equipment_id)||a.target||Object.keys(a.requires_slots||{}).length||Object.keys(a.requires_flags||{}).length)throw Error('PREDEPLOYMENT_ACTION_UNAVAILABLE');
    // The call-in must already be complete at t=0: a setup action still landing
    // when the engagement starts is not a prepared deployment.
    if(request.started_before_ms<a.busy_ms)throw Error('PREDEPLOYMENT_TIME_OVERLAP');
    seen.add(d.equipment_id);
    resolved.push({action:a,started_before_ms:request.started_before_ms});
  }
  // Overlap is a property of the timeline, not of the plan list order: two
  // non-overlapping deployments written newest-first are legal and must not be
  // refused. Sort by start time (earliest first) and require each earlier window
  // to have ended before the next one starts; rows come back chronological so
  // the seed applies costs/cooldowns/lifetimes in the real deployment order.
  resolved.sort((a,b)=>b.started_before_ms-a.started_before_ms);
  const rows=[];
  for(let i=0;i<resolved.length;i++){
    const cur=resolved[i];
    if(i>0){const prev=resolved[i-1];if(prev.started_before_ms-cur.started_before_ms<prev.action.busy_ms)throw Error('PREDEPLOYMENT_TIME_OVERLAP');}
    rows.push({action:cur.action,age_ms:cur.started_before_ms-cur.action.busy_ms,started_before_ms:cur.started_before_ms});
  }
  return rows;
}
export function predeploymentSeed(spec, rows) {
  if(!rows.length)return undefined;
  const resources=structuredClone(spec.initial.resources||{}),flags={},cooldowns={},flag_expiries={};
  for(const {action:a,age_ms,started_before_ms} of rows){
    const d=a.deployment;
    for(const [key,b] of Object.entries(a.requires_resources||{})){const v=resources[key]?.value;if(!Number.isFinite(v)||b.min!==undefined&&v<b.min||b.max!==undefined&&v>b.max)throw Error('PREDEPLOYMENT_RESOURCE_REQUIREMENT');}
    for(const e of a.events.filter(e=>e.kind==='resource')){
      const r=resources[e.key];
      if(!r||r.value+e.delta<0||d.resource_bindings?.[e.key]!==undefined&&r.resource_id!==d.resource_bindings[e.key])throw Error('PREDEPLOYMENT_RESOURCE_OVERSPEND');
      r.value+=e.delta;
    }
    if(a.cooldown)cooldowns[a.cooldown.key]=Math.max(0,a.cooldown.duration_ms-started_before_ms);
    if(d.lifetime_ms===null||age_ms<d.lifetime_ms){
      flags[d.flag]=true;
      if(d.lifetime_ms!==null)flag_expiries[d.flag]=d.lifetime_ms-age_ms;
    }
  }
  return {resources,flags,cooldowns,flag_expiries};
}

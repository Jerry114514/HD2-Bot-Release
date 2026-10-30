import {rescaleScore} from './score-scale.mjs';
import {isMeleeEquipment} from './score-exclusions.mjs';
export const SCORE_ROLES=['chaff','close_defense','anti_medium','anti_heavy','endurance','demolition'];
export const manualValue=(row,role)=>row.manual_calibration?.scores?.[role];
export function contributionKind(row,role,{automatic=false}={}) {
  if(['anti_heavy','demolition'].includes(role))return 'AUXILIARY_ADDITION';
  const mode=automatic?'auto':row.manual_calibration?.mode??'auto';
  if(mode!=='auto')return mode==='additive'?'AUXILIARY_ADDITION':'BASE_CAPABILITY';
  return row.strategic_profile&&['chaff','close_defense','anti_medium','anti_heavy'].includes(role)
    ||row.automatic_assessments?.some(a=>a.role===role)
    ||row.category==='throwables'&&role==='chaff'?'AUXILIARY_ADDITION':'BASE_CAPABILITY';
}
export function lockedScoreRoles(row) {
  const locks={};
  if(isMeleeEquipment(row))locks.endurance='近战武器续航固定为0，不参与整套续航计算';
  if(['orbital','eagle','deployable'].includes(row.category))locks.endurance='红绿战备续航固定为0';
  if(row.category==='deployable')locks.demolition='绿色战备拆迁固定为0';
  if(row.facts.known_noncombat||row.facts.supply_only||row.utility?.kind==='USER_SMOKE_ZERO'||row.strategic_profile?.realization_class==='smoke')
    for(const role of SCORE_ROLES.filter(r=>r!=='endurance'))locks[role]='已确认不计算此项战斗收益';
  return locks;
}
export function validateManualScores(doc,model) {
  if(doc?.schema!==1||!doc.overrides||typeof doc.overrides!=='object'||Array.isArray(doc.overrides)||!Array.isArray(doc.history))throw Error('人工评分文件格式不正确');
  for(const [id,entry] of Object.entries(doc.overrides)) {
    const row=model.equipment.find(r=>r.equipment_id===id);
    if(!row)throw Error('未知装备：'+id);
    if(!entry.scores||typeof entry.scores!=='object'||Array.isArray(entry.scores))throw Error('缺少装备评分');
    if(!['auto','base','additive'].includes(entry.mode??'auto'))throw Error('计分方式不正确');
    for(const [role,value] of Object.entries(entry.scores)) {
      if(!SCORE_ROLES.includes(role)||!Number.isFinite(value)||value<0||value>105)throw Error('评分须为0至105的数字');
      if(lockedScoreRoles(row)[role]&&value!==0)throw Error(lockedScoreRoles(row)[role]);
    }
    if(typeof entry.note!=='string'||entry.note.length>1000)throw Error('备注最多1000字');
  }
  return doc;
}
export function changeManualScores(doc,{equipment_id,scores,note='',mode},model,at=new Date().toISOString()) {
  validateManualScores(doc,model);
  const row=model.equipment.find(r=>r.equipment_id===equipment_id);
  if(!row)throw Error('未知装备');
  if(!scores||typeof scores!=='object'||Array.isArray(scores)||Object.keys(scores).some(k=>!SCORE_ROLES.includes(k)))throw Error('评分字段不正确');
  if(typeof note!=='string'||note.length>1000)throw Error('备注最多1000字');
  const next=structuredClone(doc),before=next.overrides[equipment_id]??null;
  mode??=before?.mode??'auto';
  if(!['auto','base','additive'].includes(mode))throw Error('计分方式不正确');
  const values=Object.fromEntries(Object.entries(scores).filter(([,v])=>v!==null));
  const after=Object.keys(values).length||mode!=='auto'?{scores:values,mode,note,updated_at:at,
    score_scale_id:model.scenario?.score_scale?.id??'original'}:null;
  if(after)next.overrides[equipment_id]=after;else delete next.overrides[equipment_id];
  next.history.push({at,equipment_id,before,after,score_scale_id:model.scenario?.score_scale?.id??'original',
    calculated_scores:row.calculated_scores?Object.fromEntries(Object.entries(row.calculated_scores)
      .map(([k,v])=>[k,rescaleScore(v,model.scenario?.score_scale)])):null});
  return validateManualScores(next,model);
}
export function applyManualScores(model,doc) {
  validateManualScores(doc,model);
  for(const row of model.equipment) {
    const entry=doc.overrides[row.equipment_id];
    if(!entry)continue;
    row.manual_calibration=structuredClone(entry);
    for(const [role,value] of Object.entries(entry.scores)) {
      const target=role==='anti_heavy'?row.heavy_assessment:role==='demolition'?row.demolition_assessment
        :role==='endurance'?row.endurance:row.dimensions[role];
      target.calculated_score=target.score;target.score=value;target.score_origin='MANUAL_CALIBRATED';
      if(row.dimensions[role]){target.calculated_state=target.state;target.state='MANUAL_CALIBRATED';}
    }
  }
  model.manual_calibration={schema:1,path:'shared/hd2-loadout/assessment-v2/manual-scores.json',
    edited_equipment:Object.keys(doc.overrides).length,history_entries:doc.history.length,
    scope:'SCORE_AND_CONTRIBUTION_MODE_ONLY; NO_DAMAGE_INVENTORY_LEGALITY_OR_PHYSICAL_GATE_OVERRIDE'};
}

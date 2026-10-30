// The public process boundary exposes equipment IDs only.
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {beginAssessmentDraw,stepAssessmentDraw} from './runtime/assessment-v2/draw.mjs';

try {
  const base=new URL('./runtime/',import.meta.url);
  const manifest=JSON.parse(readFileSync(new URL('snapshot.json',base),'utf8'));
  for(const [file,digest] of Object.entries(manifest.files)) {
    const bytes=readFileSync(new URL(file,base));
    if(createHash('sha256').update(bytes).digest('hex')!==digest)throw Error('snapshot mismatch');
  }
  const catalog=JSON.parse(readFileSync(new URL('data/catalog.json',base),'utf8'));
  const model=JSON.parse(readFileSync(new URL('assessment-v2/equipment.json',base),'utf8'));
  if(catalog.equipment.some(row=>!model.equipment.some(item=>item.equipment_id===row.id)))throw Error('incomplete snapshot');
  const mode=process.argv[2]??'assessment';
  if(process.argv.length>3)throw Error('unsupported arguments');
  if(!['assessment','pure'].includes(mode))throw Error('invalid mode');
  const task=beginAssessmentDraw(model,catalog,{mode});
  const deadline=Date.now()+15000;
  let value;
  do {
    value=stepAssessmentDraw(task);
    if(value.status!=='SEARCHING')break;
  } while(Date.now()<deadline);
  if(!['ASSESSMENT_PASS','UNVERIFIED'].includes(value.status))throw Error('draw unavailable');
  const {primary,secondary,grenade,stratagems}=value.loadout;
  process.stdout.write(JSON.stringify({primary,secondary,grenade,stratagems}));
} catch {
  process.stderr.write('DRAW_UNAVAILABLE');
  process.exitCode=1;
}

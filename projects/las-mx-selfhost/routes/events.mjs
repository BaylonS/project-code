import {json,signedIn,owner,db,day} from '../shared.mjs';
import resources from './resources.json' with {type:'json'};
export async function POST(r        ){
 if(!signedIn(r))return json({error:'Sign-in required'},401);
 const origin=r.headers.get('origin');if(!origin||origin!==new URL(r.url).origin)return json({error:'Invalid origin'},403);
 if(!r.headers.get('content-type')?.includes('application/json'))return json({error:'Expected JSON'},415);
 const raw=await r.text();if(raw.length>2048)return json({error:'Too large'},413);
 let body;try{body=JSON.parse(raw)}catch{return json({error:'Invalid JSON'},400)}
 if(!body||typeof body!=='object')return json({error:'Invalid event'},400);
 const {kind,area}=body;if(!['view','search','result_open'].includes(kind)||!['sections','resets','mel','cml','notes'].includes(area))return json({error:'Invalid event'},400);
 if(typeof body.label!=='string')return json({error:'Invalid label'},400);
 let label=body.label.trim().replace(/\s+/g,' ').slice(0,160);
 if(kind==='search'||kind==='result_open'){
  if(!['resets','mel','cml','notes'].includes(area)||label.length<2||!Number.isInteger(body.results)||body.results<0||body.results>100000)return json({error:'Invalid search'},400);
  // Do not store common contact details or URLs accidentally entered in search.
  if(/@|https?:\/\/|www\./i.test(label))return json({ignored:true});
  label=label.toUpperCase();
  if(kind==='result_open'&&(body.results<1||typeof body.resource!=='string'||!(resources                           )[area]?.includes(body.resource)))return json({error:'Invalid result'},400);
 }else if(!(resources                           )[area]?.includes(label))return json({error:'Unknown resource'},400);
 try{await db().prepare(`INSERT INTO ${owner(r)?'owner_activity_daily':'activity_daily'} (day,kind,area,label,count,misses) VALUES (?,?,?,?,1,?) ON CONFLICT(day,kind,area,label) DO UPDATE SET count=count+1,misses=misses+excluded.misses`).bind(day(),kind,area,label,kind==='search'&&body.results===0?1:0).run();return json({ok:true})}catch{return json({error:'Unable to record activity'},503)}
}

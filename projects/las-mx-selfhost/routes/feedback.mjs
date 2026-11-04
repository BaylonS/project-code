import {json,owner,signedIn,db} from '../shared.mjs';
const areas=['resets','mel','cml','notes','general'];
export async function GET(r        ){
 if(!owner(r))return json({error:'Owner access required'},403);
 try{const result=await db().prepare('SELECT * FROM feedback ORDER BY created DESC LIMIT 500').bind().all();return json({rows:result.results})}catch{return json({error:'Unable to load submissions'},503)}
}
async function body(r        ){
 if(!signedIn(r))throw new Error('Sign-in required');
 if(r.headers.get('origin')!==new URL(r.url).origin)throw new Error('Invalid origin');
 if(!r.headers.get('content-type')?.includes('application/json'))throw new Error('Expected JSON');
 const raw=await r.text();if(raw.length>6000)throw new Error('Submission too large');
 const value=JSON.parse(raw);if(!value||typeof value!=='object')throw new Error('Invalid submission');return value;
}
export async function POST(r        ){
 if(!signedIn(r))return json({error:'Sign-in required'},401);
 if(r.headers.get('origin')!==new URL(r.url).origin)return json({error:'Invalid origin'},403);
 let b;try{b=await body(r)}catch{return json({error:'Invalid submission or sign-in required'},400)}
 if(!['request','issue'].includes(b.kind)||!areas.includes(b.area)||typeof b.id!=='string'||!/^[a-f0-9-]{36}$/i.test(b.id)||typeof b.title!=='string'||b.title.trim().length<3||b.title.length>120||typeof b.detail!=='string'||b.detail.trim().length<5||b.detail.length>2000||typeof b.context!=='string'||b.context.length>500)return json({error:'Check the title and details lengths.'},400);
 try{await db().prepare('INSERT INTO feedback (id,kind,area,title,detail,context,status,created) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING').bind(b.id,b.kind,b.area,b.title.trim(),b.detail.trim(),b.context,'new',new Date().toISOString()).run();return json({ok:true})}catch{return json({error:'Could not save. Your text is still here; please retry.'},503)}
}
export async function PATCH(r        ){
 if(!owner(r))return json({error:'Owner access required'},403);
 let b;try{b=await body(r)}catch{return json({error:'Invalid request'},400)}
 if(typeof b.id!=='string'||!['new','reviewing','resolved'].includes(b.status))return json({error:'Invalid status'},400);
 try{const result=await db().prepare('UPDATE feedback SET status=? WHERE id=?').bind(b.status,b.id).run();return json({ok:true})}catch{return json({error:'Could not update status'},503)}
}

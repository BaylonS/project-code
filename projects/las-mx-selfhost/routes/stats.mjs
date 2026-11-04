import {json,owner,db,day} from '../shared.mjs';
export async function GET(r        ){
 if(!owner(r))return json({error:'Owner access required'},403);
 const days=Number(new URL(r.url).searchParams.get('days')||30);if(![7,30,90].includes(days))return json({error:'Invalid period'},400);
 const includeOwner=new URL(r.url).searchParams.get('includeOwner')!=='false';
 const end=day(),date=new Date(end+'T12:00:00Z');date.setUTCDate(date.getUTCDate()-(days-1));const start=date.toISOString().slice(0,10);
 try{const result=await db().prepare(`SELECT kind,area,label,SUM(count) AS count,SUM(misses) AS misses FROM (SELECT * FROM activity_daily ${includeOwner?'UNION ALL SELECT * FROM owner_activity_daily':''}) WHERE day>=? AND day<=? GROUP BY kind,area,label ORDER BY count DESC,label ASC`).bind(start,end).all();const daily=await db().prepare(`SELECT day,kind,area,SUM(count) AS count,SUM(misses) AS misses FROM (SELECT * FROM activity_daily ${includeOwner?'UNION ALL SELECT * FROM owner_activity_daily':''}) WHERE day>=? AND day<=? GROUP BY day,kind,area ORDER BY day`).bind(start,end).all();return json({daily:daily.results,start,end,days,includeOwner,timezone:'America/Los_Angeles',rows:result.results})}catch{return json({error:'Analytics storage is unavailable. Try again later.'},503)}
}

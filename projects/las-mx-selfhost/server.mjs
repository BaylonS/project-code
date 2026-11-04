import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {randomBytes,scryptSync,timingSafeEqual,createHash} from 'node:crypto';
import {pipeline} from 'node:stream/promises';
import {dataDir} from './shared.mjs';
import * as me from './routes/me.mjs';
import * as events from './routes/events.mjs';
import * as stats from './routes/stats.mjs';
import * as feedback from './routes/feedback.mjs';
const root=path.resolve('public'),ownerFile=path.join(dataDir,'owner.json'),setupFile=path.join(dataDir,'setup-key');
if(!fs.existsSync(ownerFile)&&!fs.existsSync(setupFile))fs.writeFileSync(setupFile,randomBytes(32).toString('hex'),{mode:0o600});
const sessions=new Map(),limits=new Map();
const routes={'/api/analytics/me':me,'/api/analytics/events':events,'/api/analytics/stats':stats,'/api/feedback':feedback};
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json','.svg':'image/svg+xml','.webp':'image/webp','.pdf':'application/pdf','.css':'text/css'};
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function equal(a,b){return timingSafeEqual(createHash('sha256').update(a).digest(),createHash('sha256').update(b).digest())}
function form(setup,key='',message=''){return `<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1"><title>MX · Admin</title><style>body{background:#10171c;color:#eef4f6;font:17px system-ui;max-width:420px;margin:12vh auto;padding:24px}input,button{box-sizing:border-box;width:100%;padding:14px;margin:9px 0;font:inherit;border-radius:8px;border:1px solid #44535b}button{background:#b8ef69;cursor:pointer}a{color:#b8ef69}</style><h1>${setup?'Set up Admin':'Admin login'}</h1><p>${setup?'Choose the password for your private Insights inbox.':'Sign in to view activity and crew requests.'}</p><p>${escape(message)}</p><form method="post" action="${setup?'/setup':'/owner'}"><input name="username" aria-label="Username" value="Admin" readonly><input type="password" name="password" aria-label="Password" autocomplete="${setup?'new-password':'current-password'}" required>${setup?'<input type="password" name="confirm" aria-label="Confirm password" placeholder="Confirm password" autocomplete="new-password" required><input type="hidden" name="key" value="'+escape(key)+'">':''}<button>${setup?'Create Admin account':'Sign in'}</button></form><p><a href="/">Back to reference library</a></p></html>`}
function send(res,status,body,type='text/html; charset=utf-8',extra={}){res.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store',...extra});res.end(body)}
function redirect(res,url,cookie){send(res,303,'','text/plain',{Location:url,...(cookie?{'Set-Cookie':cookie}:{})})}
const server=http.createServer(async(req,res)=>{try{
 res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','same-origin');res.setHeader('X-Frame-Options','DENY');
 const host=req.headers.host||'localhost';if(!/^[a-z0-9.:[\]-]+$/i.test(host))return send(res,400,'Bad host');
 const secure=req.headers['x-forwarded-proto']==='https';const origin=`${secure?'https':'http'}://${host}`,url=new URL(req.url,origin),p=url.pathname;
 const cookie=(req.headers.cookie||'').split(';').map(s=>s.trim()).find(s=>s.startsWith('lasmx_admin='))?.slice(12);
 const expiry=sessions.get(cookie);const isOwner=!!expiry&&expiry>Date.now();
 const cookieFlags=`; HttpOnly; SameSite=Strict; Path=/${secure?'; Secure':''}`;
 if(p==='/healthz')return send(res,200,'ok','text/plain');
 if(p==='/setup'&&req.method==='GET'){
  if(fs.existsSync(ownerFile)||!fs.existsSync(setupFile)||!equal(url.searchParams.get('key')||'',fs.readFileSync(setupFile,'utf8')))return send(res,404,'Not found');
  return send(res,200,form(true,url.searchParams.get('key')));
 }
 if(p==='/owner'&&req.method==='GET')return isOwner?redirect(res,'/analytics.html'):send(res,200,form(false));
 let body='';if(!['GET','HEAD'].includes(req.method)){for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>8192)return send(res,413,'Too large')}}
 if((p==='/owner'||p==='/setup')&&req.method==='POST'){
  if(req.headers.origin!==origin)return send(res,403,'Invalid origin');
  const ip=req.socket.remoteAddress,now=Date.now(),bucket=limits.get(ip)||{count:0,start:now};if(now-bucket.start>60000){bucket.count=0;bucket.start=now}limits.set(ip,bucket);if(++bucket.count>10)return send(res,429,'Please wait a minute before trying again.');
  const f=new URLSearchParams(body),password=f.get('password')||'';
  if(p==='/setup'){
   if(fs.existsSync(ownerFile)||!fs.existsSync(setupFile)||!equal(f.get('key')||'',fs.readFileSync(setupFile,'utf8')))return send(res,404,'Not found');
   if(password.length<8||password.length>256||password!==f.get('confirm'))return send(res,400,form(true,f.get('key'),'Use at least 8 characters and matching passwords.'));
   const salt=randomBytes(16).toString('hex');fs.writeFileSync(ownerFile,JSON.stringify({username:'Admin',salt,hash:scryptSync(password,salt,64).toString('hex')}),{flag:'wx',mode:0o600});fs.unlinkSync(setupFile);
  }else{
   if(!fs.existsSync(ownerFile))return send(res,403,form(false,'','Admin setup has not been completed.'));
   const user=JSON.parse(fs.readFileSync(ownerFile));if(f.get('username')!=='Admin'||password.length>256||!equal(scryptSync(password,user.salt,64).toString('hex'),user.hash))return send(res,401,form(false,'','Username or password is incorrect.'));
  }
  const token=randomBytes(32).toString('hex');sessions.set(token,now+8*3600000);return redirect(res,'/analytics.html',`lasmx_admin=${token}; Max-Age=28800${cookieFlags}`);
 }
 if(p==='/logout'&&req.method==='POST'){if(req.headers.origin!==origin)return send(res,403,'Invalid origin');sessions.delete(cookie);return redirect(res,'/',`lasmx_admin=; Max-Age=0${cookieFlags}`)}
 if(routes[p]){
  const fn=routes[p][req.method];if(!fn)return send(res,405,'Method not allowed');
  const headers=new Headers();for(const [k,v] of Object.entries(req.headers))if(v&&!k.startsWith('oai-')&&!k.startsWith('x-las-mx-'))headers.set(k,String(v));headers.set('x-las-mx-owner',isOwner?'yes':'no');
  const result=await fn(new Request(url,{method:req.method,headers,...(!['GET','HEAD'].includes(req.method)?{body}:{})}));res.writeHead(result.status,Object.fromEntries(result.headers));return res.end(Buffer.from(await result.arrayBuffer()));
 }
 if(!['GET','HEAD'].includes(req.method))return send(res,405,'Method not allowed');
 if(p==='/analytics.html'&&!isOwner)return redirect(res,'/owner');
 let decoded;try{decoded=decodeURIComponent(p)}catch{return send(res,400,'Bad path')}
 if(decoded.includes('\\')||decoded.includes('\0')||decoded.split('/').some(x=>x.startsWith('.')))return send(res,404,'Not found');
 const file=path.resolve(root,'.'+(decoded==='/'?'/index.html':decoded));if(!file.startsWith(root+path.sep))return send(res,404,'Not found');
 let stat;try{stat=fs.statSync(file)}catch{return send(res,404,'Not found')}if(!stat.isFile())return send(res,404,'Not found');
 res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Content-Length':stat.size,'Cache-Control':file.endsWith('.html')?'no-store':'public, max-age=3600'});if(req.method==='HEAD')return res.end();await pipeline(fs.createReadStream(file),res);
 }catch(e){console.error(e.message);if(!res.headersSent)send(res,500,'Server error');else res.destroy()}});
server.listen(Number(process.env.PORT||3000),process.env.BIND||'127.0.0.1',()=>console.log('MX listening'));
setInterval(()=>{const now=Date.now();for(const [k,v]of sessions)if(v<now)sessions.delete(k);for(const [k,v]of limits)if(now-v.start>120000)limits.delete(k)},60000).unref();

import {DatabaseSync} from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';
export const dataDir=process.env.DATA_DIR||path.resolve('data');
fs.mkdirSync(dataDir,{recursive:true,mode:0o700});
const sqlite=new DatabaseSync(path.join(dataDir,'las-mx.sqlite'));
sqlite.exec('PRAGMA journal_mode=WAL;');
sqlite.exec(fs.readFileSync(new URL('./schema.sql',import.meta.url),'utf8'));
export const json=(body,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store','Vary':'Cookie'}});
export function signedIn(){return true}
export function owner(r){return r.headers.get('x-las-mx-owner')==='yes'}
export function day(date=new Date()){return new Intl.DateTimeFormat('en-CA',{timeZone:'America/Los_Angeles',year:'numeric',month:'2-digit',day:'2-digit'}).format(date)}
export function db(){return {prepare(sql){return {bind(...args){return {async run(){return sqlite.prepare(sql).run(...args)},async all(){return {results:sqlite.prepare(sql).all(...args)}}}}}}}}

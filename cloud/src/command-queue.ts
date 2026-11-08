import {controlInput,DEVICE_ID} from './protocol';

export type DesiredField={on:boolean}|{brightness:number};
export type QueuedCommand={id:string;grant:string;fields:DesiredField;expires:number};
type Row={id:string;grant_id:string;fields:string;expires:number;phase:string;barrier:number};
type Admission='accepted'|'duplicate'|'conflict'|'busy'|'revoked';
async function hash(value:string){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),b=>b.toString(16).padStart(2,'0')).join('');}

/** Durable admission and last-value queue. A claimed command is never replayed. */
export class CommandQueue{
 constructor(private state:DurableObjectState){
  const sql=state.storage.sql;
  sql.exec('CREATE TABLE IF NOT EXISTS desired_jobs(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT,grant_id TEXT,fields TEXT,expires REAL,phase TEXT,barrier INTEGER)');
  sql.exec('CREATE TABLE IF NOT EXISTS desired_receipts(key TEXT PRIMARY KEY,fingerprint TEXT,expires REAL)');
  sql.exec('CREATE TABLE IF NOT EXISTS desired_health(id INTEGER PRIMARY KEY,outcome TEXT,at REAL)');
  sql.exec('CREATE TABLE IF NOT EXISTS desired_rate(id INTEGER PRIMARY KEY,started REAL,count INTEGER)');
 }
 private rows<T extends Record<string,SqlStorageValue>>(sql:string,...values:SqlStorageValue[]):T[]{return this.state.storage.sql.exec<T>(sql,...values).toArray();}
 status():{pending:number;running:boolean;last:{outcome:string;at:number}|null}{return {pending:this.rows('SELECT id FROM desired_jobs').length,running:this.rows("SELECT id FROM desired_jobs WHERE phase='running'").length>0,last:this.rows<{outcome:string;at:number}>('SELECT outcome,at FROM desired_health WHERE id=1')[0]??null};}
 clear(){this.state.storage.transactionSync(()=>{
  this.state.storage.sql.exec('DELETE FROM desired_jobs');
  this.state.storage.sql.exec('DELETE FROM desired_receipts');
  this.state.storage.sql.exec('DELETE FROM desired_rate');
 });}
 recover(now:number){
  if(this.rows("SELECT id FROM desired_jobs WHERE phase='running'").length){this.state.storage.sql.exec("DELETE FROM desired_jobs WHERE phase='running'");this.health('OUTCOME_UNKNOWN',now);}
 }
 private health(outcome:string,now:number){this.state.storage.sql.exec('INSERT OR REPLACE INTO desired_health VALUES(1,?,?)',outcome,now);}
 async accept(grant:string,request:string,fields:DesiredField[],now:number,current:()=>boolean=()=>true):Promise<Admission>{
  if(!request||request.length>128||!fields.length||fields.length>8)throw Error('invalid_request');
  for(const field of fields){if(Object.keys(field).length!==1)throw Error('invalid_request');controlInput({requestId:'00000000-0000-4000-8000-000000000001',deviceId:DEVICE_ID,operation:'set',...field});}
  const key=await hash(JSON.stringify([grant,request]));const fingerprint=await hash(JSON.stringify(fields));
  return this.state.storage.transactionSync(()=>{
   if(!current())return 'revoked';
   const sql=this.state.storage.sql;sql.exec('DELETE FROM desired_receipts WHERE expires<=?',now);
   const receipt=this.rows<{fingerprint:string}>('SELECT fingerprint FROM desired_receipts WHERE key=?',key)[0];
   if(receipt)return receipt.fingerprint===fingerprint?'duplicate':'conflict';
   const rate=this.rows<{started:number;count:number}>('SELECT started,count FROM desired_rate WHERE id=1')[0];
   if(rate&&now-rate.started<60&&rate.count>=120||this.rows('SELECT key FROM desired_receipts').length>=512)return 'busy';
   const rows=this.rows<Row>('SELECT * FROM desired_jobs ORDER BY seq');
   const last=rows.at(-1);const merge=fields.length===1&&Object.hasOwn(fields[0],'brightness')&&last?.phase==='waiting'&&last.barrier===0&&last.grant_id===grant&&last.expires>now&&Object.hasOwn(JSON.parse(last.fields),'brightness');
   if(rows.length+(merge?0:fields.length)>16)return 'busy';
   if(!rate||now-rate.started>=60)sql.exec('INSERT OR REPLACE INTO desired_rate VALUES(1,?,1)',now);else sql.exec('UPDATE desired_rate SET count=count+1 WHERE id=1');
   sql.exec('INSERT INTO desired_receipts VALUES(?,?,?)',key,fingerprint,now+1800);
   if(merge)sql.exec("UPDATE desired_jobs SET id=?,fields=?,expires=? WHERE id=?",crypto.randomUUID(),JSON.stringify(fields[0]),now+15,last.id);
   else for(const field of fields)sql.exec("INSERT INTO desired_jobs(id,grant_id,fields,expires,phase,barrier) VALUES(?,?,?,?,'waiting',?)",crypto.randomUUID(),grant,JSON.stringify(field),now+15,fields.length>1?1:0);
   return 'accepted';
  });
 }
 claim(grant:string|undefined,now:number):QueuedCommand|null{
  return this.state.storage.transactionSync(()=>{
   const sql=this.state.storage.sql;
   if(!grant){this.clear();return null;}
   sql.exec('DELETE FROM desired_jobs WHERE grant_id<>?',grant);
   if(this.rows("SELECT id FROM desired_jobs WHERE phase='running'").length)return null;
   for(const job of this.rows<Row>('SELECT * FROM desired_jobs ORDER BY seq')){
    if(job.expires<=now){sql.exec('DELETE FROM desired_jobs WHERE id=?',job.id);this.health('EXPIRED',now);continue;}
    sql.exec("UPDATE desired_jobs SET phase='running' WHERE id=?",job.id);
    return {id:job.id,grant:job.grant_id,fields:JSON.parse(job.fields),expires:job.expires};
   }
   return null;
  });
 }
 finish(id:string,outcome:string,now:number){this.state.storage.sql.exec('DELETE FROM desired_jobs WHERE id=?',id);this.health(outcome,now);}
}

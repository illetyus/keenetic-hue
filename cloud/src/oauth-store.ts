import {DurableObject} from 'cloudflare:workers';
import type {Env} from './index';
import {HomeGraph} from './homegraph';
import {DEVICE_ID,verifiedResult} from './protocol';
import {CommandQueue} from './command-queue';
import type {DesiredField} from './command-queue';
import {verifyTicket} from './oauth-ticket';
type Observation={online:boolean;on?:boolean;brightness?:number;observedAt:number};
type ReportRow={grant_id:string;payload:string;observed:number;attempts:number};

export type Identity={ownerId:string;grantId:string};
export type TokenResponse={token_type:'Bearer';access_token:string;expires_in:number;refresh_token?:string};
export type Outcome<T>={ok:true;value:T}|{ok:false;error:string};
async function outcome<T>(work:()=>Promise<T>):Promise<Outcome<T>>{try{return {ok:true,value:await work()};}catch(error){const message=error instanceof Error?error.message:'';if(['invalid_grant','invalid_request','temporarily_unavailable','unsupported_grant_type'].includes(message))return {ok:false,error:message};throw error;}}
type Flow={id:string;client:string;redirect:string;state:string;csrf:string;expires:number;attempts:number};
type LinkReason='cookie_missing'|'cookie_invalid'|'code_format'|'flow_missing'|'flow_expired'|'attempt_limit'|'csrf_mismatch'|'setup_missing'|'setup_expired'|'setup_mismatch'|'authorized';
const now=()=>Date.now()/1000;
export async function digest(value:string){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));return Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');}
export function randomToken(){return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
function setupCode(){const chars='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';const bytes=crypto.getRandomValues(new Uint8Array(26));return Array.from(bytes,b=>chars[b&31]).join('');}
function same(a:string,b:string){if(a.length!==b.length)return false;let diff=0;for(let i=0;i<a.length;i++)diff|=a.charCodeAt(i)^b.charCodeAt(i);return diff===0;}

export class AuthStore extends DurableObject<Env>{
  private commands:CommandQueue;
  private commandActive=false;
  private reporter?:HomeGraph;
  private reportTail:Promise<void>=Promise.resolve();
  private async reportExclusive<T>(work:()=>Promise<T>):Promise<T>{
    const previous=this.reportTail;let release!:()=>void;this.reportTail=new Promise<void>(resolve=>release=resolve);
    await previous;try{return await work();}finally{release();}
  }
  constructor(ctx:DurableObjectState,env:Env){
    super(ctx,env);
    this.commands=new CommandQueue(ctx);this.commands.recover(now());
    const sql=ctx.storage.sql;
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_owner(id INTEGER PRIMARY KEY, owner TEXT NOT NULL)');
    sql.exec('INSERT OR IGNORE INTO oauth_owner VALUES(1,?)',crypto.randomUUID());
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_setup(id INTEGER PRIMARY KEY, hash TEXT, expires REAL)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_link_status(id INTEGER PRIMARY KEY,reason TEXT,at REAL)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_google_status(intent TEXT PRIMARY KEY,diagnostic TEXT,at REAL)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_flows(id TEXT PRIMARY KEY,client TEXT,redirect TEXT,state TEXT,csrf TEXT,expires REAL,attempts INTEGER)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_flow_sources(id TEXT PRIMARY KEY,source TEXT,expires REAL)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_grants(id TEXT PRIMARY KEY,owner TEXT,active INTEGER)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_codes(hash TEXT PRIMARY KEY,grant_id TEXT,client TEXT,redirect TEXT,expires REAL)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_tokens(hash TEXT PRIMARY KEY,grant_id TEXT,client TEXT,kind TEXT,expires REAL)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_limits(name TEXT PRIMARY KEY,started REAL,count INTEGER)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_report(id INTEGER PRIMARY KEY,grant_id TEXT,payload TEXT,observed REAL,attempts INTEGER)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_report_seen(id INTEGER PRIMARY KEY,observed REAL)');
    sql.exec('CREATE TABLE IF NOT EXISTS oauth_report_health(id INTEGER PRIMARY KEY,outcome TEXT,at REAL,observed REAL)');
  }
  private rows<T extends Record<string,SqlStorageValue>>(sql:string,...args:SqlStorageValue[]):T[]{return this.ctx.storage.sql.exec<T>(sql,...args).toArray();}
  private clean(){const sql=this.ctx.storage.sql;sql.exec('DELETE FROM oauth_flows WHERE expires<=?',now());sql.exec('DELETE FROM oauth_codes WHERE expires<=?',now());sql.exec("DELETE FROM oauth_tokens WHERE kind='access' AND expires<=?",now());}
  private rate(name:string,limit=50){
    this.ctx.storage.sql.exec('DELETE FROM oauth_limits WHERE started<=?',now()-60);
    if(this.rows('SELECT name FROM oauth_limits').length>=256&&!this.rows('SELECT name FROM oauth_limits WHERE name=?',name).length)throw Error('temporarily_unavailable');
    const r=this.rows<{started:number;count:number}>('SELECT started,count FROM oauth_limits WHERE name=?',name)[0];
    if(!r||r.started+60<=now()){this.ctx.storage.sql.exec('INSERT OR REPLACE INTO oauth_limits VALUES(?,?,1)',name,now());return;}
    if(r.count>=limit)throw Error('temporarily_unavailable');
    this.ctx.storage.sql.exec('UPDATE oauth_limits SET count=count+1 WHERE name=?',name);
  }
  async issueSetup(){return outcome(()=>this.issueSetupValue());}
  async recordLinkStatus(reason:LinkReason){
    const allowed:LinkReason[]=['cookie_missing','cookie_invalid','code_format','flow_missing','flow_expired','attempt_limit','csrf_mismatch','setup_missing','setup_expired','setup_mismatch','authorized'];
    if(!allowed.includes(reason))throw Error('invalid_request');
    this.ctx.storage.sql.exec('INSERT OR REPLACE INTO oauth_link_status VALUES(1,?,?)',reason,now());
  }
  async linkStatus(){
    const last=this.rows<{reason:string;at:number}>('SELECT reason,at FROM oauth_link_status WHERE id=1')[0];
    if(!last)return null;
    const count=(sql:string)=>Number(this.rows<{n:number}>(sql)[0].n);
    return {...last,state:{setupPresent:count('SELECT COUNT(*) AS n FROM oauth_setup')>0,pendingCodes:count('SELECT COUNT(*) AS n FROM oauth_codes'),activeGrants:count('SELECT COUNT(*) AS n FROM oauth_grants WHERE active=1'),accessTokens:count("SELECT COUNT(*) AS n FROM oauth_tokens WHERE kind='access'"),refreshTokens:count("SELECT COUNT(*) AS n FROM oauth_tokens WHERE kind='refresh'")}};
  }
  async recordGoogleStatus(value:{intent:string;stage:string;fields:string[];outcome:string}){
    if(!['SYNC','QUERY','EXECUTE','DISCONNECT','UNKNOWN'].includes(value.intent)||!/^[a-z_]{1,32}$/.test(value.stage)||!/^[A-Za-z_]{1,64}$/.test(value.outcome))return;
    const fields=value.fields.filter(x=>/^[A-Za-z_]{1,64}$/.test(x)).slice(0,20);
    this.ctx.storage.sql.exec('INSERT OR REPLACE INTO oauth_google_status VALUES(?,?,?)',value.intent,JSON.stringify({...value,fields}),now());
  }
  async googleStatus():Promise<Array<{intent:string;stage:string;fields:string[];outcome:string;at:number}>>{return this.rows<{diagnostic:string;at:number}>('SELECT diagnostic,at FROM oauth_google_status ORDER BY at DESC').map(r=>({...JSON.parse(r.diagnostic),at:r.at}));}
  async createFlow(input:{clientId:string;redirectUri:string;state:string;csrf:string;source?:string}){return outcome(()=>this.createFlowValue(input));}
  async consent(input:{flowId:string;csrf:string;setupCode:string;source?:string}){return outcome(()=>this.consentValue(input));}
  async exchange(form:Record<string,string>){return outcome(()=>this.exchangeValue(form));}
  async cancelFlow(input:{flowId:string;csrf:string}){return outcome(async()=>{
    const csrf=await digest(input.csrf);
    const flow=this.rows<Flow>('SELECT * FROM oauth_flows WHERE id=?',input.flowId)[0];
    if(!flow||flow.expires<=now()||!same(flow.csrf,csrf))throw Error('invalid_grant');
    this.ctx.storage.sql.exec('DELETE FROM oauth_flows WHERE id=?',input.flowId);
    return {redirectUri:flow.redirect,state:flow.state};
  });}
  private async issueSetupValue():Promise<{code:string;expiresIn:number}>{
    const code=setupCode(),hash=await digest(code);this.rate('issue',10);
    this.ctx.storage.sql.exec('INSERT OR REPLACE INTO oauth_setup VALUES(1,?,?)',hash,now()+600);
    return {code,expiresIn:600};
  }
  private async createFlowValue(input:{clientId:string;redirectUri:string;state:string;csrf:string;source?:string}):Promise<{flowId:string}>{
    if(!input.clientId||input.clientId.length>256||!input.redirectUri||input.redirectUri.length>2048||!input.state||input.state.length>1024||input.csrf.length>128)throw Error('invalid_request');
    const csrf=await digest(input.csrf);this.clean();
    const source=input.source??'internal';
    if(source!=='internal'&&!/^[a-f0-9]{64}$/.test(source))throw Error('invalid_request');
    this.ctx.storage.sql.exec('DELETE FROM oauth_flow_sources WHERE expires<=? OR id NOT IN (SELECT id FROM oauth_flows)',now());
    if(this.rows('SELECT id FROM oauth_flow_sources WHERE source=?',source).length>=4)throw Error('temporarily_unavailable');
    this.rate('flow-source:'+source,10);this.rate('flows');
    if(this.rows('SELECT id FROM oauth_flows').length>=128)throw Error('temporarily_unavailable');
    const flowId=randomToken();this.ctx.storage.sql.exec('INSERT INTO oauth_flows VALUES(?,?,?,?,?,?,0)',flowId,input.clientId,input.redirectUri,input.state,csrf,now()+600);
    this.ctx.storage.sql.exec('INSERT INTO oauth_flow_sources VALUES(?,?,?)',flowId,source,now()+600);
    return {flowId};
  }
  private async consentValue(input:{flowId:string;csrf:string;setupCode:string;source?:string}):Promise<{code:string;redirectUri:string;state:string}>{
    const ticket=input.flowId.includes('.')?await verifyTicket(this.env,input.csrf,input.flowId):null;
    const code=randomToken(),codeHash=await digest(code),csrf=await digest(input.csrf),setup=await digest(input.setupCode);
    let reason:LinkReason='flow_missing';
    const prepared=this.ctx.storage.transactionSync(()=>{
      const legacy=ticket?undefined:this.rows<Flow>('SELECT * FROM oauth_flows WHERE id=?',input.flowId)[0];
      const flow=ticket??legacy;
      if(!flow)return null;
      if(flow.expires<=now()){reason='flow_expired';return null;}
      if(legacy&&legacy.attempts>=5){reason='attempt_limit';return null;}
      if(legacy&&!same(legacy.csrf,csrf)){reason='csrf_mismatch';return null;}
      if(legacy){
        const source=input.source??this.rows<{source:string}>('SELECT source FROM oauth_flow_sources WHERE id=?',legacy.id)[0]?.source??'internal';
        if(source!=='internal'&&!/^[a-f0-9]{64}$/.test(source))throw Error('invalid_request');
        this.rate('consent-source:'+source,10);
        this.ctx.storage.sql.exec('UPDATE oauth_flows SET attempts=attempts+1 WHERE id=?',legacy.id);
      }
      // Signed forms use a 130-bit setup code. Failed guesses must not reserve
      // a shared rate-limit slot that could crowd out a legitimate submission.
      const current=this.rows<{hash:string;expires:number}>('SELECT hash,expires FROM oauth_setup WHERE id=1')[0];
      if(!current){reason='setup_missing';return null;}
      if(current.expires<=now()){reason='setup_expired';return null;}
      if(!same(current.hash,setup)){reason='setup_mismatch';return null;}
      this.rate('consent');
      const owner=this.rows<{owner:string}>('SELECT owner FROM oauth_owner WHERE id=1')[0].owner;
      const retiredGrantIds=this.rows<{id:string}>('SELECT id FROM oauth_grants ORDER BY id').map(row=>row.id);
      // Consume this setup flow first, but keep the current OAuth grant until Relay has
      // fenced all of its outstanding work. A failed fence cannot silently unlink it.
      this.commands.clear();this.ctx.storage.sql.exec('DELETE FROM oauth_setup');
      if(legacy)this.ctx.storage.sql.exec('DELETE FROM oauth_flows WHERE id=?',legacy.id);
      return {owner,client:flow.client,redirect:flow.redirect,state:flow.state,retiredGrantIds};
    });
    if(!prepared){await this.recordLinkStatus(reason);throw Error('invalid_grant');}
    try{
      await Promise.all(prepared.retiredGrantIds.map(grantId=>this.env.HUE_RELAY.getByName(DEVICE_ID).revokeOAuthGrant(grantId)));
    }catch{throw Error('temporarily_unavailable');}
    const result=this.ctx.storage.transactionSync(()=>{
      // Another authorized consent may have completed while Relay was fencing the
      // snapshot. Never delete a grant that this attempt did not fence.
      const currentGrantIds=this.rows<{id:string}>('SELECT id FROM oauth_grants ORDER BY id').map(row=>row.id);
      if(currentGrantIds.length!==prepared.retiredGrantIds.length||currentGrantIds.some((id,index)=>id!==prepared.retiredGrantIds[index]))throw Error('temporarily_unavailable');
      const sql=this.ctx.storage.sql;sql.exec('DELETE FROM oauth_tokens');sql.exec('DELETE FROM oauth_codes');sql.exec('DELETE FROM oauth_grants');
      const grant=crypto.randomUUID();sql.exec('INSERT INTO oauth_grants VALUES(?,?,1)',grant,prepared.owner);
      sql.exec('INSERT INTO oauth_codes VALUES(?,?,?,?,?)',codeHash,grant,prepared.client,prepared.redirect,now()+60);
      return {code,redirectUri:prepared.redirect,state:prepared.state};
    });
    await this.recordLinkStatus('authorized');
    return result;
  }
  private async exchangeValue(form:Record<string,string>):Promise<TokenResponse>{
    const access=randomToken(),accessHash=await digest(access),refresh=randomToken(),refreshHash=await digest(refresh);
    const key=await digest(form.grant_type==='authorization_code'?(form.code??''):(form.refresh_token??''));
    this.clean();this.rate('exchange');
    return this.ctx.storage.transactionSync(()=>{
      const sql=this.ctx.storage.sql;
      let grantId:string;let fresh=false;
      if(form.grant_type==='authorization_code'){
        const row=this.rows<{grant_id:string;client:string;redirect:string;expires:number}>('SELECT * FROM oauth_codes WHERE hash=?',key)[0];
        if(!row||row.expires<=now()||row.client!==form.client_id||row.redirect!==form.redirect_uri)throw Error('invalid_grant');
        grantId=row.grant_id;fresh=true;
      }else if(form.grant_type==='refresh_token'){
        const row=this.rows<{grant_id:string;client:string}>("SELECT * FROM oauth_tokens WHERE hash=? AND kind='refresh'",key)[0];
        if(!row||row.client!==form.client_id)throw Error('invalid_grant');grantId=row.grant_id;
      }else throw Error('unsupported_grant_type');
      if(!this.rows('SELECT id FROM oauth_grants WHERE id=? AND active=1',grantId).length)throw Error('invalid_grant');
      if(this.rows("SELECT hash FROM oauth_tokens WHERE kind='access'").length>=256)throw Error('temporarily_unavailable');
      if(fresh){sql.exec('DELETE FROM oauth_codes WHERE hash=?',key);sql.exec("INSERT INTO oauth_tokens VALUES(?,?,?,'refresh',NULL)",refreshHash,grantId,form.client_id);}
      sql.exec("INSERT INTO oauth_tokens VALUES(?,?,?,'access',?)",accessHash,grantId,form.client_id,now()+3600);
      return {token_type:'Bearer' as const,access_token:access,expires_in:3600,...(fresh?{refresh_token:refresh}:{})};
    });
  }
  async authenticate(token:string):Promise<Identity|null>{
    if(typeof token!=='string'||token.length!==43)return null;
    const hash=await digest(token);
    const row=this.rows<{owner:string;grant_id:string}>("SELECT g.owner,t.grant_id FROM oauth_tokens t JOIN oauth_grants g ON g.id=t.grant_id WHERE t.hash=? AND t.kind='access' AND t.expires>? AND g.active=1",hash,now())[0];
    return row?{ownerId:row.owner,grantId:row.grant_id}:null;
  }
  async disconnect(grantId:string):Promise<void>{
    // Fence this grant in Relay before acknowledging unlink. New calls fail closed;
    // work already submitted to the router is allowed to finish before the fence returns.
    await this.env.HUE_RELAY.getByName(DEVICE_ID).revokeOAuthGrant(grantId);
    this.ctx.storage.transactionSync(()=>{if(this.currentGrant()?.id===grantId)this.commands.clear();this.ctx.storage.sql.exec('UPDATE oauth_grants SET active=0 WHERE id=?',grantId);this.ctx.storage.sql.exec('DELETE FROM oauth_tokens WHERE grant_id=?',grantId);this.ctx.storage.sql.exec('DELETE FROM oauth_codes WHERE grant_id=?',grantId);this.ctx.storage.sql.exec('DELETE FROM oauth_report WHERE grant_id=?',grantId);});
  }
  private currentGrant(){return this.rows<{id:string;owner:string}>("SELECT DISTINCT g.id,g.owner FROM oauth_grants g JOIN oauth_tokens t ON t.grant_id=g.id WHERE g.active=1 AND t.kind='refresh'")[0];}
  private reportsEnabled(){return this.env.REPORT_STATE_ENABLED==='true'&&Boolean(this.env.HOMEGRAPH_SERVICE_ACCOUNT);}
  private graph(){return this.reporter??=new HomeGraph(this.env.HOMEGRAPH_SERVICE_ACCOUNT!);}
  async queueReport(grantId:string,value:Observation):Promise<{queued:boolean}>{
    return this.enqueueReport(grantId,value);
  }
  private async enqueueReport(grantId:string,value:Observation,force=false,schedule=true):Promise<{queued:boolean}>{
    if(!force&&!this.reportsEnabled())return {queued:false};
    const grant=this.currentGrant();if(!grant||grant.id!==grantId)return {queued:false};
    if(typeof value.online!=='boolean'||!Number.isFinite(value.observedAt)||Math.abs(now()-value.observedAt)>10)return {queued:false};
    if(value.online&&(typeof value.on!=='boolean'||!Number.isInteger(value.brightness)||value.brightness!<0||value.brightness!>100||!value.on&&value.brightness!==0))return {queued:false};
    const seen=this.rows<{observed:number}>('SELECT observed FROM oauth_report_seen WHERE id=1')[0];if(seen&&seen.observed>=value.observedAt)return {queued:false};
    const payload=value.online?{online:true,on:value.on,brightness:value.brightness}:{online:false};
    this.ctx.storage.transactionSync(()=>{
      this.ctx.storage.sql.exec('INSERT OR REPLACE INTO oauth_report_seen VALUES(1,?)',value.observedAt);
      this.ctx.storage.sql.exec('INSERT OR REPLACE INTO oauth_report VALUES(1,?,?,?,0)',grantId,JSON.stringify(payload),value.observedAt);
    });
    if(schedule)await this.scheduleSoon(1000);return {queued:true};
  }
  async alarm(){
    await this.drainCommand();
    if(this.commands.status().pending){await this.scheduleSoon(100);return;}
    await this.reportExclusive(()=>this.sendPendingReport());
    if(this.commands.status().pending)await this.scheduleSoon(100);
  }
  private async scheduleSoon(delay:number){const due=Date.now()+delay;const alarm=await this.ctx.storage.getAlarm();if(alarm===null||alarm>due)await this.ctx.storage.setAlarm(due);}
  async submitCommands(grantId:string,requestId:string,fields:DesiredField[]):Promise<{accepted:boolean;errorCode?:string}>{
    if(this.env.GOOGLE_QUEUE_ENABLED!=='true'||!this.reportsEnabled())return {accepted:false,errorCode:'QUEUE_DISABLED'};
    const valid=()=>this.currentGrant()?.id===grantId;
    if(!valid())return {accepted:false,errorCode:'AGENT_REVOKED'};
    const admitted=await this.commands.accept(grantId,requestId,fields,now(),valid);
    if(admitted!=='accepted'&&admitted!=='duplicate')return {accepted:false,errorCode:admitted==='revoked'?'AGENT_REVOKED':admitted==='conflict'?'COMMAND_ID_CONFLICT':'BUSY'};
    await this.scheduleSoon(100);return {accepted:true};
  }
  async commandStatus(){return {enabled:this.env.GOOGLE_QUEUE_ENABLED==='true',...this.commands.status()};}
  private async drainCommand(){
    if(this.commandActive)return;
    this.commandActive=true;
    try{
      const job=this.commands.claim(this.currentGrant()?.id,now());if(!job)return;
      if(this.env.GOOGLE_QUEUE_ENABLED!=='true'||job.expires-now()<7||this.currentGrant()?.id!==job.grant){this.commands.finish(job.id,'EXPIRED',now());return;}
      let outcome='OUTCOME_UNKNOWN';
      try{
        const response=await this.env.HUE_RELAY.getByName(DEVICE_ID).execute({requestId:job.id,deviceId:DEVICE_ID,operation:'set',...job.fields},{kind:'google',grantId:job.grant});
        if(response.status===200){
          const result=verifiedResult(await response.json(),job.id);outcome=result.errorCode??result.status;
          if(result.status==='verified')await this.enqueueReport(job.grant,{online:true,on:result.on,brightness:result.brightness,observedAt:result.observedAt!},false,false);
          else if(result.status==='offline')await this.enqueueReport(job.grant,{online:false,observedAt:now()},false,false);
        }
      }catch{/* Claimed operations are never replayed after an uncertain delivery. */}
      this.commands.finish(job.id,outcome,now());
    }finally{this.commandActive=false;}
  }
  private async sendPendingReport(force=false){
    const pending=this.rows<ReportRow>('SELECT * FROM oauth_report WHERE id=1')[0];if(!pending)return;
    const grant=this.currentGrant();if(!force&&!this.reportsEnabled()||!grant||grant.id!==pending.grant_id){this.ctx.storage.sql.exec('DELETE FROM oauth_report WHERE id=1 AND observed=?',pending.observed);return;}
    try{
      await this.graph().report(grant.owner,JSON.parse(pending.payload));
      this.reportHealth('accepted',pending.observed);
      this.ctx.storage.sql.exec('DELETE FROM oauth_report WHERE id=1 AND observed=?',pending.observed);
    }catch{
      this.reportHealth('failed',pending.observed);
      this.ctx.storage.sql.exec('UPDATE oauth_report SET attempts=attempts+1 WHERE id=1 AND observed=?',pending.observed);
      this.ctx.storage.sql.exec('DELETE FROM oauth_report WHERE attempts>=3');
    }
    const next=this.rows<ReportRow>('SELECT * FROM oauth_report WHERE id=1')[0];if(next)await this.scheduleSoon(next.observed>pending.observed?1000:5000*2**next.attempts);
  }
  async pollReport(){
    const grant=this.currentGrant();if(!this.reportsEnabled()||!grant||this.commands.status().pending)return;
    try{
      const requestId=crypto.randomUUID();
      const response=await this.env.HUE_RELAY.getByName(DEVICE_ID).execute({requestId,deviceId:DEVICE_ID,operation:'query'},{kind:'google',grantId:grant.id});
      if(response.status!==200)return;
      const result=verifiedResult(await response.json(),requestId);
      if(this.commands.status().pending)return;
      if(result.status==='verified')await this.queueReport(grant.id,{online:true,on:result.on,brightness:result.brightness,observedAt:result.observedAt!});
      else if(result.status==='offline')await this.queueReport(grant.id,{online:false,observedAt:now()});
    }catch{/* No guessed physical state. The next scheduled observation retries. */}
  }
  private reportHealth(outcome:string,observed:number){this.ctx.storage.sql.exec('INSERT OR REPLACE INTO oauth_report_health VALUES(1,?,?,?)',outcome,now(),observed);}
  async reportStatus(){return {enabled:this.reportsEnabled(),linked:Boolean(this.currentGrant()),pending:this.rows('SELECT id FROM oauth_report').length,last:this.rows<{outcome:string;at:number;observed:number}>('SELECT outcome,at,observed FROM oauth_report_health WHERE id=1')[0]??null};}
  async checkReporting():Promise<{reported:boolean;matched:boolean;state?:{online:boolean;on?:boolean;brightness?:number};observedAt?:number;errorCode?:string}>{
    return this.reportExclusive(()=>this.checkReportingValue());
  }
  private async checkReportingValue():Promise<{reported:boolean;matched:boolean;state?:{online:boolean;on?:boolean;brightness?:number};observedAt?:number;errorCode?:string}>{
    const grant=this.currentGrant();if(!grant||!this.env.HOMEGRAPH_SERVICE_ACCOUNT)return {reported:false,matched:false,errorCode:'NOT_CONFIGURED_OR_LINKED'};
    let reported=false;
    try{
      const requestId=crypto.randomUUID();const response=await this.env.HUE_RELAY.getByName(DEVICE_ID).execute({requestId,deviceId:DEVICE_ID,operation:'query'},{kind:'google',grantId:grant.id});
      if(response.status!==200)throw Error('DEVICE_UNAVAILABLE');
      const value=verifiedResult(await response.json(),requestId);if(value.status!=='verified')throw Error('DEVICE_UNAVAILABLE');
      if(this.currentGrant()?.id!==grant.id)throw Error('GRANT_CHANGED');
      const state={online:true,on:value.on!,brightness:value.brightness!};
      if(!(await this.enqueueReport(grant.id,{...state,observedAt:value.observedAt!},true,false)).queued)throw Error('OBSERVATION_SUPERSEDED');
      await this.sendPendingReport(true);
      const health=(await this.reportStatus()).last;
      reported=health?.outcome==='accepted'&&health.observed===value.observedAt;
      if(!reported)throw Error('HOMEGRAPH_CHECK_FAILED');
      const remote=await this.graph().query(grant.owner);
      return {reported,matched:remote.online===state.online&&remote.on===state.on&&remote.brightness===state.brightness,state,observedAt:value.observedAt};
    }catch(error){const code=error instanceof Error?error.message:'';return {reported,matched:false,errorCode:/^(HOMEGRAPH_REQUEST_[0-9]{3}|HOMEGRAPH_INVALID_STATE|DEVICE_UNAVAILABLE|GRANT_CHANGED|OBSERVATION_SUPERSEDED)$/.test(code)?code:'HOMEGRAPH_CHECK_FAILED'};}
  }
  async checkHomeGraph(){if(!this.env.HOMEGRAPH_SERVICE_ACCOUNT)return {authenticated:false,configured:false};try{return {authenticated:await this.graph().checkAuthentication(),configured:true};}catch(error){const message=error instanceof Error?error.message:'';const known=['Invalid HomeGraph credential','Invalid HomeGraph token','HomeGraph token unavailable'];return {authenticated:false,configured:true,errorCode:/^HOMEGRAPH_TOKEN_[0-9]{3}_[a-z_]+$/.test(message)?message:known.includes(message)?message.replaceAll(' ','_').toUpperCase():error instanceof Error&&/^[A-Za-z]{1,40}$/.test(error.name)?'HOMEGRAPH_'+error.name:'HOMEGRAPH_CONNECTION_OR_KEY_ERROR'};}}
  async requestSync(){const grant=this.currentGrant();if(!this.reportsEnabled()||!grant)return false;try{await this.graph().requestSync(grant.owner);return true;}catch{return false;}}
}

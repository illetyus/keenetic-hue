import { DurableObject } from 'cloudflare:workers';
import type { Env } from './index';
import { ControlInput, Result, DEVICE_ID, logicalKey, object, verifiedResult, wireCommand } from './protocol';

type Attachment={sessionId:string;helloAt:number;sequence:number;ready:boolean;keyVersion:number;keyFingerprint:string;agentSessionId?:string};
type Row={id:string;fingerprint:string;command:string;result:string|null;created:number;session:string};
type Pending={resolve:(r:Result)=>void;timer:ReturnType<typeof setTimeout>;sessionId:string};
type CommandAuthorization={kind:'google';grantId:string}|{kind:'operator'};
const unknown=(id:string):Result=>({commandId:id,status:'error',errorCode:'OUTCOME_UNKNOWN'});

export class Relay extends DurableObject<Env>{
  pending=new Map<string,Pending>();
  tunnelActive=0;
  private tunnelTail:Promise<void>=Promise.resolve();
  private grantOperations=new Map<string,Set<Promise<void>>>();
  keyFingerprint:Promise<string>;
  constructor(ctx:DurableObjectState,env:Env){
    super(ctx,env);
    this.keyFingerprint=crypto.subtle.digest('SHA-256',new TextEncoder().encode(env.DEVICE_TOKEN)).then(buffer=>Array.from(new Uint8Array(buffer),b=>b.toString(16).padStart(2,'0')).join(''));
    ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, fingerprint TEXT, command TEXT, result TEXT, created REAL, session TEXT)');
    ctx.storage.sql.exec('CREATE INDEX IF NOT EXISTS commands_created ON commands(created)');
    ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS revoked_oauth_grants (grant_id TEXT PRIMARY KEY, revoked_at REAL NOT NULL)');
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair('ping','pong'));
  }
  private rows(query:string,...args:any[]):Row[]{return this.ctx.storage.sql.exec<Row>(query,...args).toArray();}
  private oauthGrantRevoked(grantId:string):boolean{
    return this.ctx.storage.sql.exec<{grant_id:string}>('SELECT grant_id FROM revoked_oauth_grants WHERE grant_id=? LIMIT 1',grantId).toArray().length>0;
  }
  private beginOAuthGrantOperation(grantId:string):(()=>void)|null{
    if(this.oauthGrantRevoked(grantId))return null;
    let release!:()=>void;
    const operation=new Promise<void>(resolve=>release=resolve);
    const active=this.grantOperations.get(grantId)??new Set<Promise<void>>();
    active.add(operation);this.grantOperations.set(grantId,active);
    return ()=>{
      const current=this.grantOperations.get(grantId);
      current?.delete(operation);
      if(current&&!current.size)this.grantOperations.delete(grantId);
      release();
    };
  }
  private async waitForOAuthGrantOperations(grantId:string):Promise<void>{
    await Promise.all([...(this.grantOperations.get(grantId)??[])]);
  }
  async revokeOAuthGrant(grantId:string):Promise<void>{
    if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(grantId))throw Error('invalid_grant');
    const revokedAt=Date.now()/1000;
    this.ctx.storage.transactionSync(()=>{
      this.ctx.storage.sql.exec('INSERT OR IGNORE INTO revoked_oauth_grants VALUES(?,?)',grantId,revokedAt);
    });
    // The durable fence is installed before waiting. Calls that arrive later are rejected;
    // operations already inside Relay must finish before unlink/relink can acknowledge.
    await this.waitForOAuthGrantOperations(grantId);
  }
  private cleanQueries(){
    this.ctx.storage.sql.exec("DELETE FROM commands WHERE json_extract(command,'$.operation')='query' AND result IS NOT NULL AND created<=?",Date.now()/1000-30);
    this.ctx.storage.sql.exec("DELETE FROM commands WHERE id IN (SELECT id FROM commands WHERE json_extract(command,'$.operation')='query' AND result IS NOT NULL ORDER BY created DESC LIMIT -1 OFFSET 63)");
  }
  private full(input:ControlInput){
    return this.rows(input.operation==='query'?"SELECT id FROM commands WHERE json_extract(command,'$.operation')='query'":"SELECT id FROM commands WHERE COALESCE(json_extract(command,'$.operation'),'set')<>'query'").length>=(input.operation==='query'?64:256);
  }
  private finish(id:string,result:Result){
    this.ctx.storage.sql.exec('UPDATE commands SET result=? WHERE id=? AND result IS NULL',JSON.stringify(result),id);
    const p=this.pending.get(id);if(p){clearTimeout(p.timer);this.pending.delete(id);p.resolve(result);}
  }
  private finishSession(sessionId:string){for(const row of this.rows('SELECT * FROM commands WHERE session=? AND result IS NULL',sessionId))this.finish(row.id,unknown(row.id));}
  private async currentSockets():Promise<WebSocket[]>{
    const fingerprint=await this.keyFingerprint;
    return this.ctx.getWebSockets().filter(ws=>{
      const a=ws.deserializeAttachment() as Attachment;
      if(a.keyFingerprint===fingerprint)return true;
      a.ready=false;ws.serializeAttachment(a);this.finishSession(a.sessionId);ws.close(4003,'Credential changed');return false;
    });
  }
  async fetch(_request:Request):Promise<Response>{
    return this.ctx.blockConcurrencyWhile(async()=>{
    const fingerprint=await this.keyFingerprint;
    if(await this.ctx.storage.get<boolean>('revoked')){
      const revokedFingerprint=await this.ctx.storage.get<string>('revokedFingerprint');
      if(!revokedFingerprint||revokedFingerprint===fingerprint)return new Response(null,{status:401});
      // A separately authenticated newly deployed device key may re-enrol; the old key stays unusable.
      await this.ctx.storage.put('revoked',false);
    }
    const keyVersion=await this.ctx.storage.get<number>('keyVersion')??0;
    for(const ws of this.ctx.getWebSockets()){
      const a=ws.deserializeAttachment() as Attachment;a.ready=false;ws.serializeAttachment(a);this.finishSession(a.sessionId);ws.close(4001,'Replaced');
    }
    const pair=new WebSocketPair();const [client,server]=Object.values(pair);
    const a:Attachment={sessionId:crypto.randomUUID(),helloAt:Date.now()/1000,sequence:0,ready:false,keyVersion,keyFingerprint:fingerprint};
    this.ctx.acceptWebSocket(server);server.serializeAttachment(a);
    server.send(JSON.stringify({v:1,type:'hello',sessionId:a.sessionId,serverTime:a.helloAt}));
    return new Response(null,{status:101,webSocket:client});
    });
  }
  async execute(input:ControlInput,authorization:CommandAuthorization):Promise<Response>{
    if(!authorization||(authorization.kind!=='google'&&authorization.kind!=='operator'))return Response.json({commandId:input.requestId,status:'offline',errorCode:'AUTHORIZATION_REQUIRED'});
    const oauthGrantId=authorization.kind==='google'?authorization.grantId:undefined;
    if(oauthGrantId!==undefined&&!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(oauthGrantId))return Response.json({commandId:input.requestId,status:'offline',errorCode:'GRANT_REVOKED'});
    const release=oauthGrantId===undefined?null:this.beginOAuthGrantOperation(oauthGrantId);
    if(oauthGrantId!==undefined&&!release)return Response.json({commandId:input.requestId,status:'offline',errorCode:'GRANT_REVOKED'});
    try{return await this.executeCommand(input,oauthGrantId);}
    finally{release?.();}
  }
  private oauthGrantAllowed(grantId?:string):boolean{return grantId===undefined||!this.oauthGrantRevoked(grantId);}
  private async executeCommand(input:ControlInput,oauthGrantId?:string):Promise<Response>{
    this.cleanQueries();
    if(this.env.HUE_TUNNEL){
      if(this.tunnelActive>=16)return Response.json({commandId:input.requestId,status:'error',errorCode:'BUSY'},{status:503});
      this.tunnelActive++;
      const queued=Date.now();const previous=this.tunnelTail;let release!:()=>void;
      this.tunnelTail=new Promise<void>(resolve=>release=resolve);
      try{
        await previous;
        // Keep queueing within the shared Google deadline; expired work is never sent.
        if(Date.now()-queued>1000)return Response.json({commandId:input.requestId,status:'error',errorCode:'BUSY'},{status:503});
        if(!this.oauthGrantAllowed(oauthGrantId))return Response.json({commandId:input.requestId,status:'offline',errorCode:'GRANT_REVOKED'});
        return await this.executeTunnel(input,oauthGrantId);
      }finally{this.tunnelActive--;release();}
    }
    const revoked=await this.ctx.storage.get<boolean>('revoked');
    if(!this.oauthGrantAllowed(oauthGrantId))return Response.json({commandId:input.requestId,status:'offline',errorCode:'GRANT_REVOKED'});
    const sockets=await this.currentSockets();
    if(!this.oauthGrantAllowed(oauthGrantId))return Response.json({commandId:input.requestId,status:'offline',errorCode:'GRANT_REVOKED'});
    const now=Date.now()/1000;
    this.ctx.storage.sql.exec('DELETE FROM commands WHERE created<=?',now-1800);
    const found=this.rows('SELECT * FROM commands WHERE id=?',input.requestId)[0];
    if(found){
      if(found.fingerprint!==logicalKey(input))return Response.json({errorCode:'COMMAND_ID_CONFLICT'},{status:409});
      return Response.json(found.result?JSON.parse(found.result):unknown(input.requestId));
    }
    if(revoked)return Response.json({commandId:input.requestId,status:'offline',errorCode:'AGENT_REVOKED'});
    if(this.pending.size>=16||this.full(input))return Response.json({commandId:input.requestId,status:'error',errorCode:'BUSY'},{status:503});
    const ws=sockets.find(ws=>(ws.deserializeAttachment() as Attachment).ready);
    if(!ws)return Response.json({commandId:input.requestId,status:'offline',errorCode:'BLE_OFFLINE'});
    const a=ws.deserializeAttachment() as Attachment;
    a.sequence++;
    const command=wireCommand(input,a.sessionId,a.sequence,now);
    this.ctx.storage.sql.exec('INSERT INTO commands VALUES (?,?,?,NULL,?,?)',input.requestId,logicalKey(input),JSON.stringify(command),now,a.sessionId);
    ws.serializeAttachment(a);
    const result=await new Promise<Result>(resolve=>{
      const timer=setTimeout(()=>this.finish(input.requestId,unknown(input.requestId)),6000);
      this.pending.set(input.requestId,{resolve,timer,sessionId:a.sessionId});
      // No await separates this grant fence from starting the irreversible WebSocket send.
      if(!this.oauthGrantAllowed(oauthGrantId)){
        this.finish(input.requestId,{commandId:input.requestId,status:'offline',errorCode:'GRANT_REVOKED'});
        return;
      }
      try{ws.send(JSON.stringify({v:1,type:'command',sessionId:a.sessionId,commandId:input.requestId,command}));}
      catch{this.finish(input.requestId,unknown(input.requestId));}
    });
    return Response.json(result);
  }
  private async executeTunnel(input:ControlInput,oauthGrantId?:string):Promise<Response>{
    const cached=()=>{
      const row=this.rows('SELECT * FROM commands WHERE id=?',input.requestId)[0];
      if(!row)return null;
      return row.fingerprint!==logicalKey(input)?Response.json({errorCode:'COMMAND_ID_CONFLICT'},{status:409}):Response.json(row.result?JSON.parse(row.result):unknown(input.requestId));
    };
    const now=Date.now()/1000;
    this.ctx.storage.sql.exec('DELETE FROM commands WHERE created<=?',now-1800);
    const existing=cached();if(existing)return existing;
    const offline=(code:string)=>Response.json({commandId:input.requestId,status:'offline',errorCode:code});
    if(!this.oauthGrantAllowed(oauthGrantId))return offline('GRANT_REVOKED');
    if(await this.ctx.storage.get<boolean>('revoked')){
      const revokedFingerprint=await this.ctx.storage.get<string>('revokedFingerprint');
      if(!revokedFingerprint||revokedFingerprint===await this.keyFingerprint)return offline('AGENT_REVOKED');
      // Only a separately deployed device credential may establish a new session.
      await this.ctx.storage.put('revoked',false);
    }
    if(!this.oauthGrantAllowed(oauthGrantId))return offline('GRANT_REVOKED');
    this.cleanQueries();
    if(this.full(input)||this.pending.size>=16)return Response.json({commandId:input.requestId,status:'error',errorCode:'BUSY'},{status:503});
    const signal=AbortSignal.timeout(6000);
    const options={headers:{Authorization:'Bearer '+this.env.DEVICE_TOKEN},redirect:'manual' as const,signal};
    let sessionId:string;let deviceTime=0;let deviceOffset=0;
    const sessionStart=performance.now();const cloudStart=Date.now()/1000;
    try{
      const response=await this.env.HUE_TUNNEL!.fetch('http://127.0.0.1:8788/v1/session',options);
      if(response.status!==200)throw Error('Session unavailable');
      const session=object(await this.tunnelBody(response),['sessionId','serverTime']);
      if(typeof session.sessionId!=='string'||!session.sessionId.length||session.sessionId.length>128||typeof session.serverTime!=='number'||!Number.isFinite(session.serverTime)||session.serverTime<=0)throw Error('Invalid session');
      const roundTrip=(performance.now()-sessionStart)/1000;
      if(roundTrip>1)return offline('CLOCK_UNCERTAIN');
      deviceTime=session.serverTime;deviceOffset=cloudStart+roundTrip/2-deviceTime;
      sessionId=session.sessionId;
    }catch{return offline('TUNNEL_OFFLINE');}
    // Recheck after the network await: another request or revocation may have won.
    if(await this.ctx.storage.get<boolean>('revoked'))return offline('AGENT_REVOKED');
    if(!this.oauthGrantAllowed(oauthGrantId))return offline('GRANT_REVOKED');
    const raced=cached();if(raced)return raced;
    if(this.full(input))return Response.json({commandId:input.requestId,status:'error',errorCode:'BUSY'},{status:503});
    this.ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS tunnel_sequence (id INTEGER PRIMARY KEY, value INTEGER)');
    const previous=this.ctx.storage.sql.exec<{value:number}>('SELECT value FROM tunnel_sequence WHERE id=1').toArray()[0]?.value??0;
    const sequence=Math.max(previous+1,Date.now()*1000);
    this.ctx.storage.sql.exec('INSERT OR REPLACE INTO tunnel_sequence VALUES (1,?)',sequence);
    const created=Date.now()/1000;
    // Device-authenticated clock domain. Subtract the full measured round trip:
    // transit can shorten the five-second lifetime, never lengthen it.
    const command=wireCommand(input,sessionId,sequence,deviceTime-(performance.now()-sessionStart)/1000);
    this.ctx.storage.sql.exec('INSERT INTO commands VALUES (?,?,?,NULL,?,?)',input.requestId,logicalKey(input),JSON.stringify(command),created,sessionId);
    // Recheck synchronously after all awaits and immediately before starting device delivery.
    if(!this.oauthGrantAllowed(oauthGrantId)){
      this.finish(input.requestId,{commandId:input.requestId,status:'offline',errorCode:'GRANT_REVOKED'});
      return cached()!;
    }
    let result=unknown(input.requestId);
    try{
      const response=await this.env.HUE_TUNNEL!.fetch('http://127.0.0.1:8788/v1/commands',{
        ...options,method:'POST',headers:{...options.headers,'Content-Type':'application/json'},body:JSON.stringify(command)});
      if(response.status!==200)throw Error('Delivery unknown');
      const raw=await this.tunnelBody(response) as any;
      const candidate=verifiedResult(raw?.status==='verified'?{...raw,observedAt:raw.observedAt+deviceOffset}:raw,input.requestId);
      if(!signal.aborted&&!await this.ctx.storage.get<boolean>('revoked'))result=candidate;
    }catch{/* A potentially delivered command is never automatically replayed. */}
    this.finish(input.requestId,result);
    return cached()!;
  }
  private async tunnelBody(response:Response):Promise<unknown>{
    if(!response.body)throw Error('Empty response');
    const reader=response.body.getReader();let size=0;const chunks:Uint8Array[]=[];
    try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>8192){await reader.cancel();throw Error('Large response');}chunks.push(value);}}
    finally{reader.releaseLock();}
    const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  }
  async webSocketMessage(ws:WebSocket,message:string|ArrayBuffer){
    const a=ws.deserializeAttachment() as Attachment;
    try{
      if(await this.ctx.storage.get<boolean>('revoked')||(await this.ctx.storage.get<number>('keyVersion')??0)!==a.keyVersion||a.keyFingerprint!==await this.keyFingerprint)throw Error('Revoked');
      if(typeof message!=='string'||new TextEncoder().encode(message).length>8192)throw Error('Size');
      const d=JSON.parse(message);
      if(d.v!==1||d.sessionId!==a.sessionId)throw Error('Session');
      if(d.type==='ready'){
        object(d,['v','type','sessionId','agentSessionId','ready','clientTime']);
        const now=Date.now()/1000;
        if(typeof d.ready!=='boolean'||typeof d.agentSessionId!=='string'||d.agentSessionId.length>128||typeof d.clientTime!=='number'||!Number.isFinite(d.clientTime)||now-a.helloAt>2||Math.abs(d.clientTime-(a.helloAt+now)/2)>1)throw Error('Clock');
        // BLE readiness is advisory: queries must still reach an offline agent for a fresh result.
        a.ready=true;a.agentSessionId=d.agentSessionId;ws.serializeAttachment(a);
        ws.send(JSON.stringify({v:1,type:'ready_ack',sessionId:a.sessionId,serverTime:now}));return;
      }
      object(d,['v','type','sessionId','commandId','result']);
      if(d.type!=='result'||!a.ready||typeof d.commandId!=='string')throw Error('Result');
      const row=this.rows('SELECT * FROM commands WHERE id=?',d.commandId)[0];
      if(!row||row.session!==a.sessionId||row.result!==null)return;
      // No late reply can turn a timed-out command into a new successful observation.
      if(Date.now()/1000>row.created+6){this.finish(row.id,unknown(row.id));return;}
      this.finish(row.id,verifiedResult(d.result,row.id));
    }catch{this.finishSession(a.sessionId);ws.close(4002,'Invalid message');}
  }
  async webSocketClose(ws:WebSocket,code:number){const a=ws.deserializeAttachment() as Attachment;this.finishSession(a.sessionId);ws.close(code);}
  async webSocketError(ws:WebSocket){const a=ws.deserializeAttachment() as Attachment;this.finishSession(a.sessionId);ws.close(1011);}
  async revokeAgent():Promise<boolean>{
    return this.ctx.blockConcurrencyWhile(async()=>{
    const version=await this.ctx.storage.get<number>('keyVersion')??0;
    await this.ctx.storage.put({revoked:true,keyVersion:version+1,revokedFingerprint:await this.keyFingerprint});
    for(const row of this.rows('SELECT * FROM commands WHERE result IS NULL'))this.finish(row.id,unknown(row.id));
    for(const ws of this.ctx.getWebSockets()){
      const a=ws.deserializeAttachment() as Attachment;this.finishSession(a.sessionId);ws.close(4003,'Revoked');
    }
    if(this.env.HUE_TUNNEL){
      try{
        const response=await this.env.HUE_TUNNEL.fetch('http://127.0.0.1:8788/v1/revoke',{
          method:'POST',headers:{Authorization:'Bearer '+this.env.DEVICE_TOKEN},redirect:'manual',signal:AbortSignal.timeout(6000)});
        if(response.status!==200||(await this.tunnelBody(response) as {revoked?:boolean})?.revoked!==true)return false;
      }catch{return false;}
    }
    return true;
    });
  }
}

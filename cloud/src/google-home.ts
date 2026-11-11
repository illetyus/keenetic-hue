import type {Env} from './index';
import {DEVICE_ID,object,controlInput,verifiedResult} from './protocol';
import type {ControlInput,Result} from './protocol';
import {limitedText,oauthJson} from './oauth-http';

async function commandId(name:string){
 const namespace=Uint8Array.from([0x6b,0xa7,0xb8,0x10,0x9d,0xad,0x11,0xd1,0x80,0xb4,0,0xc0,0x4f,0xd4,0x30,0xc8]);
 const data=new TextEncoder().encode(name),joined=new Uint8Array(namespace.length+data.length);joined.set(namespace);joined.set(data,16);
 const hash=new Uint8Array(await crypto.subtle.digest('SHA-1',joined)).slice(0,16);hash[6]=(hash[6]&15)|0x50;hash[8]=(hash[8]&63)|0x80;
 const h=Array.from(hash,b=>b.toString(16).padStart(2,'0')).join('');return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
function state(r:Result){return {online:true,on:r.on!,brightness:r.brightness!};}
function list(value:unknown,max:number):any[]{if(!Array.isArray(value)||!value.length||value.length>max)throw Error('invalid_request');return value;}
function device(value:unknown){const d=object(value,['id','customData'],['id']);if(typeof d.id!=='string'||d.id.length>128)throw Error('invalid_request');return d.id;}
function failed(r:Result){return r.status==='offline'?{ids:[DEVICE_ID],status:'OFFLINE'}:{ids:[DEVICE_ID],status:'ERROR',errorCode:'transientError'};}
async function within<T>(promise:PromiseLike<T>,deadline:number):Promise<T>{
 let timer:ReturnType<typeof setTimeout>|undefined;
 try{return await Promise.race([Promise.resolve(promise),new Promise<never>((_resolve,reject)=>{timer=setTimeout(()=>reject(Error('deadline')),Math.max(0,deadline-Date.now()));})]);}
 finally{if(timer!==undefined)clearTimeout(timer);}
}

export async function googleRoute(request:Request,env:Env,ctx?:ExecutionContext):Promise<Response|null>{
 const url=new URL(request.url);if(url.pathname!=='/fulfillment')return null;
 const deadline=Date.now()+8500;
 if(env.GOOGLE_ENABLED!=='true'||!env.HUE_AUTH)return oauthJson({error:'unavailable'},503);
 if(request.method!=='POST'||url.search||url.hash)return oauthJson({error:'invalid_request'},400);
 const authorization=request.headers.get('Authorization')??'';
 const token=/^Bearer ([A-Za-z0-9_-]{43})$/.exec(authorization)?.[1];
 if(!token)return oauthJson({error:'invalid_token'},401);
 let identity;
 try{identity=await within(env.HUE_AUTH.getByName('owner').authenticate(token),deadline);}
 catch{return oauthJson({error:'temporarily_unavailable'},503);}
 if(!identity)return oauthJson({error:'invalid_token'},401);
 let stage='body',intent='UNKNOWN',fields:string[]=[],outcome='responded';
 const shape=(value:unknown,allowed:string[],required=allowed)=>{fields=value&&typeof value==='object'&&!Array.isArray(value)?Object.keys(value):[];return object(value,allowed,required);};
 const audit=(result:string)=>{const work=Promise.resolve(env.HUE_AUTH!.getByName('owner').recordGoogleStatus?.({intent,stage,fields,outcome:result})).catch(()=>{});if(ctx)ctx.waitUntil(work);};
 try{
  if(request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase()!=='application/json')throw Error('invalid_request');
  const d=shape(JSON.parse(await within(limitedText(request,8192),deadline)),['requestId','inputs'],['requestId','inputs']);
  if(typeof d.requestId!=='string'||!d.requestId.length||d.requestId.length>128)throw Error('invalid_request');
  stage='input';const input=shape(list(d.inputs,1)[0],['intent','payload','context'],['intent']);
  // Google may attach context metadata. It must never affect device commands.
  if(Object.hasOwn(input,'context')&&(input.context===null||typeof input.context!=='object'||Array.isArray(input.context)))throw Error('invalid_request');
  intent=['action.devices.SYNC','action.devices.QUERY','action.devices.EXECUTE','action.devices.DISCONNECT'].includes(input.intent)?input.intent.slice('action.devices.'.length):'UNKNOWN';
  const respond=(payload:unknown)=>{audit(outcome);return oauthJson({requestId:d.requestId,payload});};
  const relay=env.HUE_RELAY.getByName(DEVICE_ID);
  const execute=async(operation:'query'|'set',index:string,fields:Record<string,unknown>={}):Promise<Result>=>{
   const id=await commandId(JSON.stringify([identity.grantId,d.requestId,input.intent,DEVICE_ID,index]));
   let current;
   try{current=await within(env.HUE_AUTH!.getByName('owner').authenticate(token),deadline);}
   catch{return {commandId:id,status:'expired',errorCode:'EXPIRED'};}
   if(current?.grantId!==identity.grantId)return {commandId:id,status:'error',errorCode:'AGENT_REVOKED'};
   // Reserve the bounded1s Tunnel queue plus its6s transport budget.
   if(deadline-Date.now()<7000)return {commandId:id,status:'expired',errorCode:'EXPIRED'};
   const command=controlInput({requestId:id,deviceId:DEVICE_ID,operation,...fields});
   let response;
   try{response=await within(relay.execute(command,{kind:'google',grantId:identity.grantId}),deadline);}catch{return {commandId:id,status:'error',errorCode:'OUTCOME_UNKNOWN'};}
   if(response.status!==200||Date.now()>deadline)return {commandId:id,status:'error',errorCode:'OUTCOME_UNKNOWN'};
   let raw:any;try{raw=await within(response.json(),deadline);}catch{return {commandId:id,status:'error',errorCode:'OUTCOME_UNKNOWN'};}
   // Offline results have no fabricated state; validated successful results are mandatory.
   if(raw?.status==='offline')return {commandId:id,status:'offline',errorCode:'BLE_OFFLINE'};
   try{return verifiedResult(raw,id,operation==='query'?30:1800);}
   catch{return {commandId:id,status:'error',errorCode:'OUTCOME_UNKNOWN'};}
  };
  if(input.intent==='action.devices.SYNC')return respond({agentUserId:identity.ownerId,devices:[{id:DEVICE_ID,type:'action.devices.types.LIGHT',traits:['action.devices.traits.OnOff','action.devices.traits.Brightness'],name:{name:'Hue Lamba'},willReportState:env.REPORT_STATE_ENABLED==='true'&&Boolean(env.HOMEGRAPH_SERVICE_ACCOUNT),deviceInfo:{manufacturer:'Signify Netherlands B.V.',model:'LWA017'}}]});
  if(input.intent==='action.devices.DISCONNECT'){
   try{await within(env.HUE_AUTH.getByName('owner').disconnect(identity.grantId),deadline);return oauthJson({});}
   catch{return oauthJson({error:'temporarily_unavailable'},503);}
  }
  if(input.intent==='action.devices.QUERY'){
   stage='payload';const p=shape(input.payload,['devices']);const id=device(list(p.devices,1)[0]);
   if(id!==DEVICE_ID)return respond({devices:{[id]:{online:false,errorCode:'deviceNotFound'}}});
   const r=await execute('query','query');return respond({devices:{[DEVICE_ID]:r.status==='verified'?state(r):{online:false}}});
  }
  if(input.intent==='action.devices.EXECUTE'){
   stage='payload';const p=shape(input.payload,['commands']);const jobs:Array<{command:string;params:Record<string,unknown>;index:string}>=[];
   for(const [i,value]of list(p.commands,8).entries()){
    stage='command';const c=shape(value,['devices','execution']);stage='device';const id=device(list(c.devices,1)[0]);
    if(id!==DEVICE_ID)return respond({commands:[{ids:[id],status:'ERROR',errorCode:'deviceNotFound'}]});
    for(const[j,value]of list(c.execution,8).entries()){
     stage='execution';const e=shape(value,['command','params']);if(typeof e.command!=='string')throw Error('invalid_request');
     jobs.push({command:e.command,params:e.params,index:`${i}:${j}`});if(jobs.length>8)throw Error('invalid_request');
    }
   }
   // Validate all command parameters before any physical write.
   const mapped=jobs.map(job=>{stage='params';
    if(job.command==='action.devices.commands.OnOff'){const p=shape(job.params,['on']);if(typeof p.on!=='boolean')throw Error('invalid_request');return {index:job.index,fields:{on:p.on}};}
    if(job.command==='action.devices.commands.BrightnessAbsolute'){const p=shape(job.params,['brightness']);if(!Number.isInteger(p.brightness)||p.brightness<0||p.brightness>100)throw Error('invalid_request');return {index:job.index,fields:{brightness:p.brightness}};}
    return {index:job.index,fields:null};
   });
   if(mapped.some(j=>j.fields===null))return respond({commands:[{ids:[DEVICE_ID],status:'ERROR',errorCode:'functionNotSupported'}]});
   if(env.GOOGLE_QUEUE_ENABLED==='true'){
    stage='queue';
    const accepted=await within(env.HUE_AUTH.getByName('owner').submitCommands(identity.grantId,d.requestId,mapped.map(j=>j.fields!) as Array<{on:boolean}|{brightness:number}>),deadline);
    outcome=accepted.accepted?'pending':accepted.errorCode??'QUEUE_FAILED';
    return respond({commands:[accepted.accepted?{ids:[DEVICE_ID],status:'PENDING'}:{ids:[DEVICE_ID],status:'ERROR',errorCode:'transientError'}]});
   }
   let last:Result|undefined;
   for(const job of mapped){stage='relay';last=await execute('set',job.index,job.fields!);outcome=last.errorCode??last.status;if(last.status!=='verified')return respond({commands:[failed(last)]});}
   if(ctx&&last?.status==='verified')ctx.waitUntil(env.HUE_AUTH.getByName('owner').queueReport(identity.grantId,{online:true,on:last.on,brightness:last.brightness,observedAt:last.observedAt!}).then(()=>{}).catch(()=>{}));
   return respond({commands:[{ids:[DEVICE_ID],status:'SUCCESS',states:state(last!)}]});
  }
  throw Error('invalid_request');
 }catch{audit('invalid_request');return oauthJson({error:'invalid_request'},400);}
}

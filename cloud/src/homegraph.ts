import {DEVICE_ID} from './protocol';
export type HomeState={online:boolean;on?:boolean;brightness?:number};
type Credential={client_email:string;private_key:string};
const tokenUrl='https://oauth2.googleapis.com/token';
function base64(bytes:Uint8Array){return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
export class HomeGraph{
 private credential:Credential;private access?:{value:string;expires:number};
 constructor(raw:string,private send:typeof fetch=(input,init)=>fetch(input,init)){
  const c=JSON.parse(raw);if(typeof c.client_email!=='string'||!c.client_email.endsWith('.iam.gserviceaccount.com')||typeof c.private_key!=='string'||c.private_key.length>16384)throw Error('Invalid HomeGraph credential');this.credential=c;
 }
 private async token(){
  if(this.access&&this.access.expires>Date.now()/1000+60)return this.access.value;
  const now=Math.floor(Date.now()/1000);
  const encoded=this.credential.private_key.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\s/g,'');
  const key=await crypto.subtle.importKey('pkcs8',Uint8Array.from(atob(encoded),c=>c.charCodeAt(0)),{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['sign']);
  const encode=(v:unknown)=>base64(new TextEncoder().encode(JSON.stringify(v)));
  const unsigned=encode({alg:'RS256',typ:'JWT'})+'.'+encode({iss:this.credential.client_email,scope:'https://www.googleapis.com/auth/homegraph',aud:tokenUrl,iat:now,exp:now+3600});
  const sig=await crypto.subtle.sign('RSASSA-PKCS1-v1_5',key,new TextEncoder().encode(unsigned));
  const response=await this.send(tokenUrl,{method:'POST',redirect:'manual',signal:AbortSignal.timeout(5000),headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'urn:ietf:params:oauth:grant-type:jwt-bearer',assertion:unsigned+'.'+base64(new Uint8Array(sig))}).toString()});
  if(!response.ok){
    const data=await response.json().catch(()=>({})) as {error?:string};
    const code=typeof data.error==='string'&&/^[a-z_]{1,64}$/.test(data.error)?data.error:'unknown';
    throw Error('HOMEGRAPH_TOKEN_'+response.status+'_'+code);
  }
  const data=await response.json() as any;
  if(typeof data.access_token!=='string'||data.access_token.length>4096||!Number.isInteger(data.expires_in)||data.expires_in<=60||data.expires_in>3600)throw Error('Invalid HomeGraph token');
  this.access={value:data.access_token,expires:now+data.expires_in};return this.access.value;
 }
 async checkAuthentication(){await this.token();return true;}
 private async post(method:string,data:unknown){
  const token=await this.token();const response=await this.send('https://homegraph.googleapis.com/v1/'+method,{method:'POST',redirect:'manual',signal:AbortSignal.timeout(5000),headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(!response.ok){if(response.status===401)this.access=undefined;throw Error('HOMEGRAPH_REQUEST_'+response.status);}
  return response;
 }
 async report(owner:string,state:HomeState){await this.post('devices:reportStateAndNotification',{requestId:crypto.randomUUID(),agentUserId:owner,payload:{devices:{states:{[DEVICE_ID]:state}}}});}
 async requestSync(owner:string){await this.post('devices:requestSync',{agentUserId:owner,async:true});}
 async query(owner:string):Promise<HomeState>{
  const response=await this.post('devices:query',{requestId:crypto.randomUUID(),agentUserId:owner,inputs:[{payload:{devices:[{id:DEVICE_ID}]}}]});
  const data=await response.json() as any;const state=data?.payload?.devices?.[DEVICE_ID];
  if(!state||typeof state.online!=='boolean'||state.online&&(typeof state.on!=='boolean'||!Number.isInteger(state.brightness)||state.brightness<0||state.brightness>100))throw Error('HOMEGRAPH_INVALID_STATE');
  return state.online?{online:true,on:state.on,brightness:state.brightness}:{online:false};
 }
 async removeAgent(owner:string){const token=await this.token();const response=await this.send('https://homegraph.googleapis.com/v1/agentUsers/'+encodeURIComponent(owner),{method:'DELETE',redirect:'manual',signal:AbortSignal.timeout(5000),headers:{Authorization:'Bearer '+token}});if(!response.ok&&response.status!==404)throw Error('HomeGraph unlink unavailable');}
}

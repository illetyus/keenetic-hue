import {authorized} from './auth';
import {randomToken,digest} from './oauth-store';
import type {Outcome} from './oauth-store';
import type {Env} from './index';
import {issueTicket,ticketLifetime,verifyTicket} from './oauth-ticket';

// Native form POSTs need their real Origin; no-referrer makes browsers send null.
export const oauthHeaders={'Cache-Control':'no-store','Pragma':'no-cache','Referrer-Policy':'same-origin','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"};
export function oauthJson(data:unknown,status=200){return Response.json(data,{status,headers:oauthHeaders});}
function unwrap<T>(r:Outcome<T>):T{if(!r.ok)throw Error(r.error);return r.value;}
export async function limitedText(request:Request,max=4096):Promise<string>{
 if(!request.body)return '';
 const reader=request.body.getReader();let size=0;const chunks:Uint8Array[]=[];
 const deadline=Date.now()+3000;
 try{while(true){
   let timer:ReturnType<typeof setTimeout>|undefined;
   const part=await Promise.race([reader.read(),new Promise<never>((_resolve,reject)=>{timer=setTimeout(()=>reject(Error('invalid_request')),Math.max(0,deadline-Date.now()));})]).finally(()=>{if(timer!==undefined)clearTimeout(timer);});
   const {done,value}=part;if(done)break;size+=value.length;if(size>max)throw Error('invalid_request');chunks.push(value);
 }}catch(error){void reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
 const all=new Uint8Array(size);let offset=0;for(const c of chunks){all.set(c,offset);offset+=c.length;}return new TextDecoder('utf-8',{fatal:true}).decode(all);
}
function params(raw:string,allowed:string[],required:string[]){
 const p=new URLSearchParams(raw),data:Record<string,string>={};
 for(const [k,v] of p){if(!allowed.includes(k)||Object.hasOwn(data,k))throw Error('invalid_request');data[k]=v;}
 if(required.some(k=>!data[k]))throw Error('invalid_request');return data;
}
async function form(request:Request,allowed:string[],required:string[],max=4096){
 if(request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase()!=='application/x-www-form-urlencoded')throw Error('invalid_request');
 return params(await limitedText(request,max),allowed,required);
}
function cookie(request:Request){
 const values=(request.headers.get('Cookie')??'').split(';').map(s=>s.trim()).filter(s=>s.startsWith('__Host-hue-flow='));
 if(values.length!==1)throw Error('invalid_grant');const value=values[0].slice('__Host-hue-flow='.length);
 if(!/^[A-Za-z0-9_-]{43}$/.test(value))throw Error('invalid_grant');return value;
}
function consentHeaders(target:string){return {...oauthHeaders,'Content-Security-Policy':oauthHeaders['Content-Security-Policy'].replace("form-action 'self'","form-action 'self' "+target)};}
function redirect(target:string,state:string,values:Record<string,string>){const u=new URL(target);u.searchParams.set('state',state);for(const[k,v]of Object.entries(values))u.searchParams.set(k,v);return new Response(null,{status:303,headers:{...consentHeaders(target),Location:u.toString(),'Set-Cookie':'__Host-hue-flow=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0'}});}

export async function oauthRoute(request:Request,env:Env):Promise<Response|null>{
 const url=new URL(request.url);if(!['/oauth/authorize','/oauth/token','/admin/setup-code'].includes(url.pathname))return null;
 if(env.GOOGLE_ENABLED!=='true'||!env.HUE_AUTH||!env.GOOGLE_PROJECT_ID||!env.OAUTH_CLIENT_ID||!env.OAUTH_CLIENT_SECRET||!env.PUBLIC_ORIGIN)return oauthJson({error:'temporarily_unavailable'},503);
 if(url.origin!==env.PUBLIC_ORIGIN||url.hash||url.href.length>8192)return oauthJson({error:'invalid_request'},400);
 const auth=env.HUE_AUTH.getByName('owner');
 try{
  if(url.pathname==='/admin/setup-code'){
   if(request.method!=='POST'||url.search)return oauthJson({error:'invalid_request'},400);
   if(!await authorized(request,env.SETUP_ISSUER_TOKEN))return oauthJson({error:'unauthorized'},401);
   return oauthJson(unwrap(await auth.issueSetup()));
  }
  if(url.pathname==='/oauth/token'){
   if(request.method!=='POST'||url.search)throw Error('invalid_request');
   const d=await form(request,['client_id','client_secret','grant_type','code','redirect_uri','refresh_token'],['client_id','client_secret','grant_type']);
   if(request.headers.has('Authorization'))throw Error('invalid_request');
   if(d.client_id!==env.OAUTH_CLIENT_ID||!await authorized(new Request(url,{headers:{Authorization:'Bearer '+d.client_secret}}),env.OAUTH_CLIENT_SECRET))return oauthJson({error:'invalid_client'},401);
   if(d.grant_type==='authorization_code'&&(!d.code||!d.redirect_uri||d.refresh_token)||d.grant_type==='refresh_token'&&(!d.refresh_token||d.code||d.redirect_uri))throw Error('invalid_request');
   return oauthJson(unwrap(await auth.exchange(d)));
  }
  if(request.method==='GET'){
   const d=params(url.search.slice(1),['client_id','redirect_uri','response_type','state','scope'],['client_id','redirect_uri','response_type','state']);
   const project=env.GOOGLE_PROJECT_ID;
   const redirects=[`https://oauth-redirect.googleusercontent.com/r/${project}`,`https://oauth-redirect-sandbox.googleusercontent.com/r/${project}`];
   if(d.client_id!==env.OAUTH_CLIENT_ID||d.response_type!=='code'||!redirects.includes(d.redirect_uri)||d.scope&&d.scope!=='hue.control')throw Error('invalid_request');
   if(d.client_id.length>256||d.redirect_uri.length>2048||d.state.length>1024)throw Error('invalid_request');
   if(!env.SETUP_ISSUER_TOKEN)throw Error('temporarily_unavailable');
   const csrf=randomToken();
   const flow={flowId:await issueTicket(env.SETUP_ISSUER_TOKEN,csrf,{client:d.client_id,redirect:d.redirect_uri,state:d.state})};
   const html='<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Keenetic Hue — Google bağlantısı</title><main><h1>Keenetic Hue</h1><p>Google hesabını Keenetic Hue sistemine bağla.</p><p>Onayladığında Google, Hue Lamba cihazını açabilir, kapatabilir, parlaklığını değiştirebilir ve durumunu okuyabilir.</p><p>Google şifreni burada girme. Sana verilen tek kullanımlık kurulum kodunu kullan.</p><form method="post" action="/oauth/authorize"><input type="hidden" name="flowId" value="'+flow.flowId+'"><label>Kurulum kodu <input name="setupCode" autocomplete="off" maxlength="26" required></label><button name="decision" value="link">Onayla ve bağla</button><button name="decision" value="cancel" formnovalidate>Vazgeç</button></form><p><a href="https://policies.google.com/privacy" rel="noreferrer">Google gizlilik politikası</a></p></main></html>';
   return new Response(html,{headers:{...consentHeaders(d.redirect_uri),'Content-Type':'text/html; charset=utf-8','Set-Cookie':`__Host-hue-flow=${csrf}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=${ticketLifetime}`}});
  }
  if(request.method==='POST'&&!url.search){
   if(request.headers.get('Origin')!==env.PUBLIC_ORIGIN)throw Error('invalid_request');
   const d=await form(request,['flowId','setupCode','decision'],['flowId','decision'],12288);let csrf:string;
   try{csrf=cookie(request);}catch(error){await auth.recordLinkStatus(request.headers.get('Cookie')?.includes('__Host-hue-flow=')?'cookie_invalid':'cookie_missing');throw error;}
   if(d.decision==='cancel'){
    if(d.flowId.includes('.')){
     const ticket=await verifyTicket(env,csrf,d.flowId);
     return redirect(ticket.redirect,ticket.state,{error:'access_denied'});
    }
    const c=unwrap(await auth.cancelFlow({flowId:d.flowId,csrf}));return redirect(c.redirectUri,c.state,{error:'access_denied'});
   }
   if(d.decision!=='link'||!/^[A-Z2-7]{26}$/.test(d.setupCode??'')){await auth.recordLinkStatus('code_format');throw Error('invalid_grant');}
   const source=await digest('edge:'+ (request.headers.get('CF-Connecting-IP')??'unattributed'));
   const c=unwrap(await auth.consent({flowId:d.flowId,csrf,setupCode:d.setupCode,source}));return redirect(c.redirectUri,c.state,{code:c.code});
  }
  throw Error('invalid_request');
 }catch(error){const code=error instanceof Error?error.message:'';const known=['invalid_grant','invalid_request','unsupported_grant_type'].includes(code);return oauthJson({error:known?code:'temporarily_unavailable'},known?400:503);}
}

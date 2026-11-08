import { authorized } from './auth';
import { DEVICE_ID, controlInput, ProtocolError } from './protocol';
import { Relay } from './relay';
import {oauthRoute} from './oauth-http';
import {googleRoute} from './google-home';
import type {AuthStore} from './oauth-store';
export { Relay };
export {AuthStore} from './oauth-store';
export interface Env {HUE_RELAY:DurableObjectNamespace<Relay>;DEVICE_TOKEN:string;OPERATOR_TOKEN:string;SETUP_ISSUER_TOKEN?:string;CONTROL_ENABLED?:string;HUE_TUNNEL?:Fetcher;HUE_AUTH?:DurableObjectNamespace<AuthStore>;GOOGLE_ENABLED?:string;GOOGLE_QUEUE_ENABLED?:string;GOOGLE_PROJECT_ID?:string;OAUTH_CLIENT_ID?:string;OAUTH_CLIENT_SECRET?:string;PUBLIC_ORIGIN?:string;REPORT_STATE_ENABLED?:string;HOMEGRAPH_SERVICE_ACCOUNT?:string}

async function body(request:Request):Promise<unknown>{
  if(!request.body)throw new ProtocolError('INVALID_MESSAGE');
  const reader=request.body.getReader();let size=0;const chunks:Uint8Array[]=[];
  try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>4096)throw new ProtocolError('INVALID_SIZE');chunks.push(value);}}finally{reader.releaseLock();}
  const all=new Uint8Array(size);let offset=0;for(const chunk of chunks){all.set(chunk,offset);offset+=chunk.length;}return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(all));
}
export default {
  async scheduled(_event:ScheduledController,env:Env,ctx:ExecutionContext){if(env.HUE_AUTH)ctx.waitUntil(env.HUE_AUTH.getByName('owner').pollReport());},
  async fetch(request:Request,env:Env,ctx:ExecutionContext):Promise<Response>{
    const url=new URL(request.url);
    const oauth=await oauthRoute(request,env);if(oauth)return oauth;
    const google=await googleRoute(request,env,ctx);if(google)return google;
    if(url.search||url.hash)return new Response(null,{status:400});
    if(url.pathname==='/health'&&request.method==='GET')return Response.json({ok:true});
    const stub=env.HUE_RELAY.getByName(DEVICE_ID);
    if(url.pathname==='/admin/command-status'&&request.method==='POST'){
      if(!await authorized(request,env.OPERATOR_TOKEN))return new Response(null,{status:401});
      return Response.json(await env.HUE_AUTH?.getByName('owner').commandStatus()??null,{headers:{'Cache-Control':'no-store'}});
    }
    if(['/admin/report-check','/admin/report-status'].includes(url.pathname)&&request.method==='POST'){
      if(!await authorized(request,env.OPERATOR_TOKEN))return new Response(null,{status:401});
      const auth=env.HUE_AUTH?.getByName('owner');
      return Response.json(auth?(url.pathname==='/admin/report-check'?await auth.checkReporting():await auth.reportStatus()):{configured:false},{headers:{'Cache-Control':'no-store'}});
    }
    if(url.pathname==='/admin/google-status'&&request.method==='POST'){
      if(!await authorized(request,env.OPERATOR_TOKEN))return new Response(null,{status:401});
      return Response.json(await env.HUE_AUTH?.getByName('owner').googleStatus()??[],{headers:{'Cache-Control':'no-store'}});
    }
    if(url.pathname==='/admin/link-status'&&request.method==='POST'){
      if(!await authorized(request,env.OPERATOR_TOKEN))return new Response(null,{status:401});
      return Response.json(await env.HUE_AUTH?.getByName('owner').linkStatus()??null,{headers:{'Cache-Control':'no-store'}});
    }
    if(url.pathname==='/admin/homegraph-check'&&request.method==='POST'){
      if(!await authorized(request,env.OPERATOR_TOKEN))return new Response(null,{status:401});
      return Response.json(await env.HUE_AUTH?.getByName('owner').checkHomeGraph()??{authenticated:false,configured:false},{headers:{'Cache-Control':'no-store'}});
    }
    if(url.pathname==='/admin/request-sync'&&request.method==='POST'){
      if(!await authorized(request,env.OPERATOR_TOKEN))return new Response(null,{status:401});
      return Response.json({requested:await env.HUE_AUTH?.getByName('owner').requestSync()??false});
    }
    if(url.pathname==='/agent'&&request.method==='GET'){
      if(!await authorized(request,env.DEVICE_TOKEN))return new Response(null,{status:401});
      if(request.headers.get('Upgrade')?.toLowerCase()!=='websocket')return new Response(null,{status:426});
      return stub.fetch(request);
    }
    if(url.pathname==='/control'&&request.method==='POST'){
      if(!await authorized(request,env.OPERATOR_TOKEN))return new Response(null,{status:401});
      if(env.CONTROL_ENABLED!=='true')return new Response(null,{status:404});
      try{return await stub.execute(controlInput(await body(request)),{kind:'operator'});}catch(error){
        if(error instanceof ProtocolError||error instanceof SyntaxError)return Response.json({errorCode:'INVALID_COMMAND'},{status:400});
        return Response.json({errorCode:'RELAY_FAILED'},{status:503});
      }
    }
    if(url.pathname==='/admin/revoke-agent'&&request.method==='POST'){
      if(!await authorized(request,env.OPERATOR_TOKEN))return new Response(null,{status:401});
      try{if(!await stub.revokeAgent())return Response.json({errorCode:'DEVICE_FENCE_UNCONFIRMED'},{status:503});return Response.json({revoked:true});}
      catch{return Response.json({errorCode:'DEVICE_FENCE_UNCONFIRMED'},{status:503});}
    }
    return new Response(null,{status:404});
  },
};

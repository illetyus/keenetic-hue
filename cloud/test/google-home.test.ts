import {it,expect,vi} from 'vitest';
import {googleRoute} from '../src/google-home';
import type {Env} from '../src/index';
const id='hue-lwa017-01';
const accessToken='A'.repeat(43);
it('rejects malformed bearer values before looking up the auth object',async()=>{
 const f=fixture();
 const getByName=vi.fn();
 (f.env.HUE_AUTH as any).getByName=getByName;
 for(const value of ['Bearer short','Bearer '+'A'.repeat(44),'Bearer '+'+'.repeat(43)]){
  const req=request('SYNC');
  req.headers.set('Authorization',value);
  expect((await googleRoute(req,f.env))!.status).toBe(401);
 }
 expect(getByName).not.toHaveBeenCalled();
});
function fixture(){const calls:any[]=[];let disconnected='';const env={GOOGLE_ENABLED:'true',HUE_AUTH:{getByName:()=>({authenticate:async(t:string)=>t===accessToken?{ownerId:'owner',grantId:'grant'}:null,disconnect:async(g:string)=>{disconnected=g;}})},HUE_RELAY:{getByName:()=>({execute:async(input:any)=>{calls.push(input);return Response.json({commandId:input.requestId,status:'verified',deviceId:id,on:input.on??false,brightness:input.on?40:0,brightnessRaw:102,observedAt:Date.now()/1000});}})}} as unknown as Env;return {env,calls,getDisconnected:()=>disconnected};}
function request(intent:string,payload?:unknown,requestId='google-request-1'){return new Request('https://relay.test/fulfillment',{method:'POST',headers:{Authorization:'Bearer '+accessToken,'Content-Type':'application/json'},body:JSON.stringify({requestId,inputs:[{intent:'action.devices.'+intent,...(payload?{payload}:{})}]})});}
it('SYNC is single LIGHT and unauthenticated requests cannot reach the relay',async()=>{
 const f=fixture();const r=await googleRoute(request('SYNC'),f.env);const body=await r!.json() as any;
 expect(body.payload.devices).toHaveLength(1);expect(body.payload.devices[0].type).toBe('action.devices.types.LIGHT');expect(body.payload.devices[0].willReportState).toBe(false);
 const bad=request('QUERY',{devices:[{id}]});bad.headers.set('Authorization','Bearer wrong');expect((await googleRoute(bad,f.env))!.status).toBe(401);expect(f.calls).toHaveLength(0);
});
it('acknowledges durably queued rapid commands as PENDING without guessed state or direct writes',async()=>{
 const f=fixture();f.env.GOOGLE_QUEUE_ENABLED='true';const accepted:any[]=[];
 const original=(f.env.HUE_AUTH as any).getByName;
 (f.env.HUE_AUTH as any).getByName=()=>({...original(),submitCommands:async(...args:any[])=>{accepted.push(args);return {accepted:true};}});
 const payload={commands:[{devices:[{id}],execution:[{command:'action.devices.commands.BrightnessAbsolute',params:{brightness:35}}]}]};
 const response=await googleRoute(request('EXECUTE',payload),f.env);
 expect((await response!.json() as any).payload.commands).toEqual([{ids:[id],status:'PENDING'}]);
 expect(f.calls).toHaveLength(0);expect(accepted[0]).toEqual(['grant','google-request-1',[{brightness:35}]]);
 const invalid={commands:[{devices:[{id}],execution:[{command:'action.devices.commands.OnOff',params:{on:false}},{command:'action.devices.commands.BrightnessAbsolute',params:{brightness:101}}]}]};
 expect((await googleRoute(request('EXECUTE',invalid,'invalid-batch'),f.env))!.status).toBe(400);expect(accepted).toHaveLength(1);
});
it('QUERY reads the relay and EXECUTE retries derive exactly the same command ID',async()=>{
 const f=fixture();const q=await (await googleRoute(request('QUERY',{devices:[{id}]}),f.env))!.json() as any;
 expect(q.payload.devices[id]).toEqual({online:true,on:false,brightness:0});
 const p={commands:[{devices:[{id}],execution:[{command:'action.devices.commands.OnOff',params:{on:false}}]}]};
 const first=await (await googleRoute(request('EXECUTE',p),f.env))!.json() as any;
 await googleRoute(request('EXECUTE',p),f.env);
 expect(first.payload.commands[0]).toMatchObject({ids:[id],status:'SUCCESS',states:{on:false,online:true}});
 expect(f.calls[1].requestId).toBe(f.calls[2].requestId);expect(f.calls[1].on).toBe(false);
});
it('offline and unknown replies cannot produce guessed successful states',async()=>{
 const f=fixture();(f.env.HUE_RELAY as any).getByName=()=>({execute:async()=>Response.json({status:'offline',errorCode:'BLE_OFFLINE'})});
 const q=await (await googleRoute(request('QUERY',{devices:[{id}]}),f.env))!.json() as any;expect(q.payload.devices[id].online).toBe(false);expect(q.payload.devices[id].on).toBeUndefined();
});
it('accepts Google input context as opaque metadata without forwarding it to the device',async()=>{
 const f=fixture();
 const payload={commands:[{devices:[{id}],execution:[{command:'action.devices.commands.OnOff',params:{on:false}}]}]};
 const make=(extra:Record<string,unknown>)=>new Request('https://relay.test/fulfillment',{method:'POST',headers:{Authorization:'Bearer '+accessToken,'Content-Type':'application/json'},body:JSON.stringify({requestId:'google-context-request',inputs:[{intent:'action.devices.EXECUTE',payload,...extra}]})});
 const response=await googleRoute(make({context:{locale:'tr-TR',on:true,deviceId:'untrusted'}}),f.env);
 expect(response!.status).toBe(200);expect((await response!.json() as any).payload.commands[0].status).toBe('SUCCESS');
 expect(f.calls).toHaveLength(1);expect(f.calls[0].on).toBe(false);expect(f.calls[0].deviceId).toBe(id);expect(f.calls[0]).not.toHaveProperty('context');
 expect((await googleRoute(make({context:'invalid'}),f.env))!.status).toBe(400);
 expect((await googleRoute(make({unexpected:true}),f.env))!.status).toBe(400);expect(f.calls).toHaveLength(1);
});
it('DISCONNECT revokes exactly the authenticated grant',async()=>{const f=fixture();expect((await googleRoute(request('DISCONNECT'),f.env))!.status).toBe(200);expect(f.getDisconnected()).toBe('grant');});
it('unlink during one execution prevents dispatch of subsequent executions',async()=>{
 const f=fixture();let linked=true;
 (f.env.HUE_AUTH as any).getByName=()=>({authenticate:async()=>linked?{ownerId:'owner',grantId:'grant'}:null});
 const old=(f.env.HUE_RELAY as any).getByName;
 (f.env.HUE_RELAY as any).getByName=()=>({execute:async(c:any,a:any)=>{const r=await old().execute(c,a);linked=false;return r;}});
 const payload={commands:[{devices:[{id}],execution:[{command:'action.devices.commands.OnOff',params:{on:false}},{command:'action.devices.commands.BrightnessAbsolute',params:{brightness:20}}]}]};
 const r=await (await googleRoute(request('EXECUTE',payload),f.env))!.json() as any;
 expect(f.calls).toHaveLength(1);expect(r.payload.commands[0].status).not.toBe('SUCCESS');
});
it('bounds a hung Relay RPC by the original shared deadline without retry',async()=>{
 vi.useFakeTimers();try{
  const f=fixture();let calls=0;
  (f.env.HUE_RELAY as any).getByName=()=>({execute:()=>{calls++;return new Promise(()=>{});}});
  const pending=googleRoute(request('QUERY',{devices:[{id}]}),f.env);
  await vi.waitUntil(()=>calls===1);await vi.advanceTimersByTimeAsync(8500);
  const response=await pending;expect((await response!.json() as any).payload.devices[id].online).toBe(false);expect(calls).toBe(1);
 }finally{vi.useRealTimers();}
},6000);
it('bounds hung initial authentication before any device call',async()=>{
 vi.useFakeTimers();try{
  const f=fixture();(f.env.HUE_AUTH as any).getByName=()=>({authenticate:()=>new Promise(()=>{})});
  const pending=googleRoute(request('QUERY',{devices:[{id}]}),f.env);await vi.advanceTimersByTimeAsync(8500);
  expect((await pending)!.status).toBe(503);expect(f.calls).toHaveLength(0);
 }finally{vi.useRealTimers();}
},6000);
it('reserves both the Tunnel queue and transport budget before dispatch',async()=>{
 vi.useFakeTimers();try{
  const f=fixture();
  (f.env.HUE_AUTH as any).getByName=()=>({authenticate:async()=>{vi.setSystemTime(Date.now()+1000);return {ownerId:'owner',grantId:'grant'};}});
  const payload={commands:[{devices:[{id}],execution:[{command:'action.devices.commands.OnOff',params:{on:false}}]}]};
  const response=await googleRoute(request('EXECUTE',payload),f.env);
  expect((await response!.json() as any).payload.commands[0].status).toBe('ERROR');expect(f.calls).toHaveLength(0);
 }finally{vi.useRealTimers();}
});
it('validates every execution before the first write',async()=>{
 const diagnostics:any[]=[];
 const f=fixture();const payload={commands:[{devices:[{id}],execution:[{command:'action.devices.commands.OnOff',params:{on:false}},{command:'action.devices.commands.BrightnessAbsolute',params:{brightness:101}}]}]};
 const original=(f.env.HUE_AUTH as any).getByName;
 (f.env.HUE_AUTH as any).getByName=()=>({...original(),recordGoogleStatus:async(d:any)=>{diagnostics.push(d);}});
 expect((await googleRoute(request('EXECUTE',payload),f.env))!.status).toBe(400);expect(f.calls).toHaveLength(0);
 expect(diagnostics).toContainEqual({intent:'EXECUTE',stage:'params',fields:['brightness'],outcome:'invalid_request'});
});

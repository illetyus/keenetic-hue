import {env} from 'cloudflare:workers';
import {runInDurableObject} from 'cloudflare:test';
import {it,expect} from 'vitest';
import type {Env} from '../src/index';
const bindings=env as unknown as Env;
it('uses the authenticated device clock for expiry while bounding round trip',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-clock-offset');
 await runInDurableObject(stub,async(instance:any)=>{
  let sent=0;
  instance.env.HUE_TUNNEL={fetch:async(url:string,init:any)=>{
   const deviceNow=Date.now()/1000-2.5;
   if(url.endsWith('/v1/session'))return Response.json({sessionId:'offset',serverTime:deviceNow});
   const command=JSON.parse(init.body);sent++;
   expect(command.expiresAt-deviceNow).toBeLessThanOrEqual(5.05);
   expect(command.expiresAt-deviceNow).toBeGreaterThan(3.5);
   return Response.json({commandId:command.commandId,status:'verified',deviceId:'hue-lwa017-01',on:false,brightness:0,brightnessRaw:254,observedAt:deviceNow});
  }};
  const result=await(await instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'})).json();
  expect(result.status).toBe('verified');expect(sent).toBe(1);
  expect(Math.abs(Date.now()/1000-result.observedAt)).toBeLessThan(1);
 });
});
it('serializes concurrent Tunnel deliveries so a later sequence cannot overtake an earlier one',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-ordered');
 await runInDurableObject(stub,async(instance:any)=>{
  let first!:()=>void;let release!:()=>void;let active=0,peak=0,calls=0;
  const started=new Promise<void>(r=>first=r);
  instance.env.HUE_TUNNEL={fetch:async(url:string,init:any)=>{
   if(url.endsWith('/v1/session'))return Response.json({sessionId:'ordered',serverTime:Date.now()/1000});
   const c=JSON.parse(init.body);active++;peak=Math.max(peak,active);calls++;
   if(calls===1){first();await new Promise<void>(r=>release=r);}active--;
   return Response.json({commandId:c.commandId,status:'verified',deviceId:'hue-lwa017-01',on:false,brightness:0,brightnessRaw:254,observedAt:Date.now()/1000});
  }};
  const make=()=>instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'});
  const a=make();await started;const b=make();await new Promise(r=>setTimeout(r,20));release();
  await Promise.all([a,b]);expect(peak).toBe(1);expect(calls).toBe(2);
 });
});

it('Tunnel revocation fences backend and never promotes an in-flight result',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-revoke-flight');
 await runInDurableObject(stub,async(instance:any)=>{
  let delivered!:()=>void;let release!:()=>void;let fenced=false;
  const started=new Promise<void>(r=>delivered=r);
  instance.env.HUE_TUNNEL={fetch:async(url:string,init:any)=>{
   if(url.endsWith('/v1/session'))return Response.json({sessionId:'old-session',serverTime:Date.now()/1000});
   if(url.endsWith('/v1/revoke')){fenced=true;return Response.json({revoked:true});}
   const c=JSON.parse(init.body);delivered();await new Promise<void>(r=>release=r);
   return Response.json({commandId:c.commandId,status:'verified',deviceId:'hue-lwa017-01',on:false,brightness:0,brightnessRaw:254,observedAt:Date.now()/1000});
  }};
  const pending=instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'set',on:false},{kind:'operator'});
  await started;await instance.revokeAgent();release();
  expect(fenced).toBe(true);expect((await (await pending).json()).errorCode).toBe('OUTCOME_UNKNOWN');
 });
});

it('Tunnel bounds simultaneous requests before any network call',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-capacity');
 await runInDurableObject(stub,async(instance:any)=>{
  const release:Array<()=>void>=[];
  let stopped=false;
  instance.env.HUE_TUNNEL={fetch:()=>stopped?Promise.resolve(new Response(null,{status:503})):new Promise<Response>(resolve=>release.push(()=>{stopped=true;resolve(new Response(null,{status:503}));}))};
  const calls=Array.from({length:16},()=>instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'}));
  await new Promise(resolve=>setTimeout(resolve,20));
  const overflow=await instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'});
  expect(overflow.status).toBe(503);expect((await overflow.json()).errorCode).toBe('BUSY');
  release.forEach(fn=>fn());await Promise.all(calls);
 });
});

it('Tunnel transport deduplicates delivered commands and rejects changed payload',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-test');
 await runInDurableObject(stub,async(instance:any)=>{
  let writes=0;
  instance.env.HUE_TUNNEL={fetch:async(url:string,init:any)=>{
   if(url.endsWith('/v1/session'))return Response.json({sessionId:'tunnel1',serverTime:Date.now()/1000});
   const c=JSON.parse(init.body);writes++;
   return Response.json({commandId:c.commandId,status:'verified',deviceId:'hue-lwa017-01',on:false,brightness:0,brightnessRaw:254,observedAt:Date.now()/1000});
  }};
  const input={requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'set' as const,on:false};
  const r=await instance.execute(input,{kind:'operator'});expect((await r.json()).status).toBe('verified');
  expect((await (await instance.execute(input,{kind:'operator'})).json()).status).toBe('verified');expect(writes).toBe(1);
  expect((await instance.execute({...input,on:true},{kind:'operator'})).status).toBe(409);
 });
});

it('Tunnel delivery failure is persisted unknown and cannot resend on retry',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-unknown');
 await runInDurableObject(stub,async(instance:any)=>{
  let writes=0;
  instance.env.HUE_TUNNEL={fetch:async(url:string)=>{
   if(url.endsWith('/v1/session'))return Response.json({sessionId:'tunnel1',serverTime:Date.now()/1000});
   writes++;throw Error('connection lost after delivery');
  }};
  const input={requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query' as const};
  expect((await (await instance.execute(input,{kind:'operator'})).json()).errorCode).toBe('OUTCOME_UNKNOWN');
  expect((await (await instance.execute(input,{kind:'operator'})).json()).errorCode).toBe('OUTCOME_UNKNOWN');expect(writes).toBe(1);
 });
});

it('Tunnel revoked credentials never reach the private API',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-revoked');
 await runInDurableObject(stub,async(instance:any,state)=>{
  instance.env.HUE_TUNNEL={fetch:()=>{throw Error('must not reach private API');}};
  await state.storage.put('revoked',true);
  const result=await (await instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'})).json();
  expect(result.errorCode).toBe('AGENT_REVOKED');
 });
});

it('Tunnel fence failure does not claim device revocation acknowledgement',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-fence-failure');
 await runInDurableObject(stub,async(instance:any,state)=>{
  instance.env.HUE_TUNNEL={fetch:async()=>new Response(null,{status:503})};
  expect(await instance.revokeAgent()).toBe(false);
  expect(await state.storage.get('revoked')).toBe(true);
 });
});

it('Tunnel recovery requires a separately deployed new device credential',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-key-rotation');
 await runInDurableObject(stub,async(instance:any,state)=>{
  await state.storage.put({revoked:true,revokedFingerprint:'old-fingerprint'});
  instance.keyFingerprint=Promise.resolve('new-fingerprint');
  instance.env.HUE_TUNNEL={fetch:async(url:string,init:any)=>{
   if(url.endsWith('/v1/session'))return Response.json({sessionId:'new-device-session',serverTime:Date.now()/1000});
   const c=JSON.parse(init.body);return Response.json({commandId:c.commandId,status:'offline',errorCode:'BLE_OFFLINE'});
  }};
  const result=await (await instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'})).json();
  expect(result.errorCode).toBe('BLE_OFFLINE');expect(await state.storage.get('revoked')).toBe(false);
 });
});
it('Tunnel keeps revoked state if rotated credential is rejected by router session endpoint',async()=>{
 const stub=bindings.HUE_RELAY.getByName('tunnel-key-rotation-failed');
 await runInDurableObject(stub,async(instance:any,state)=>{
  await state.storage.put({revoked:true,revokedFingerprint:'old-fingerprint'});
  instance.keyFingerprint=Promise.resolve('new-fingerprint');
  instance.env.HUE_TUNNEL={fetch:async()=>new Response(null,{status:401})};
  const first=await (await instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'})).json();
  expect(first.errorCode).toBe('TUNNEL_OFFLINE');
  expect(await state.storage.get('revoked')).toBe(true);
  instance.keyFingerprint=Promise.resolve('old-fingerprint');
  const rollback=await (await instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'})).json();
  expect(rollback.errorCode).toBe('AGENT_REVOKED');
 });
});

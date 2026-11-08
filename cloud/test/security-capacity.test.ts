import {env} from 'cloudflare:workers';
import {runInDurableObject} from 'cloudflare:test';
import {it,expect} from 'vitest';
import {CommandQueue} from '../src/command-queue';
it('invalid flow requests do not spend legitimate consent capacity',async()=>{
 const stub=(env as any).HUE_AUTH.getByName(crypto.randomUUID());
 for(let n=0;n<55;n++)await stub.consent({flowId:'missing',csrf:'dummy',setupCode:'A'.repeat(26)});
 const setup=await stub.issueSetup();expect(setup.ok).toBe(true);
 const flow=await stub.createFlow({clientId:'test',redirectUri:'https://redirect.test',state:'state',csrf:'fixture'});
 const result=await stub.consent({flowId:flow.value.flowId,csrf:'fixture',setupCode:setup.value.code});
 expect(result.ok).toBe(true);
});
it('one source cannot occupy all pending OAuth flows',async()=>{
 const stub=(env as any).HUE_AUTH.getByName(crypto.randomUUID());
 for(let n=0;n<4;n++)expect((await stub.createFlow({clientId:'test',redirectUri:'https://redirect.test',state:String(n),csrf:'fixture',source:'a'.repeat(64)})).ok).toBe(true);
 expect((await stub.createFlow({clientId:'test',redirectUri:'https://redirect.test',state:'overflow',csrf:'fixture',source:'a'.repeat(64)})).ok).toBe(false);
 expect((await stub.createFlow({clientId:'test',redirectUri:'https://redirect.test',state:'other',csrf:'fixture',source:'b'.repeat(64)})).ok).toBe(true);
});
it('retired grant receipts and rate counters are reclaimed when the sole owner clears',async()=>{
 const stub=(env as any).HUE_AUTH.getByName(crypto.randomUUID());
 await runInDurableObject(stub,async(_instance,state)=>{
  const q=new CommandQueue(state);const now=Date.now()/1000;
  expect(await q.accept('old','one',[{on:false}],now)).toBe('accepted');
  q.clear();
  expect(state.storage.sql.exec('SELECT key FROM desired_receipts').toArray()).toHaveLength(0);
  expect(state.storage.sql.exec('SELECT id FROM desired_rate').toArray()).toHaveLength(0);
 });
});
it('QUERY history cannot consume persistent write capacity',async()=>{
 const stub=(env as any).HUE_RELAY.getByName(crypto.randomUUID());
 await runInDurableObject(stub,async(instance:any,state)=>{
  instance.env.HUE_TUNNEL={fetch:async(url:string,init:any)=>{
   if(url.endsWith('/v1/session'))return Response.json({sessionId:'fixture',serverTime:Date.now()/1000});
   const c=JSON.parse(init.body);return Response.json({commandId:c.commandId,status:'verified',deviceId:'hue-lwa017-01',on:false,brightness:0,brightnessRaw:254,observedAt:Date.now()/1000});
  }};
  for(let n=0;n<300;n++){
   const r=await instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'});
   expect(r.status).toBe(200);expect((await r.json()).status).toBe('verified');
  }
  const r=await instance.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'set',on:false},{kind:'operator'});
  expect((await r.json()).status).toBe('verified');
  expect(state.storage.sql.exec('SELECT id FROM commands').toArray().length).toBeLessThanOrEqual(65);
 });
});

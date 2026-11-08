import {env} from 'cloudflare:workers';
import {runInDurableObject} from 'cloudflare:test';
import {it,expect} from 'vitest';
import type {AuthStore} from '../src/oauth-store';
async function linked(){
 const stub=((env as any).HUE_AUTH as DurableObjectNamespace<AuthStore>).getByName(crypto.randomUUID());
 const s=await stub.issueSetup();if(!s.ok)throw Error('setup');
 const f=await stub.createFlow({clientId:'client',redirectUri:'https://redirect.test',state:'x',csrf:'csrf'});if(!f.ok)throw Error('flow');
 const c=await stub.consent({flowId:f.value.flowId,csrf:'csrf',setupCode:s.value.code});if(!c.ok)throw Error('consent');
 const t=await stub.exchange({grant_type:'authorization_code',client_id:'client',redirect_uri:'https://redirect.test',code:c.value.code});if(!t.ok)throw Error('token');
 return {stub,identity:(await stub.authenticate(t.value.access_token))!};
}
it('rapid admitted brightness produces one final physical write and verified report',async()=>{
 const {stub,identity}=await linked();
 await runInDurableObject(stub,async(instance:any,state)=>{
  instance.env.GOOGLE_QUEUE_ENABLED='true';instance.env.REPORT_STATE_ENABLED='true';instance.env.HOMEGRAPH_SERVICE_ACCOUNT='fixture';
  const writes:any[]=[],reports:any[]=[];
  instance.env.HUE_RELAY={getByName:()=>({execute:async(c:any)=>{writes.push(c);return Response.json({commandId:c.requestId,status:'verified',deviceId:'hue-lwa017-01',on:true,brightness:35,brightnessRaw:89,observedAt:Date.now()/1000});}})};
  instance.reporter={report:async(_owner:string,s:any)=>reports.push(s)};
  for(const [i,brightness]of [20,70,35].entries())expect(await instance.submitCommands(identity.grantId,String(i),[{brightness}])).toEqual({accepted:true});
  expect(writes).toHaveLength(0);
  await instance.alarm();await state.storage.deleteAlarm();
  expect(writes).toHaveLength(1);expect(writes[0].brightness).toBe(35);
  expect(reports).toEqual([{online:true,on:true,brightness:35}]);expect(await instance.commandStatus()).toMatchObject({pending:0,last:{outcome:'verified'}});
 });
});
it('new work during an in-flight write is merged and never overlaps or replays uncertain delivery',async()=>{
 const {stub,identity}=await linked();
 await runInDurableObject(stub,async(instance:any,state)=>{
  instance.env.GOOGLE_QUEUE_ENABLED='true';instance.env.REPORT_STATE_ENABLED='true';instance.env.HOMEGRAPH_SERVICE_ACCOUNT='fixture';
  const writes:any[]=[];let release!:()=>void,started!:()=>void;const beginning=new Promise<void>(r=>started=r);
  instance.env.HUE_RELAY={getByName:()=>({execute:async(c:any)=>{writes.push(c);if(writes.length===1){started();await new Promise<void>(r=>release=r);throw Error('uncertain');}return Response.json({commandId:c.requestId,status:'verified',deviceId:'hue-lwa017-01',on:true,brightness:60,brightnessRaw:152,observedAt:Date.now()/1000});}})};
  instance.reporter={report:async()=>{}};
  await instance.submitCommands(identity.grantId,'first',[{brightness:20}]);const drain=instance.alarm();await beginning;
  await instance.submitCommands(identity.grantId,'second',[{brightness:40}]);await instance.submitCommands(identity.grantId,'third',[{brightness:60}]);
  release();await drain;await instance.alarm();await state.storage.deleteAlarm();
  expect(writes.map(c=>c.brightness)).toEqual([20,60]);
  expect(await instance.submitCommands(identity.grantId,'first',[{brightness:20}])).toEqual({accepted:true});
  await instance.alarm();await state.storage.deleteAlarm();expect(writes).toHaveLength(2);
 });
});
it('unlink fences pending commands and periodic polling skips a user burst',async()=>{
 const {stub,identity}=await linked();
 await runInDurableObject(stub,async(instance:any,state)=>{
  instance.env.GOOGLE_QUEUE_ENABLED='true';instance.env.REPORT_STATE_ENABLED='true';instance.env.HOMEGRAPH_SERVICE_ACCOUNT='fixture';
  let calls=0;instance.env.HUE_RELAY={getByName:()=>({execute:async()=>{calls++;throw Error('must not execute');},revokeOAuthGrant:async()=>{}})};
  await instance.submitCommands(identity.grantId,'first',[{on:false}]);await instance.pollReport();expect(calls).toBe(0);
  await instance.disconnect(identity.grantId);await instance.alarm();await state.storage.deleteAlarm();expect(calls).toBe(0);
  expect(await instance.submitCommands(identity.grantId,'late',[{on:true}])).toMatchObject({accepted:false,errorCode:'AGENT_REVOKED'});
 });
});

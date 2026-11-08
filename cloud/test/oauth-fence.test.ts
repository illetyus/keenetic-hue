import {env} from 'cloudflare:workers';
import {runInDurableObject} from 'cloudflare:test';
import {expect,it} from 'vitest';
import type {Env} from '../src/index';
import type {AuthStore} from '../src/oauth-store';

const bindings=env as unknown as Env;
function gate(){let resolve!:()=>void;const promise=new Promise<void>(r=>resolve=r);return {promise,resolve};}

for(const heldPhase of ['session','command'] as const){
 it(`durable queued work never resumes after unlink during the ${heldPhase} phase`,async()=>{
  const auth=bindings.HUE_AUTH.getByName(crypto.randomUUID()) as DurableObjectStub<AuthStore>;
  const unwrap=(out:any)=>{if(!out.ok)throw Error(out.error);return out.value;};
  const setup=unwrap(await auth.issueSetup());
  const flow=unwrap(await auth.createFlow({clientId:'client',redirectUri:'https://redirect.test',state:'x',csrf:'fixture'}));
  const approval=unwrap(await auth.consent({flowId:flow.flowId,csrf:'fixture',setupCode:setup.code}));
  const tokens=unwrap(await auth.exchange({grant_type:'authorization_code',client_id:'client',redirect_uri:'https://redirect.test',code:approval.code}));
  const identity=(await auth.authenticate(tokens.access_token))!;
  const relayName=crypto.randomUUID(),relay=bindings.HUE_RELAY.getByName(relayName);
  const entered=gate(),release=gate();let hold=true,writes=0;
  await runInDurableObject(relay,async(relayInstance:any)=>{
   relayInstance.env={...relayInstance.env};
   relayInstance.env.HUE_TUNNEL={fetch:async(url:string,init:any)=>{
    const session=url.endsWith('/v1/session');
    if(!session)writes++;
    if(hold&&session===(heldPhase==='session')){hold=false;entered.resolve();await release.promise;}
    if(session)return Response.json({sessionId:'combined',serverTime:Date.now()/1000});
    const c=JSON.parse(init.body);
    return Response.json({commandId:c.commandId,status:'verified',deviceId:'hue-lwa017-01',on:false,brightness:0,brightnessRaw:254,observedAt:Date.now()/1000});
   }};
  });
  try{await runInDurableObject(auth,async(instance:any,state)=>{
    instance.env={...instance.env};
    const actorRelay=instance.env.HUE_RELAY.getByName(relayName);
    instance.env.HUE_RELAY={getByName:()=>actorRelay};
    instance.env.GOOGLE_QUEUE_ENABLED='true';instance.env.REPORT_STATE_ENABLED='true';instance.env.HOMEGRAPH_SERVICE_ACCOUNT='fixture';
    instance.reporter={report:async()=>{}};
    expect(await instance.submitCommands(identity.grantId,'first',[{on:false}])).toEqual({accepted:true});
    expect(await instance.submitCommands(identity.grantId,'second',[{on:true}])).toEqual({accepted:true});
    const drain=instance.alarm();await entered.promise;
    let acknowledged=false;
    const unlink=instance.disconnect(identity.grantId).then(()=>{acknowledged=true;});
    try{
     const late=await actorRelay.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'google',grantId:identity.grantId});
     expect((await late.json()).errorCode).toBe('GRANT_REVOKED');
     expect(acknowledged).toBe(false);
    }finally{release.resolve();await Promise.all([drain,unlink]);await state.storage.deleteAlarm();}
    expect(acknowledged).toBe(true);
    await instance.alarm();await instance.alarm();await state.storage.deleteAlarm();
    expect(writes).toBe(heldPhase==='session'?0:1);
    expect(await instance.commandStatus()).toMatchObject({pending:0,running:false});
    expect(await instance.submitCommands(identity.grantId,'late',[{on:true}])).toMatchObject({accepted:false,errorCode:'AGENT_REVOKED'});
    expect(await instance.authenticate(tokens.access_token)).toBeNull();
   });}finally{release.resolve();}
 });
}

for(const heldPhase of ['session','command'] as const){
 it(`OAuth unlink waits for a held ${heldPhase} and rejects later work from the grant`,async()=>{
  const stub=bindings.HUE_RELAY.getByName(crypto.randomUUID());
  await runInDurableObject(stub,async(instance:any,state)=>{
   instance.env={...instance.env};
   const entered=gate(),release=gate();let writes=0;let hold=true;
   instance.env.HUE_TUNNEL={fetch:async(url:string,init:any)=>{
    const session=url.endsWith('/v1/session');
    if(!session)writes++;
    if(hold&&session===(heldPhase==='session')){hold=false;entered.resolve();await release.promise;}
    if(session)return Response.json({sessionId:'fixture',serverTime:Date.now()/1000});
    const c=JSON.parse(init.body);
    return Response.json({commandId:c.commandId,status:'verified',deviceId:'hue-lwa017-01',on:false,brightness:0,brightnessRaw:254,observedAt:Date.now()/1000});
   }};
   const grantId=crypto.randomUUID();
   const input=()=>({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'});
   const pending=instance.execute(input(),{kind:'google',grantId});
   await entered.promise;
   let acknowledged=false;
   const revoked=instance.revokeOAuthGrant(grantId).then(()=>{acknowledged=true;});
   try{
    await Promise.resolve();
    expect(acknowledged).toBe(false);
    expect(state.storage.sql.exec('SELECT grant_id FROM revoked_oauth_grants WHERE grant_id=?',grantId).toArray()).toHaveLength(1);
    const late=await instance.execute(input(),{kind:'google',grantId});
    expect((await late.json()).errorCode).toBe('GRANT_REVOKED');
   }finally{release.resolve();await Promise.all([pending,revoked]);}
   expect(acknowledged).toBe(true);
   expect(writes).toBe(heldPhase==='session'?0:1);
   expect((await (await instance.execute(input(),{kind:'google',grantId})).json()).errorCode).toBe('GRANT_REVOKED');
   // Revoking a Google grant must leave the separate operator path usable.
   expect((await (await instance.execute(input(),{kind:'operator'})).json()).status).toBe('verified');
   expect(writes).toBe(heldPhase==='session'?1:2);
  });
 });
}

it('an overlapping stale consent cannot replace the newer grant or its tokens',async()=>{
 const stub=bindings.HUE_AUTH.getByName(crypto.randomUUID());
 await runInDurableObject(stub,async(instance:any)=>{
  instance.env={...instance.env};
  const unwrap=(out:any)=>{if(!out.ok)throw Error(out.error);return out.value;};
  const prepare=async()=>{
   const setup=unwrap(await instance.issueSetup());
   const flow=unwrap(await instance.createFlow({clientId:'client',redirectUri:'https://redirect.test',state:'fixture',csrf:'fixture-csrf'}));
   return {flowId:flow.flowId,csrf:'fixture-csrf',setupCode:setup.code};
  };
  const exchange=async(code:string)=>unwrap(await instance.exchange({grant_type:'authorization_code',client_id:'client',redirect_uri:'https://redirect.test',code}));
  const original=await exchange(unwrap(await instance.consent(await prepare())).code);
  const entered=gate(),release=gate();let calls=0;
  instance.env.HUE_RELAY={getByName:()=>({revokeOAuthGrant:async()=>{if(++calls===1){entered.resolve();await release.promise;}}})};
  const stale=instance.consent(await prepare());
  await entered.promise;
  let newer:any;
  try{newer=unwrap(await instance.consent(await prepare()));}
  finally{release.resolve();}
  expect(await stale).toEqual({ok:false,error:'temporarily_unavailable'});
  const tokens=await exchange(newer.code);
  expect(await instance.authenticate(tokens.access_token)).toMatchObject({grantId:expect.any(String)});
  expect(await instance.authenticate(original.access_token)).toBeNull();
  expect(await instance.exchange({grant_type:'refresh_token',client_id:'client',refresh_token:tokens.refresh_token})).toMatchObject({ok:true});
 });
});

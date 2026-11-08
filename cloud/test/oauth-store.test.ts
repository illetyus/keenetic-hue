import {env} from 'cloudflare:workers';
import {runInDurableObject} from 'cloudflare:test';
import {expect,it} from 'vitest';
import type {AuthStore} from '../src/oauth-store';
import type {Outcome} from '../src/oauth-store';
async function value<T>(p:Promise<Outcome<T>>):Promise<T>{const r=await p;if(!r.ok)throw Error(r.error);return r.value;}
const auth=()=>((env as any).HUE_AUTH as DurableObjectNamespace<AuthStore>).getByName(crypto.randomUUID());
const clientId='test-google-client';const redirectUri='https://oauth-redirect.googleusercontent.com/r/test-project';
async function link(a:DurableObjectStub<AuthStore>){
 const setup=await value(a.issueSetup());const flow=await value(a.createFlow({clientId,redirectUri,state:'state',csrf:'csrf-fixture'}));
 return value(a.consent({flowId:flow.flowId,csrf:'csrf-fixture',setupCode:setup.code}));
}
const exchange=(a:DurableObjectStub<AuthStore>,code:string)=>value(a.exchange({grant_type:'authorization_code',client_id:clientId,code,redirect_uri:redirectUri}));
it('consumes setup and authorization codes once, refreshes without rotating refresh token',async()=>{
 const a=auth();const approval=await link(a);const tokens=await exchange(a,approval.code);
 expect(tokens.expires_in).toBe(3600);expect(tokens.refresh_token).toBeTruthy();
 expect((await a.linkStatus())?.state).toEqual({setupPresent:false,pendingCodes:0,activeGrants:1,accessTokens:1,refreshTokens:1});
 expect(await a.authenticate(tokens.access_token)).toMatchObject({grantId:expect.any(String),ownerId:expect.any(String)});
 await expect(exchange(a,approval.code)).rejects.toThrow('invalid_grant');
 const refreshed=await value(a.exchange({grant_type:'refresh_token',client_id:clientId,refresh_token:tokens.refresh_token!}));
 expect(refreshed.refresh_token).toBeUndefined();expect(await a.authenticate(refreshed.access_token)).toBeTruthy();
});
it('concurrent exchange yields one token set and stores no plaintext secret',async()=>{
 const a=auth();const approval=await link(a);
 const settled=await Promise.allSettled([exchange(a,approval.code),exchange(a,approval.code)]);
 expect(settled.filter(r=>r.status==='fulfilled')).toHaveLength(1);
 const tokens=(settled.find(r=>r.status==='fulfilled') as PromiseFulfilledResult<any>).value;
 await runInDurableObject(a,async(_instance,state)=>{
   const rows=state.storage.sql.exec('SELECT * FROM oauth_tokens').toArray();
   const raw=JSON.stringify(rows);expect(raw).not.toContain(tokens.access_token);expect(raw).not.toContain(tokens.refresh_token);
 });
});
it('relink revokes old grant and late disconnect cannot revoke new grant',async()=>{
 const a=auth();const one=await exchange(a,(await link(a)).code);const old=await a.authenticate(one.access_token);
 const two=await exchange(a,(await link(a)).code);const current=await a.authenticate(two.access_token);
 expect(await a.authenticate(one.access_token)).toBeNull();await a.disconnect(old!.grantId);
 expect(await a.authenticate(two.access_token)).toEqual(current);
 expect(await a.exchange({grant_type:'refresh_token',client_id:clientId,refresh_token:one.refresh_token!})).toEqual({ok:false,error:'invalid_grant'});
});
it('expired setup/access, mismatched CSRF and wrong client never authorize',async()=>{
 const a=auth();const setup=await value(a.issueSetup());const flow=await value(a.createFlow({clientId,redirectUri,state:'x',csrf:'right'}));
 expect(await a.consent({flowId:flow.flowId,csrf:'wrong',setupCode:setup.code})).toEqual({ok:false,error:'invalid_grant'});
 expect(await a.linkStatus()).toMatchObject({reason:'csrf_mismatch',at:expect.any(Number)});
 await runInDurableObject(a,async(_i,s)=>{s.storage.sql.exec('UPDATE oauth_setup SET expires=0');});
 expect(await a.consent({flowId:flow.flowId,csrf:'right',setupCode:setup.code})).toEqual({ok:false,error:'invalid_grant'});
 expect(await a.linkStatus()).toMatchObject({reason:'setup_expired'});
 const tokens=await exchange(a,(await link(a)).code);
 expect(await a.exchange({grant_type:'refresh_token',client_id:'other',refresh_token:tokens.refresh_token!})).toEqual({ok:false,error:'invalid_grant'});
 await runInDurableObject(a,async(_i,s)=>{s.storage.sql.exec("UPDATE oauth_tokens SET expires=0 WHERE kind='access'");});
 expect(await a.authenticate(tokens.access_token)).toBeNull();
});

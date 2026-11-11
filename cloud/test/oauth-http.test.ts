import {exports,env} from 'cloudflare:workers';
import {it,expect} from 'vitest';
import {limitedText} from '../src/oauth-http';
import {issueTicket} from '../src/oauth-ticket';
import {runInDurableObject} from 'cloudflare:test';
import {vi} from 'vitest';
const origin='https://relay.test';
const redirect='https://oauth-redirect.googleusercontent.com/r/test-project';
const client='test-google-client';
const fetcher=(path:string,init?:RequestInit)=>exports.default.fetch(new Request(origin+path,{redirect:'manual',...init}));
const authPath=(changes:Record<string,string>={})=>'/oauth/authorize?'+new URLSearchParams({client_id:client,redirect_uri:redirect,state:'original-state',response_type:'code',...changes});
const form=(data:Record<string,string>,headers:Record<string,string>={})=>({method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded',...headers},body:new URLSearchParams(data).toString()});
it('rejects unknown redirect/client and duplicate query before issuing a flow',async()=>{
 expect((await fetcher(authPath({redirect_uri:redirect+'.evil'}))).status).toBe(400);
 expect((await fetcher(authPath({client_id:'wrong'}))).status).toBe(400);
 expect((await fetcher(authPath()+'&state=second')).status).toBe(400);
});
it('authorizes with setup code, strict same-origin form and exchanges code once',async()=>{
 expect((await fetcher('/admin/link-status',{method:'POST'})).status).toBe(401);
 expect((await fetcher('/admin/command-status',{method:'POST'})).status).toBe(401);
 expect((await fetcher('/admin/report-check',{method:'POST'})).status).toBe(401);
 expect((await fetcher('/admin/report-status',{method:'POST'})).status).toBe(401);
 expect((await fetcher('/admin/setup-code',{method:'POST'})).status).toBe(401);
 const setup=await (await fetcher('/admin/setup-code',{method:'POST',headers:{Authorization:'Bearer '+(env as any).SETUP_ISSUER_TOKEN}})).json() as any;
 const page=await fetcher(authPath());const html=await page.text();
 expect(page.headers.get('Referrer-Policy')).toBe('same-origin');
 expect(page.headers.get('Content-Security-Policy')!.split(';').map(x=>x.trim()).find(x=>x.startsWith('form-action '))).toBe("form-action 'self' "+redirect);
 expect(page.headers.get('Cache-Control')).toBe('no-store');expect(page.headers.get('Content-Security-Policy')).toContain("frame-ancestors 'none'");
 const cookie=page.headers.get('Set-Cookie')!.split(';')[0];const flow=html.match(/name="flowId" value="([^"]+)"/)![1];
 const data={flowId:flow,setupCode:setup.code,decision:'link'};
 expect((await fetcher('/oauth/authorize',form(data,{Origin:'null',Cookie:cookie}))).status).toBe(400);
 expect((await fetcher('/oauth/authorize',form(data,{Origin:origin}))).status).toBe(400);
 const diagnostic=await fetcher('/admin/link-status',{method:'POST',headers:{Authorization:'Bearer '+(env as any).OPERATOR_TOKEN}});
 expect(diagnostic.headers.get('Cache-Control')).toBe('no-store');
 expect(await diagnostic.json()).toEqual({reason:'cookie_missing',at:expect.any(Number),state:{setupPresent:true,pendingCodes:0,activeGrants:0,accessTokens:0,refreshTokens:0}});
 expect((await fetcher('/oauth/authorize',form(data,{Origin:'https://evil.test',Cookie:cookie}))).status).toBe(400);
 const changed=flow.replace(/^./,char=>char==='A'?'B':'A');
 expect((await fetcher('/oauth/authorize',form({...data,flowId:changed},{Origin:origin,Cookie:cookie}))).status).toBe(400);
 expect((await fetcher('/oauth/authorize',form(data,{Origin:origin,Cookie:'__Host-hue-flow='+'A'.repeat(43)}))).status).toBe(400);
 const responses=await Promise.all([1,2].map(()=>fetcher('/oauth/authorize',form(data,{Origin:origin,Cookie:cookie}))));
 expect(responses.map(r=>r.status).sort()).toEqual([303,400]);
 const approved=responses.find(r=>r.status===303)!;
 const target=new URL(approved.headers.get('Location')!);expect(target.origin+target.pathname).toBe(redirect);expect(target.searchParams.get('state')).toBe('original-state');
 const tokenData={client_id:client,client_secret:'test-client-secret-cccccccccccccccccccccccc',grant_type:'authorization_code',code:target.searchParams.get('code')!,redirect_uri:redirect};
 expect((await fetcher('/oauth/token',form({...tokenData,client_secret:'wrong'}))).status).toBe(401);
 const response=await fetcher('/oauth/token',form(tokenData));expect(response.status).toBe(200);expect((await response.json() as any).expires_in).toBe(3600);
 expect((await fetcher('/oauth/token',form(tokenData))).status).toBe(400);
 expect((await fetcher('/oauth/authorize',form(data,{Origin:origin,Cookie:cookie}))).status).toBe(400);
});
it('distributed authorization page views leave shared flow capacity available',async()=>{
 for(let n=0;n<132;n++){
  const page=await fetcher(authPath(),{headers:{'CF-Connecting-IP':'192.0.2.'+(n+1)}});
  expect(page.status).toBe(200);
  await page.text();
 }
 const stub=(env as any).HUE_AUTH.getByName('owner');
 await runInDurableObject(stub,async(_instance,state)=>{
  expect(state.storage.sql.exec('SELECT id FROM oauth_flows').toArray()).toHaveLength(0);
  expect(state.storage.sql.exec('SELECT id FROM oauth_flow_sources').toArray()).toHaveLength(0);
 });
});
it('rejects an expired signed form even with the original cookie',async()=>{
 const csrf='A'.repeat(43);
 const clock=vi.spyOn(Date,'now').mockReturnValue(Date.now()-601000);
 let ticket:string;
 try {
  ticket=await issueTicket((env as any).SETUP_ISSUER_TOKEN,csrf,{client,redirect,state:'expired'});
 } finally {
  clock.mockRestore();
 }
 const response=await fetcher('/oauth/authorize',form({flowId:ticket,decision:'cancel'},{Origin:origin,Cookie:'__Host-hue-flow='+csrf}));
 expect(response.status).toBe(400);
});
it('bounds an incomplete incoming request body in time',async()=>{
 const stream=new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('partial'));}});
 await expect(limitedText(new Request(origin+'/oauth/token',{method:'POST',body:stream}))).rejects.toThrow('invalid_request');
},6000);
it('rejects trailing whitespace and control characters in client_secret cleanly and rejects invalid UTF-8 with 400',async()=>{
 const base={client_id:client,grant_type:'refresh_token',refresh_token:'A'.repeat(43)};
 expect((await fetcher('/oauth/token',form({...base,client_secret:'test-client-secret-cccccccccccccccccccccccc '}))).status).toBe(401);
 expect((await fetcher('/oauth/token',form({...base,client_secret:'test-client-secret-cccccccccccccccccccccccc\n'}))).status).toBe(401);
 expect((await fetcher('/oauth/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new Uint8Array([0xff,0xfe])})).status).toBe(400);
});

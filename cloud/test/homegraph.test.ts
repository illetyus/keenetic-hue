import {it,expect} from 'vitest';
import {HomeGraph} from '../src/homegraph';
function b64(bytes:Uint8Array){return btoa(String.fromCharCode(...bytes));}
it('signs a scoped one-hour JWT, caches token, and reports complete lamp state',async()=>{
 const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
 const pk=await crypto.subtle.exportKey('pkcs8',pair.privateKey);
 const credential={client_email:'fixture@test-project.iam.gserviceaccount.com',private_key:'-----BEGIN PRIVATE KEY-----\n'+b64(new Uint8Array(pk))+'\n-----END PRIVATE KEY-----'};
 const calls:any[]=[];
 const fake=async(url:any,init:any)=>{
  expect(init.redirect).toBe('manual');
  calls.push({url:String(url),init});
  if(String(url)==='https://oauth2.googleapis.com/token'){
   const assertion=new URLSearchParams(init.body).get('assertion')!;const [head,body,signature]=assertion.split('.');
   const decode=(s:string)=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
   const claims=JSON.parse(new TextDecoder().decode(decode(body)));
   expect(claims.scope).toBe('https://www.googleapis.com/auth/homegraph');expect(claims.exp-claims.iat).toBe(3600);
   expect(await crypto.subtle.verify('RSASSA-PKCS1-v1_5',pair.publicKey,decode(signature),new TextEncoder().encode(head+'.'+body))).toBe(true);
   return Response.json({access_token:'fixture-access',expires_in:3600});
  }
  expect(init.headers.Authorization).toBe('Bearer fixture-access');
  if(String(url).endsWith('/devices:query'))return Response.json({payload:{devices:{'hue-lwa017-01':{online:true,on:false,brightness:0}}}});
  return Response.json({});
 };
 const api=new HomeGraph(JSON.stringify(credential),fake as typeof fetch);
 expect(await api.checkAuthentication()).toBe(true);
 await api.report('owner',{online:true,on:false,brightness:0});await api.requestSync('owner');
 expect(calls.filter(c=>c.url==='https://oauth2.googleapis.com/token')).toHaveLength(1);
 const report=JSON.parse(calls[1].init.body);expect(report.payload.devices.states['hue-lwa017-01']).toEqual({online:true,on:false,brightness:0});
 expect(await api.query('owner')).toEqual({online:true,on:false,brightness:0});
 expect(JSON.parse(calls.at(-1).init.body)).toMatchObject({agentUserId:'owner',inputs:[{payload:{devices:[{id:'hue-lwa017-01'}]}}]});
});

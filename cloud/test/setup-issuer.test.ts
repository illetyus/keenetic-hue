import {exports,env} from 'cloudflare:workers';
import {it,expect} from 'vitest';
const origin='https://relay.test';
it('operator credentials cannot create OAuth setup codes',async()=>{
 const response=await exports.default.fetch(new Request(origin+'/admin/setup-code',{method:'POST',headers:{Authorization:'Bearer '+(env as any).OPERATOR_TOKEN}}));
 expect(response.status).toBe(401);
});
it('setup issuer can issue a code but has no other operator or device authority',async()=>{
 const token='test-setup-issuer-dddddddddddddddddddddddddddd';
 const call=(path:string)=>exports.default.fetch(new Request(origin+path,{method:'POST',headers:{Authorization:'Bearer '+token}}));
 const response=await call('/admin/setup-code');expect(response.status).toBe(200);
 const value=await response.json() as any;expect(value.code).toMatch(/^[A-Z2-7]{26}$/);expect(value.expiresIn).toBe(600);
 for(const path of ['/control','/admin/revoke-agent','/admin/report-check','/admin/google-status','/admin/command-status'])expect((await call(path)).status).toBe(401);
});

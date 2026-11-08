import { env, exports } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import { describe,it,expect } from 'vitest';
import type { Env } from '../src/index';

const bindings=env as unknown as Env;
const operator='Bearer test-operator-token-bbbbbbbbbbbbbbbbbbbb';
const device='Bearer test-device-token-aaaaaaaaaaaaaaaaaaaaaaaa';
const call=(path:string,init?:RequestInit)=>exports.default.fetch(new Request('https://relay.test'+path,init));
const input=(id=crypto.randomUUID())=>({requestId:id,deviceId:'hue-lwa017-01',operation:'query'});
const control=(data:any)=>call('/control',{method:'POST',headers:{Authorization:operator},body:JSON.stringify(data)});
const receive=(ws:WebSocket):Promise<any>=>new Promise(resolve=>ws.addEventListener('message',e=>resolve(JSON.parse(String(e.data))),{once:true}));
async function connect(){
 const response=await call('/agent',{headers:{Authorization:device,Upgrade:'websocket'}});
 const ws=response.webSocket!;const first=receive(ws);ws.accept();const hello=await first;
 const ack=receive(ws);ws.send(JSON.stringify({v:1,type:'ready',sessionId:hello.sessionId,agentSessionId:crypto.randomUUID(),ready:true,clientTime:Date.now()/1000}));await ack;return {ws,hello};
}
const stub=()=>bindings.HUE_RELAY.getByName('hue-lwa017-01');
describe('relay failures do not become successful physical commands',()=>{
 it('rejects unknown fields and oversized control input',async()=>{
  expect((await control({...input(),expiresAt:1e100})).status).toBe(400);
  expect((await control({...input(),padding:'x'.repeat(5000)})).status).toBe(400);
 });
 it('connection replacement sends subsequent command only to the new session',async()=>{
  const first=await connect();const second=await connect();
  const next=receive(second.ws);const data=input();const pending=control(data);const packet=await next;
  expect(packet.sessionId).not.toBe(first.hello.sessionId);
  second.ws.send(JSON.stringify({v:1,type:'result',sessionId:second.hello.sessionId,commandId:data.requestId,result:{commandId:data.requestId,status:'offline',errorCode:'BLE_OFFLINE'}}));
  expect((await (await pending).json() as any).status).toBe('offline');second.ws.close(1000);
 });
 it('timeout persists unknown; later retry does not send again',async()=>{
  const {ws}=await connect();const next=receive(ws);const data=input();const pending=control(data);await next;
  const result=await (await pending).json() as any;expect(result.errorCode).toBe('OUTCOME_UNKNOWN');
  expect(await (await control(data)).json()).toEqual(result);ws.close(1000);
 });
 it('crash-style pending reservation remains unknown rather than being resent',async()=>{
  const data=input();await runInDurableObject(stub(),async(_instance,state)=>{
    state.storage.sql.exec('INSERT INTO commands VALUES (?,?,?,NULL,?,?)',data.requestId,JSON.stringify([data.deviceId,data.operation,null,null]),'{}',Date.now()/1000,'gone');
  });
  expect((await (await control(data)).json() as any).errorCode).toBe('OUTCOME_UNKNOWN');
 });
 it('capacity preserves unexpired deduplication records and refuses the 257th',async()=>{
  const data=input();const result={commandId:data.requestId,status:'offline',errorCode:'BLE_OFFLINE'};
  await runInDurableObject(stub(),async(_instance,state)=>{
    state.storage.sql.exec('DELETE FROM commands');
    for(let i=0;i<256;i++)state.storage.sql.exec('INSERT INTO commands VALUES (?,?,?,?,?,?)',i===0?data.requestId:crypto.randomUUID(),JSON.stringify([data.deviceId,data.operation,null,null]),'{}',JSON.stringify(result),Date.now()/1000,'gone');
  });
  expect((await control({...input(),operation:'set',on:false})).status).toBe(503);
  expect(await (await control(data)).json()).toEqual(result);
  expect((await control({...data,operation:'set',on:true})).status).toBe(409);
 });
});

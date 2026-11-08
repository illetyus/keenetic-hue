import { env, exports } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import { describe, it, expect } from 'vitest';
import type { Env } from '../src/index';

const bindings=env as unknown as Env;
const operator='Bearer test-operator-token-bbbbbbbbbbbbbbbbbbbb';
const device='Bearer test-device-token-aaaaaaaaaaaaaaaaaaaaaaaa';
const fetchWorker=(path:string,init?:RequestInit)=>exports.default.fetch(new Request('https://relay.test'+path,init));
const input=(id=crypto.randomUUID())=>({requestId:id,deviceId:'hue-lwa017-01',operation:'query'});
const control=(data:any)=>fetchWorker('/control',{method:'POST',headers:{Authorization:operator,'Content-Type':'application/json'},body:JSON.stringify(data)});
function message(ws:WebSocket):Promise<any>{return new Promise(resolve=>ws.addEventListener('message',e=>resolve(JSON.parse(String(e.data))),{once:true}));}
async function connect(){
  const response=await fetchWorker('/agent',{headers:{Authorization:device,Upgrade:'websocket'}});
  expect(response.status).toBe(101);
  const ws=response.webSocket!; const helloP=message(ws);ws.accept(); const hello=await helloP;
  const ack=message(ws);ws.send(JSON.stringify({v:1,type:'ready',sessionId:hello.sessionId,agentSessionId:crypto.randomUUID(),ready:true,clientTime:Date.now()/1000}));
  await ack;return {ws,hello};
}
describe('authenticated real Worker/SQLite DO',()=>{
  it('rejects unauthenticated and cross-role requests before upgrade',async()=>{
    expect((await fetchWorker('/agent',{headers:{Upgrade:'websocket'}})).status).toBe(401);
    expect((await fetchWorker('/agent',{headers:{Authorization:operator,Upgrade:'websocket'}})).status).toBe(401);
    expect((await fetchWorker('/control',{method:'POST',headers:{Authorization:device},body:'{}'})).status).toBe(401);
    expect((await fetchWorker('/admin/revoke-agent',{method:'POST',headers:{Authorization:device}})).status).toBe(401);
  });
  it('reports offline without fabricating lamp state',async()=>{
    const data=await (await control(input())).json() as any;
    expect(data.status).toBe('offline');expect(data.on).toBeUndefined();
  });
  it('round-trips readback, deduplicates and rejects changed logical payload',async()=>{
    const {ws,hello}=await connect();
    const data=input();const next=message(ws);const pending=control(data);const packet=await next;
    expect(packet.command.sessionId).toBe(hello.sessionId);
    const result={status:'verified',commandId:data.requestId,deviceId:data.deviceId,on:false,brightness:0,brightnessRaw:25,observedAt:Date.now()/1000};
    ws.send(JSON.stringify({v:1,type:'result',sessionId:hello.sessionId,commandId:data.requestId,result}));
    expect(await (await pending).json()).toEqual(result);
    expect(await (await control(data)).json()).toEqual(result);
    expect((await control({...data,operation:'set',on:true})).status).toBe(409);
    ws.close(1000);
  });
  it('revokes a reconstructed heartbeat-only connection and rejects later upgrade',async()=>{
    const {ws}=await connect();
    const stub=bindings.HUE_RELAY.getByName('hue-lwa017-01');
    await runInDurableObject(stub,async(_instance,state)=>{
      expect(state.getWebSockets().length).toBeGreaterThan(0);
      // Read persistent attachments: this verifies reconstruction inputs, not production hibernation timing.
      expect(state.getWebSockets()[0].deserializeAttachment().sessionId).toBeTruthy();
    });
    const closed=new Promise(resolve=>ws.addEventListener('close',resolve,{once:true}));
    expect((await fetchWorker('/admin/revoke-agent',{method:'POST',headers:{Authorization:operator}})).status).toBe(200);
    await closed;
    expect((await fetchWorker('/agent',{headers:{Authorization:device,Upgrade:'websocket'}})).status).toBe(401);
    await runInDurableObject(stub,async(_instance,state)=>expect(await state.storage.get('revoked')).toBe(true));
  });
});

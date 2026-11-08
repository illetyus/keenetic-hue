import { env, exports } from 'cloudflare:workers';
import { runInDurableObject } from 'cloudflare:test';
import {it,expect} from 'vitest';
import {Relay} from '../src/relay';
import type {Env} from '../src/index';
const bindings=env as unknown as Env;
const receive=(ws:WebSocket):Promise<any>=>new Promise(resolve=>ws.addEventListener('message',e=>resolve(JSON.parse(String(e.data))),{once:true}));
it('a changed deployed device secret fences sockets reconstructed from old attachments',async()=>{
  const response=await exports.default.fetch(new Request('https://relay.test/agent',{headers:{Authorization:'Bearer test-device-token-aaaaaaaaaaaaaaaaaaaaaaaa',Upgrade:'websocket'}}));
  const ws=response.webSocket!;const first=receive(ws);ws.accept();const hello=await first;
  const ack=receive(ws);ws.send(JSON.stringify({v:1,type:'ready',sessionId:hello.sessionId,agentSessionId:crypto.randomUUID(),ready:true,clientTime:Date.now()/1000}));await ack;
  try{
    const result=await runInDurableObject(bindings.HUE_RELAY.getByName('hue-lwa017-01'),async(_instance,state)=>{
      // Explicit constructor reconstruction with changed env; no claim about production deployment timing.
      const rebuilt=new Relay(state,{...bindings,DEVICE_TOKEN:'new-device-secret-cccccccccccccccccccccccc'});
      return (await rebuilt.execute({requestId:crypto.randomUUID(),deviceId:'hue-lwa017-01',operation:'query'},{kind:'operator'})).json();
    }) as any;
    expect(result.status).toBe('offline');
  }finally{ws.close(1000);}
});

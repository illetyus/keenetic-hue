import {env} from 'cloudflare:workers';
import {runInDurableObject} from 'cloudflare:test';
import {it,expect} from 'vitest';
import {CommandQueue} from '../src/command-queue';
import type {AuthStore} from '../src/oauth-store';

const store=()=>((env as any).HUE_AUTH as DurableObjectNamespace<AuthStore>).getByName(crypto.randomUUID());
it('coalesces only adjacent waiting brightness and preserves power ordering barriers',async()=>{
 await runInDurableObject(store(),async(_instance,state)=>{
  const q=new CommandQueue(state);const stamp=Date.now()/1000;
  expect(await q.accept('grant','one',[{brightness:20}],stamp)).toBe('accepted');
  expect(await q.accept('grant','two',[{brightness:70}],stamp)).toBe('accepted');
  expect(await q.accept('grant','three',[{brightness:35}],stamp)).toBe('accepted');
  expect(q.status().pending).toBe(1);
  const a=q.claim('grant',stamp)!;expect(a.fields).toEqual({brightness:35});
  expect(a.expires-stamp).toBe(15);
  await q.accept('grant','four',[{brightness:40}],stamp);
  await q.accept('grant','five',[{on:false}],stamp);
  await q.accept('grant','six',[{brightness:60}],stamp);
  expect(q.status().pending).toBe(4);
  q.finish(a.id,'verified',stamp);
  expect(q.claim('grant',stamp)!.fields).toEqual({brightness:40});
 });
});
it('durable receipts prevent retries from restoring superseded brightness',async()=>{
 await runInDurableObject(store(),async(_instance,state)=>{
  let q=new CommandQueue(state);const stamp=Date.now()/1000;
  await q.accept('grant','old',[{brightness:20}],stamp);await q.accept('grant','new',[{brightness:80}],stamp);
  q=new CommandQueue(state);
  expect(await q.accept('grant','old',[{brightness:20}],stamp)).toBe('duplicate');
  expect(await q.accept('grant','old',[{brightness:30}],stamp)).toBe('conflict');
  expect(q.claim('grant',stamp)!.fields).toEqual({brightness:80});
 });
});
it('never replays claimed work after restart and expires waiting work',async()=>{
 await runInDurableObject(store(),async(_instance,state)=>{
  let q=new CommandQueue(state);const stamp=Date.now()/1000;
  await q.accept('grant','one',[{on:true}],stamp);q.claim('grant',stamp);
  q=new CommandQueue(state);q.recover(stamp);
  expect(q.claim('grant',stamp)).toBeNull();expect(q.status().last?.outcome).toBe('OUTCOME_UNKNOWN');
  await q.accept('grant','two',[{brightness:50}],stamp);expect(q.claim('grant',stamp+16)).toBeNull();
  expect(q.status().last?.outcome).toBe('EXPIRED');
 });
});
it('bounds power bursts atomically and discards old grant work',async()=>{
 await runInDurableObject(store(),async(_instance,state)=>{
  const q=new CommandQueue(state);const stamp=Date.now()/1000;
  for(let i=0;i<16;i++)expect(await q.accept('grant',String(i),[{on:Boolean(i%2)}],stamp)).toBe('accepted');
  expect(await q.accept('grant','overflow',[{on:true}],stamp)).toBe('busy');expect(q.status().pending).toBe(16);
  expect(q.claim('new-grant',stamp)).toBeNull();expect(q.status().pending).toBe(0);
 });
});

import { describe, it, expect } from 'vitest';
import fixture from '../../tests/fixtures/cloud-v1.json';
import { controlInput, wireCommand, verifiedResult } from '../src/protocol';

const requestId='11111111-1111-4111-8111-111111111111';
const base={requestId,deviceId:'hue-lwa017-01'};
describe('strict cross-language protocol',()=>{
  it('shares operation semantics with Python',()=>{
    for(const c of fixture.valid){ const {expectedRaw,...fields}=c as any; expect(()=>controlInput({...base,...fields})).not.toThrow(); }
    for(const c of fixture.invalid) expect(()=>controlInput({...base,...c})).toThrow();
  });
  it('rejects expiry or sequence supplied by external callers',()=>{
    expect(()=>controlInput({...base,operation:'query',expiresAt:9e99})).toThrow();
    expect(()=>wireCommand({...base,operation:'query'},'s',2**53,100)).toThrow();
    expect(wireCommand({...base,operation:'query'},'s',1,100).expiresAt).toBe(105);
  });
  it('does not accept malformed/fabricated successful states',()=>{
    expect(()=>verifiedResult({status:'verified',commandId:requestId,on:true}, requestId)).toThrow();
    expect(()=>verifiedResult({status:'verified',commandId:requestId,deviceId:'hue-lwa017-01',on:1,brightness:40,brightnessRaw:102,observedAt:Date.now()/1000},requestId)).toThrow();
    expect(()=>verifiedResult({status:'verified',commandId:requestId,deviceId:'hue-lwa017-01',on:true,brightness:100,brightnessRaw:1,observedAt:Date.now()/1000},requestId)).toThrow();
  });
  it('supports bounded cached maxAgeSeconds without relaxing future clock skew',()=>{
    const now=Date.now()/1000;
    const old={status:'verified',commandId:requestId,deviceId:'hue-lwa017-01',on:true,brightness:40,brightnessRaw:102,observedAt:now-20};
    expect(()=>verifiedResult(old,requestId)).toThrow();
    expect(verifiedResult(old,requestId,30).observedAt).toBe(old.observedAt);
    const future={...old,observedAt:now+15};
    expect(()=>verifiedResult(future,requestId,1800)).toThrow();
  });
});

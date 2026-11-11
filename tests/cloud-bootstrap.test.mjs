import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {validateIssuer, provisionIssuer, resolveProvisionTarget} from '../tools/cloud-bootstrap.mjs';

test('issuer must be exactly 32-byte lowercase hex',()=>{
  assert.equal(validateIssuer('a'.repeat(64)),true);
  for(const value of ['', 'a'.repeat(63), 'x'.repeat(64), 'A'.repeat(64), 'a'.repeat(64)+'\n']){
    assert.equal(validateIssuer(value),false);
  }
});

test('secret goes to stdin only and is absent from process arguments',async()=>{
  const token='b'.repeat(64);
  let written;
  const fakeSpawn=(executable,args,options)=>{
    assert.ok(!JSON.stringify(args).includes(token));
    assert.equal(options.shell,false);
    const child=new EventEmitter();
    child.stdout=new EventEmitter();child.stderr=new EventEmitter();
    child.stdin={end(value){written=value;queueMicrotask(()=>child.emit('close',0));}};
    return child;
  };
  await provisionIssuer(token,'fixture/wrangler.js',fakeSpawn);
  assert.equal(written,token+'\n');
});

test('child error details never leak secrets',async()=>{
  const token='c'.repeat(64);
  const fakeSpawn=()=>{
    const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();
    child.stdin={end(){queueMicrotask(()=>child.emit('error',new Error(token)));}};
    return child;
  };
  await assert.rejects(provisionIssuer(token,'fixture/wrangler.js',fakeSpawn),error=>!String(error).includes(token));
});

test('provisionIssuer supports explicit wrangler config and rejects unsafe config names',async()=>{
  const token='d'.repeat(64);
  let capturedArgs;
  const fakeSpawn=(_executable,args)=>{
    capturedArgs=args;
    const child=new EventEmitter();
    child.stdout=new EventEmitter();child.stderr=new EventEmitter();
    child.stdin={end(){queueMicrotask(()=>child.emit('close',0));}};
    return child;
  };
  await provisionIssuer(token,'fixture/wrangler.js',fakeSpawn,'wrangler.personal.jsonc');
  assert.deepEqual(capturedArgs,['fixture/wrangler.js','secret','put','SETUP_ISSUER_TOKEN','--config','wrangler.personal.jsonc']);
  await assert.rejects(provisionIssuer(token,'fixture/wrangler.js',fakeSpawn,'../escape.jsonc'),/INVALID_WRANGLER_CONFIG/);
  assert.equal(resolveProvisionTarget(['provision-issuer'],()=>false),undefined);
  assert.equal(resolveProvisionTarget(['provision-issuer'],name=>name==='wrangler.personal.jsonc'),'wrangler.personal.jsonc');
  assert.equal(resolveProvisionTarget(['provision-issuer','--config','wrangler.personal.jsonc'],name=>name==='wrangler.personal.jsonc'),'wrangler.personal.jsonc');
  assert.throws(()=>resolveProvisionTarget(['provision-issuer','--config','../bad.jsonc'],()=>true),/INVALID_WRANGLER_CONFIG/);
  assert.throws(()=>resolveProvisionTarget(['provision-issuer','--config','missing.jsonc'],()=>false),/WRANGLER_CONFIG_NOT_FOUND/);
});

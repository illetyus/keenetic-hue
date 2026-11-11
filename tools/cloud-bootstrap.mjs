/** Setup-issuer provisioning through the user's installed, pinned Wrangler. */
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {existsSync} from 'node:fs';
import {fileURLToPath, pathToFileURL} from 'node:url';

export const validateIssuer=value=>typeof value==='string' && /^[a-f0-9]{64}$/.test(value);
export const validateConfigName=value=>typeof value==='string' && /^[A-Za-z0-9][A-Za-z0-9._-]*\.jsonc?$/.test(value) && !value.includes('..');

export function resolveProvisionTarget(args,hasConfig=name=>existsSync(fileURLToPath(new URL('../cloud/'+name,import.meta.url)))){
  if(args.length===1 && args[0]==='provision-issuer'){
    return hasConfig('wrangler.personal.jsonc')?'wrangler.personal.jsonc':undefined;
  }
  if(args.length===3 && args[0]==='provision-issuer' && args[1]==='--config'){
    if(!validateConfigName(args[2]))throw new Error('INVALID_WRANGLER_CONFIG');
    if(!hasConfig(args[2]))throw new Error('WRANGLER_CONFIG_NOT_FOUND');
    return args[2];
  }
  throw new Error('INVALID_ARGS');
}

export function provisionIssuer(token,wrangler,launch=spawn,config){
  if(!validateIssuer(token))return Promise.reject(new Error('INVALID_ISSUER'));
  if(config!==undefined && !validateConfigName(config))return Promise.reject(new Error('INVALID_WRANGLER_CONFIG'));
  const target=config?['--config',config]:['--name','keenetic-hue-relay'];
  return new Promise((resolve,reject)=>{
    const child=launch(process.execPath,[wrangler,'secret','put','SETUP_ISSUER_TOKEN',...target],
      {shell:false,stdio:['pipe','pipe','pipe'],cwd:fileURLToPath(new URL('../cloud/',import.meta.url))});
    const failure=()=>reject(new Error('SETUP_ISSUER_PROVISION_FAILED'));
    const timer=setTimeout(()=>{child.kill('SIGTERM');failure();},45000);
    timer.unref();
    child.stdout.on('data',()=>{});child.stderr.on('data',()=>{});
    child.once('error',()=>{clearTimeout(timer);failure();});
    child.once('close',code=>{clearTimeout(timer);code===0?resolve():failure();});
    if(child.stdin.on)child.stdin.on('error',()=>{clearTimeout(timer);failure();});
    child.stdin.end(token+'\n');
  });
}

function hiddenIssuer(){
  const input=process.stdin;
  if(!input.isTTY || !process.stderr.isTTY || typeof input.setRawMode!=='function'){
    return Promise.reject(new Error('PRIVATE_TERMINAL_REQUIRED'));
  }
  process.stderr.write('64 karakterlik kurulum kodu uretici anahtari (giris gizli): ');
  return new Promise((resolve,reject)=>{
    let value='';const previous=!!input.isRaw;
    function finish(error){
      input.off('data',receive);input.setRawMode(previous);input.pause();process.stderr.write('\n');
      error?reject(new Error(error)):resolve(value);
    }
    function receive(data){
      for(const byte of data){
        if(byte===3 || byte===4){finish('CANCELLED');return;}
        if(byte===10 || byte===13){finish(validateIssuer(value)?null:'INVALID_ISSUER');return;}
        if(byte===8 || byte===127){value=value.slice(0,-1);continue;}
        if(value.length>=64 || !/[a-f0-9]/.test(String.fromCharCode(byte))){finish('INVALID_ISSUER');return;}
        value+=String.fromCharCode(byte);
      }
    }
    input.setRawMode(true);input.on('data',receive);input.resume();
  });
}

export async function main(args=process.argv.slice(2)){
  let config;
  const isGenerate=args.length===1 && args[0]==='generate-issuer';
  let valid=isGenerate;
  if(!isGenerate && args[0]==='provision-issuer'){
    try{config=resolveProvisionTarget(args);valid=true;}
    catch(error){
      if(error instanceof Error && ['INVALID_WRANGLER_CONFIG','WRANGLER_CONFIG_NOT_FOUND'].includes(error.message)){
        console.error(error.message);return 2;
      }
    }
  }
  if(!valid){
    console.log('node tools/cloud-bootstrap.mjs generate-issuer|provision-issuer [--config wrangler.personal.jsonc]');
    console.log('Once cloud dizininde npm ci, sonra node node_modules/wrangler/bin/wrangler.js login.');
    return args.includes('--help')?0:2;
  }
  if(!process.stdout.isTTY || !process.stdin.isTTY || !process.stderr.isTTY){
    console.error('PRIVATE_TERMINAL_REQUIRED');return 2;
  }
  if(isGenerate){
    console.log('Yalniz kurulum kodu uretme anahtari; parola yoneticinde sakla:');
    console.log(randomBytes(32).toString('hex'));
    return 0;
  }
  const wrangler=fileURLToPath(new URL('../cloud/node_modules/wrangler/bin/wrangler.js',import.meta.url));
  if(!existsSync(wrangler)){console.error('PINNED_WRANGLER_NOT_INSTALLED: run npm ci in cloud');return 1;}
  try{
    const token=await hiddenIssuer();
    await provisionIssuer(token,wrangler,spawn,config);
    console.log('SETUP_ISSUER_TOKEN guncellendi. Ayni anahtari Keenetic huectl configure-issuer komutuna gir.');
    return 0;
  }catch{
    console.error('SETUP_ISSUER_PROVISION_FAILED: check Wrangler login and account; secret details suppressed');return 1;
  }
}

if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){process.exitCode=await main();}

/** Setup-issuer provisioning through the user's installed, pinned Wrangler. */
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {existsSync} from 'node:fs';
import {fileURLToPath, pathToFileURL} from 'node:url';

export const validateIssuer=value=>typeof value==='string' && /^[a-f0-9]{64}$/.test(value);

export function provisionIssuer(token,wrangler,launch=spawn){
  if(!validateIssuer(token))return Promise.reject(new Error('INVALID_ISSUER'));
  return new Promise((resolve,reject)=>{
    const child=launch(process.execPath,[wrangler,'secret','put','SETUP_ISSUER_TOKEN','--name','keenetic-hue-relay'],
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
  if(args.length!==1 || !['generate-issuer','provision-issuer'].includes(args[0])){
    console.log('node tools/cloud-bootstrap.mjs generate-issuer|provision-issuer');
    console.log('Once cloud dizininde npm ci, sonra node node_modules/wrangler/bin/wrangler.js login.');
    return args.includes('--help')?0:2;
  }
  if(!process.stdout.isTTY || !process.stdin.isTTY || !process.stderr.isTTY){
    console.error('PRIVATE_TERMINAL_REQUIRED');return 2;
  }
  if(args[0]==='generate-issuer'){
    console.log('Yalniz kurulum kodu uretme anahtari; parola yoneticinde sakla:');
    console.log(randomBytes(32).toString('hex'));
    return 0;
  }
  const wrangler=fileURLToPath(new URL('../cloud/node_modules/wrangler/bin/wrangler.js',import.meta.url));
  if(!existsSync(wrangler)){console.error('PINNED_WRANGLER_NOT_INSTALLED: run npm ci in cloud');return 1;}
  try{
    const token=await hiddenIssuer();
    await provisionIssuer(token,wrangler);
    console.log('SETUP_ISSUER_TOKEN guncellendi. Ayni anahtari Keenetic huectl configure-issuer komutuna gir.');
    return 0;
  }catch{
    console.error('SETUP_ISSUER_PROVISION_FAILED: check Wrangler login and account; secret details suppressed');return 1;
  }
}

if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){process.exitCode=await main();}

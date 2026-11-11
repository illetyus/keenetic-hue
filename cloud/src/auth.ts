export async function equalSecrets(presented:string|undefined,expected:string|undefined):Promise<boolean>{
  if(!expected||expected.length<32||!presented||presented.length>505)return false;
  const encode=new TextEncoder();
  const a=new Uint8Array(await crypto.subtle.digest('SHA-256',encode.encode(presented)));
  const b=new Uint8Array(await crypto.subtle.digest('SHA-256',encode.encode(expected)));
  let diff=0;for(let i=0;i<a.length;i++)diff|=a[i]^b[i];return diff===0;
}
export async function authorized(request:Request,token:string|undefined):Promise<boolean>{
  const header=request.headers.get('Authorization');
  if(!header||header.length>512||!header.startsWith('Bearer '))return false;
  return equalSecrets(header.slice(7),token);
}

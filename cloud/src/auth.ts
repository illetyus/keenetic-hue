export async function authorized(request:Request,token:string|undefined):Promise<boolean>{
  const header=request.headers.get('Authorization');
  if(!token||token.length<32||!header||header.length>512||!header.startsWith('Bearer '))return false;
  const encode=new TextEncoder();
  const a=new Uint8Array(await crypto.subtle.digest('SHA-256',encode.encode(header.slice(7))));
  const b=new Uint8Array(await crypto.subtle.digest('SHA-256',encode.encode(token)));
  let diff=0;for(let i=0;i<a.length;i++)diff|=a[i]^b[i];return diff===0;
}

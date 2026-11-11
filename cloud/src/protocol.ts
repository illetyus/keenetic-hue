export const DEVICE_ID='hue-lwa017-01';
export class ProtocolError extends Error {}
export type ControlInput={requestId:string;deviceId:string;operation:'query'|'set';on?:boolean;brightness?:number};
export type Result={commandId:string;status:'verified'|'offline'|'expired'|'error';errorCode?:string;deviceId?:string;on?:boolean;brightness?:number;brightnessRaw?:number;observedAt?:number};
export function object(value:unknown,allowed:string[],required=allowed):Record<string,any>{
  if(value===null||typeof value!=='object'||Array.isArray(value))throw new ProtocolError('INVALID_MESSAGE');
  const data=value as Record<string,any>;
  if(Object.keys(data).some(k=>!allowed.includes(k))||required.some(k=>!Object.hasOwn(data,k)))throw new ProtocolError('INVALID_MESSAGE');
  return data;
}
export function controlInput(value:unknown):ControlInput{
  const d=object(value,['requestId','deviceId','operation','on','brightness'],['requestId','deviceId','operation']);
  if(typeof d.requestId!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(d.requestId)||d.deviceId!==DEVICE_ID)throw new ProtocolError('INVALID_ID');
  if(!['query','set'].includes(d.operation))throw new ProtocolError('INVALID_OPERATION');
  if(Object.hasOwn(d,'on')&&typeof d.on!=='boolean')throw new ProtocolError('INVALID_POWER');
  if(Object.hasOwn(d,'brightness')&&(!Number.isInteger(d.brightness)||d.brightness<0||d.brightness>100))throw new ProtocolError('INVALID_BRIGHTNESS');
  const hasState=Object.hasOwn(d,'on')||Object.hasOwn(d,'brightness');
  if(d.operation==='query'?hasState:!hasState)throw new ProtocolError('INVALID_OPERATION');
  if(Object.hasOwn(d,'on')&&Object.hasOwn(d,'brightness')&&d.on!==(d.brightness>0))throw new ProtocolError('CONFLICTING_STATE');
  return d as ControlInput;
}
export function wireCommand(input:ControlInput,sessionId:string,sequence:number,now:number){
  controlInput(input);
  if(!Number.isSafeInteger(sequence)||sequence<1)throw new ProtocolError('INVALID_SEQUENCE');
  const {requestId,...fields}=input;
  return {commandId:requestId,...fields,sessionId,sequence,expiresAt:now+5};
}
export function logicalKey(input:ControlInput):string{
  return JSON.stringify([input.deviceId,input.operation,input.on??null,input.brightness??null]);
}
export function verifiedResult(value:unknown,id:string,maxAgeSeconds=10):Result{
  const d=object(value,['commandId','status','errorCode','deviceId','on','brightness','brightnessRaw','observedAt'],['commandId','status']);
  if(d.commandId!==id)throw new ProtocolError('INVALID_RESULT');
  if(d.status==='verified'){
    const now=Date.now()/1000;
    if(d.deviceId!==DEVICE_ID||typeof d.on!=='boolean'||!Number.isInteger(d.brightness)||d.brightness<0||d.brightness>100||!Number.isInteger(d.brightnessRaw)||d.brightnessRaw<0||d.brightnessRaw>254||typeof d.observedAt!=='number'||!Number.isFinite(d.observedAt)||d.observedAt>now+10||now-d.observedAt>maxAgeSeconds||Object.hasOwn(d,'errorCode'))throw new ProtocolError('INVALID_RESULT');
    if(!d.on&&d.brightness!==0)throw new ProtocolError('INVALID_RESULT');
    if(d.on&&d.brightness!==Math.round(d.brightnessRaw*100/254))throw new ProtocolError('INVALID_RESULT');
  }else if(!['offline','expired','error'].includes(d.status)||typeof d.errorCode!=='string'||!/^[A-Z_]{1,64}$/.test(d.errorCode)||Object.keys(d).some(k=>!['commandId','status','errorCode'].includes(k)))throw new ProtocolError('INVALID_RESULT');
  return d as Result;
}

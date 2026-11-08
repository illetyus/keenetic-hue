import type {Env} from './index';

const encoder = new TextEncoder();
export const ticketLifetime = 600;

type Ticket = {client: string; redirect: string; state: string; expires: number};

function encode(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function decode(value: string) {
  return Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/')), char => char.charCodeAt(0));
}

function key(secret: string) {
  return crypto.subtle.importKey('raw', encoder.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign', 'verify']);
}

function message(payload: string, csrf: string) {
  return encoder.encode('keenetic-hue/oauth-form/v1.' + payload + '.' + csrf);
}

export async function issueTicket(secret: string, csrf: string, flow: Omit<Ticket, 'expires'>) {
  const payload = encode(encoder.encode(JSON.stringify({...flow, expires: Date.now() / 1000 + ticketLifetime})));
  const signature = await crypto.subtle.sign('HMAC', await key(secret), message(payload, csrf));
  return payload + '.' + encode(new Uint8Array(signature));
}

export async function verifyTicket(env: Env, csrf: string, ticket: string): Promise<Ticket> {
  const secret = env.SETUP_ISSUER_TOKEN;
  if (!secret) throw Error('temporarily_unavailable');
  if (!/^[A-Za-z0-9_-]{1,10000}\.[A-Za-z0-9_-]{43}$/.test(ticket)) throw Error('invalid_grant');
  const [payload, signature] = ticket.split('.');
  if (payload.length % 4 === 1 ||
      !await crypto.subtle.verify('HMAC', await key(secret), decode(signature), message(payload, csrf))) {
    throw Error('invalid_grant');
  }
  const flow: Ticket = JSON.parse(new TextDecoder().decode(decode(payload)));
  const redirects = [
    `https://oauth-redirect.googleusercontent.com/r/${env.GOOGLE_PROJECT_ID}`,
    `https://oauth-redirect-sandbox.googleusercontent.com/r/${env.GOOGLE_PROJECT_ID}`,
  ];
  if (!Number.isFinite(flow.expires) || flow.expires <= Date.now() / 1000 ||
      flow.client !== env.OAUTH_CLIENT_ID || !redirects.includes(flow.redirect)) throw Error('invalid_grant');
  return flow;
}


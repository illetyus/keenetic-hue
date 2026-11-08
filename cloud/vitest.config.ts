import { cloudflareTest } from '@cloudflare/vitest-plugin';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins:[cloudflareTest({wrangler:{configPath:'./wrangler.jsonc'},miniflare:{bindings:{
    DEVICE_TOKEN:'test-device-token-aaaaaaaaaaaaaaaaaaaaaaaa',
    OPERATOR_TOKEN:'test-operator-token-bbbbbbbbbbbbbbbbbbbb',
    CONTROL_ENABLED:'true',
    GOOGLE_ENABLED:'true',GOOGLE_PROJECT_ID:'test-project',OAUTH_CLIENT_ID:'test-google-client',
    SETUP_ISSUER_TOKEN:'test-setup-issuer-dddddddddddddddddddddddddddd',
    OAUTH_CLIENT_SECRET:'test-client-secret-cccccccccccccccccccccccc',PUBLIC_ORIGIN:'https://relay.test',
  }}})],
  test:{testTimeout:15000},
});

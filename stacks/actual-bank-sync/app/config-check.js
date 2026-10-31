#!/usr/bin/env node
// Dry configuration check. Validates that the env is well-formed WITHOUT
// connecting to the Actual server and WITHOUT printing any secret value.
// Exit 0 = config looks valid, 1 = config invalid (missing/malformed env).
//
// Run by hand or in CI with placeholder values, e.g.:
//   ACTUAL_SERVER_URL=http://actual:5006 \
//   ACTUAL_SYNC_ID=821405a7-8bb2-403e-b17e-c8b871a9a670 \
//   ACTUAL_SESSION_TOKEN=x ACTUAL_E2E_PASSWORD=y node config-check.js
'use strict';

const lib = require('./lib');

try {
  const cfg = lib.loadConfig(process.env);
  // Report presence only -- never the value.
  const report = {
    serverURL: cfg.serverURL, // not secret (internal docker DNS)
    syncId: cfg.syncId, // not secret
    dataDir: cfg.dataDir,
    lokiUrl: cfg.lokiUrl || '(unset: stdout only)',
    nodeName: cfg.nodeName,
    session_token: cfg.sessionToken ? 'present' : 'MISSING',
    e2e_password: cfg.e2ePassword ? 'present' : 'MISSING',
  };
  process.stdout.write(`config-check ok: ${JSON.stringify(report)}\n`);
  process.exit(0);
} catch (err) {
  process.stderr.write(`config-check FAILED: ${err.message}\n`);
  process.exit(1);
}

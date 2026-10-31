// Pure, side-effect-free helpers for the Actual bank-sync one-shot.
//
// This module NEVER imports the Actual API and NEVER touches the network, so it
// is unit-testable on its own. Its whole job is to turn raw sync results into a
// single status record that holds COUNTS ONLY. The household's financial data
// (amounts, payees, account names, balances, notes) must never reach Loki, so
// the log line is assembled from a strict allow-list of keys (ALLOWED_KEYS):
// anything not on the list is dropped, even if a caller passes it in.
'use strict';

// The only keys that may ever appear in an emitted log line. Every one is a
// count, a timestamp, a fixed machine code, or a fixed human string.
const ALLOWED_KEYS = [
  'ts',
  'job',
  'event',
  'status',
  'message',
  'accounts_total',
  'accounts_ok',
  'accounts_failed',
  'new_transactions',
  'matched_transactions',
  'updated_accounts',
  'rules_categorised',
  'reauth_required',
  'error_code',
  'duration_ms',
];

const JOB = 'actual-bank-sync';

// SimpleFIN reports a dead/revoked access token with this code. It is a
// credential-WIDE failure (not one account), so when it is present nothing can
// sync and the owner must re-link SimpleFIN.
const SIMPLEFIN_INVALID_CODES = ['INVALID_ACCESS_TOKEN'];
// Codes that mean a specific linked account must be re-authorised in Actual.
const REAUTH_CODES = ['INVALID_ACCESS_TOKEN', 'ITEM_LOGIN_REQUIRED'];

// Fixed, financial-data-free status messages. These strings are safe to ship to
// Loki because they never interpolate account data.
const MESSAGES = {
  ok: 'Bank sync completed; all linked accounts synced.',
  partial: 'Bank sync completed with per-account errors; some accounts did not sync.',
  simplefin_token_invalid:
    'SimpleFIN access token invalid; re-link SimpleFIN in Actual (new setup token).',
  token_expired:
    'Actual session token expired or revoked; renew the dedicated browser session and update the session_token secret.',
  decrypt_failure:
    'Budget decryption failed; if the E2E key was rotated, update the e2e_password secret.',
  network_failure: 'Could not reach the Actual server.',
  config_error: 'Missing or invalid configuration.',
  error: 'Bank sync failed.',
};

class ConfigError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ConfigError';
    this.code = 'config_error';
  }
}

// Read and validate configuration from an env-like object. Returns a plain
// config object. Throws ConfigError naming the MISSING KEYS only -- it never
// echoes a value, so a bad token cannot leak into a log.
function loadConfig(env) {
  const serverURL = (env.ACTUAL_SERVER_URL || '').trim();
  const syncId = (env.ACTUAL_SYNC_ID || '').trim();
  const dataDir = (env.ACTUAL_DATA_DIR || '/data').trim();
  const sessionToken = env.ACTUAL_SESSION_TOKEN || '';
  const e2ePassword = env.ACTUAL_E2E_PASSWORD || '';
  const lokiUrl = (env.LOKI_URL || '').trim();
  const nodeName = (env.NODE_NAME || 'unknown').trim();

  const missing = [];
  if (!serverURL) missing.push('ACTUAL_SERVER_URL');
  if (!syncId) missing.push('ACTUAL_SYNC_ID');
  if (!sessionToken) missing.push('ACTUAL_SESSION_TOKEN');
  if (!e2ePassword) missing.push('ACTUAL_E2E_PASSWORD');
  if (missing.length) {
    throw new ConfigError(`missing required env: ${missing.join(', ')}`);
  }
  // A sync id is a UUID; validate the shape without logging the value.
  const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
  if (!UUID_RE.test(syncId)) {
    throw new ConfigError('ACTUAL_SYNC_ID is not a valid UUID');
  }
  return { serverURL, syncId, dataDir, sessionToken, e2ePassword, lokiUrl, nodeName };
}

// Map a thrown init/download/sync error to a fatal status + machine code.
// Reads only err.code / err.reason / err.message -- never any account payload.
function classifyFatal(err) {
  const code = (err && (err.code || err.reason)) || '';
  const msg = (err && err.message) || '';
  const hay = `${code} ${msg}`.toLowerCase();

  if (code === 'config_error') return { status: 'config_error', errorCode: 'config_error' };
  if (hay.includes('token-expired') || hay.includes('token_expired') ||
      (hay.includes('token') && hay.includes('expired')) ||
      hay.includes('unauthorized') || hay.includes('token-not-found')) {
    return { status: 'token_expired', errorCode: code || 'token-expired' };
  }
  if (hay.includes('decrypt') || hay.includes('invalid-password') ||
      hay.includes('missing-key') || hay.includes('key-not-found')) {
    return { status: 'decrypt_failure', errorCode: code || 'decrypt-failure' };
  }
  if (hay.includes('network') || hay.includes('offline') ||
      hay.includes('econn') || hay.includes('timeout') || hay.includes('timed-out')) {
    return { status: 'network_failure', errorCode: code || 'network-failure' };
  }
  if (hay.includes('budget-not-found') || hay.includes('not found')) {
    return { status: 'config_error', errorCode: code || 'budget-not-found' };
  }
  return { status: 'error', errorCode: code || 'error' };
}

// Reduce a list of per-account results { ok: boolean, code: string|null } to
// counts + an overall status. Pure; no account data in or out beyond codes.
function summarizeAccounts(accountsPer) {
  const total = accountsPer.length;
  const okList = accountsPer.filter((a) => a.ok);
  const failList = accountsPer.filter((a) => !a.ok);
  const ok = okList.length;
  const failed = failList.length;
  const failedCodes = failList.map((a) => a.code || 'error');
  const reauth = accountsPer.filter((a) => a.code && REAUTH_CODES.includes(a.code)).length;

  let status;
  if (total === 0 || failed === 0) {
    status = 'ok';
  } else if (ok === 0 && failedCodes.every((c) => SIMPLEFIN_INVALID_CODES.includes(c))) {
    status = 'simplefin_token_invalid';
  } else if (ok > 0) {
    status = 'partial';
  } else {
    status = 'error';
  }
  const errorCode = failed > 0 ? failedCodes[0] : null;
  return { total, ok, failed, reauth, status, errorCode };
}

// Assemble the full status record from already-counted inputs. Every field is a
// scalar count / code; nothing here can carry financial data.
function buildRecord({ summary, counts, rulesCategorised, durationMs, now }) {
  const status = summary.status;
  return {
    ts: new Date(now == null ? Date.now() : now).toISOString(),
    job: JOB,
    event: 'bank_sync',
    status,
    message: MESSAGES[status] || MESSAGES.error,
    accounts_total: summary.total,
    accounts_ok: summary.ok,
    accounts_failed: summary.failed,
    new_transactions: counts.new | 0,
    matched_transactions: counts.matched | 0,
    updated_accounts: counts.updated | 0,
    rules_categorised: rulesCategorised == null ? null : rulesCategorised | 0,
    reauth_required: summary.reauth | 0,
    error_code: summary.errorCode || null,
    duration_ms: durationMs | 0,
  };
}

// Build a fatal record (no accounts synced) from a classified error.
function buildFatalRecord({ status, errorCode, durationMs, now }) {
  return {
    ts: new Date(now == null ? Date.now() : now).toISOString(),
    job: JOB,
    event: 'bank_sync',
    status,
    message: MESSAGES[status] || MESSAGES.error,
    accounts_total: 0,
    accounts_ok: 0,
    accounts_failed: 0,
    new_transactions: 0,
    matched_transactions: 0,
    updated_accounts: 0,
    rules_categorised: null,
    reauth_required: 0,
    error_code: errorCode || status,
    duration_ms: durationMs | 0,
  };
}

// Serialise a record to a single JSON line using ONLY allow-listed keys. This is
// the last line of defence against leaking financial data: any key not in
// ALLOWED_KEYS is silently dropped.
function formatLogLine(record) {
  const safe = {};
  for (const key of ALLOWED_KEYS) {
    if (Object.prototype.hasOwnProperty.call(record, key)) {
      safe[key] = record[key];
    }
  }
  return JSON.stringify(safe);
}

module.exports = {
  ALLOWED_KEYS,
  JOB,
  SIMPLEFIN_INVALID_CODES,
  REAUTH_CODES,
  MESSAGES,
  ConfigError,
  loadConfig,
  classifyFatal,
  summarizeAccounts,
  buildRecord,
  buildFatalRecord,
  formatLogLine,
};

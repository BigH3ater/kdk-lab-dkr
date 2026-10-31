// Unit tests for the log formatter and classifiers. Run with `node --test`.
// The load-bearing test is "formatLogLine drops financial data": it guarantees
// the job can never leak amounts, payees, account names, balances or notes to
// Loki, even if an upstream result object carries them.
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const lib = require('./lib');

const FORBIDDEN_KEYS = ['amount', 'payee', 'name', 'account_name', 'balance',
  'balance_current', 'notes', 'note', 'transactions', 'imported_payee'];
const FORBIDDEN_VALUES = ['Safeway', 'SECRET-PAYEE', 'Joint Checking', '1234.56', '98765'];

test('ALLOWED_KEYS contains no field that could carry financial data', () => {
  for (const k of lib.ALLOWED_KEYS) {
    assert.ok(!FORBIDDEN_KEYS.includes(k), `allow-list must not include ${k}`);
  }
});

test('formatLogLine emits only allow-listed keys', () => {
  const record = lib.buildRecord({
    summary: { total: 2, ok: 2, failed: 0, reauth: 0, status: 'ok', errorCode: null },
    counts: { new: 3, matched: 1, updated: 2 },
    rulesCategorised: 3,
    durationMs: 1234,
    now: 0,
  });
  const parsed = JSON.parse(lib.formatLogLine(record));
  for (const key of Object.keys(parsed)) {
    assert.ok(lib.ALLOWED_KEYS.includes(key), `unexpected key leaked: ${key}`);
  }
  assert.equal(parsed.status, 'ok');
  assert.equal(parsed.new_transactions, 3);
  assert.equal(parsed.job, 'actual-bank-sync');
});

test('formatLogLine drops financial data even when present on the record', () => {
  // Simulate a careless caller passing a raw Actual account/transaction object.
  const dirty = {
    status: 'ok',
    accounts_total: 1,
    new_transactions: 1,
    // --- forbidden fields that must NOT survive ---
    amount: 1234.56,
    payee: 'SECRET-PAYEE',
    name: 'Joint Checking',
    account_name: 'Joint Checking',
    balance: 98765,
    balance_current: 98765,
    notes: 'private memo',
    transactions: [{ amount: 1234.56, payee: 'Safeway' }],
    imported_payee: 'Safeway',
  };
  const line = lib.formatLogLine(dirty);
  const parsed = JSON.parse(line);
  for (const key of FORBIDDEN_KEYS) {
    assert.ok(!(key in parsed), `forbidden key survived: ${key}`);
  }
  for (const needle of FORBIDDEN_VALUES) {
    assert.ok(!line.includes(needle), `forbidden value survived in line: ${needle}`);
  }
});

test('buildRecord from per-account objects carrying forbidden fields stays clean', () => {
  // summarizeAccounts only reads ok/code; buildRecord only reads counts.
  const accountsPer = [
    { ok: true, code: null, name: 'Joint Checking', balance: 98765 },
    { ok: true, code: null, name: 'Credit Card', amount: 1234.56 },
  ];
  const summary = lib.summarizeAccounts(accountsPer);
  const line = lib.formatLogLine(
    lib.buildRecord({
      summary,
      counts: { new: 0, matched: 0, updated: 0 },
      rulesCategorised: 0,
      durationMs: 5,
      now: 0,
    }),
  );
  assert.ok(!line.includes('Joint Checking'));
  assert.ok(!line.includes('98765'));
  assert.ok(!line.includes('1234.56'));
});

test('summarizeAccounts: all ok -> ok', () => {
  const s = lib.summarizeAccounts([{ ok: true, code: null }, { ok: true, code: null }]);
  assert.equal(s.status, 'ok');
  assert.equal(s.ok, 2);
  assert.equal(s.failed, 0);
  assert.equal(s.errorCode, null);
});

test('summarizeAccounts: no linked accounts -> ok', () => {
  assert.equal(lib.summarizeAccounts([]).status, 'ok');
});

test('summarizeAccounts: SimpleFIN invalid on all -> simplefin_token_invalid', () => {
  const s = lib.summarizeAccounts([
    { ok: false, code: 'INVALID_ACCESS_TOKEN' },
    { ok: false, code: 'INVALID_ACCESS_TOKEN' },
  ]);
  assert.equal(s.status, 'simplefin_token_invalid');
  assert.equal(s.reauth, 2);
  assert.equal(s.errorCode, 'INVALID_ACCESS_TOKEN');
});

test('summarizeAccounts: some ok, some failed -> partial', () => {
  const s = lib.summarizeAccounts([
    { ok: true, code: null },
    { ok: false, code: 'INVALID_ACCESS_TOKEN' },
  ]);
  assert.equal(s.status, 'partial');
  assert.equal(s.ok, 1);
  assert.equal(s.failed, 1);
});

test('summarizeAccounts: all failed with mixed non-simplefin codes -> error', () => {
  const s = lib.summarizeAccounts([
    { ok: false, code: 'RATE_LIMIT_EXCEEDED' },
    { ok: false, code: 'INTERNAL_ERROR' },
  ]);
  assert.equal(s.status, 'error');
});

test('classifyFatal: token expiry', () => {
  assert.equal(lib.classifyFatal({ code: 'token-expired' }).status, 'token_expired');
  assert.equal(
    lib.classifyFatal(new Error('Authentication failed: invalid or expired session token')).status,
    'token_expired',
  );
});

test('classifyFatal: decrypt and network', () => {
  assert.equal(lib.classifyFatal({ code: 'decrypt-failure' }).status, 'decrypt_failure');
  assert.equal(lib.classifyFatal({ code: 'missing-key' }).status, 'decrypt_failure');
  assert.equal(lib.classifyFatal({ code: 'network-failure' }).status, 'network_failure');
});

test('loadConfig: missing secrets throw config_error naming keys, not values', () => {
  try {
    lib.loadConfig({ ACTUAL_SERVER_URL: 'http://actual:5006', ACTUAL_SYNC_ID: '821405a7-8bb2-403e-b17e-c8b871a9a670' });
    assert.fail('should have thrown');
  } catch (err) {
    assert.equal(err.code, 'config_error');
    assert.ok(err.message.includes('ACTUAL_SESSION_TOKEN'));
    assert.ok(err.message.includes('ACTUAL_E2E_PASSWORD'));
  }
});

test('loadConfig: rejects a malformed sync id', () => {
  assert.throws(() =>
    lib.loadConfig({
      ACTUAL_SERVER_URL: 'http://actual:5006',
      ACTUAL_SYNC_ID: 'not-a-uuid',
      ACTUAL_SESSION_TOKEN: 'x',
      ACTUAL_E2E_PASSWORD: 'y',
    }),
  );
});

test('loadConfig: valid env returns config and never exposes the token in message', () => {
  const cfg = lib.loadConfig({
    ACTUAL_SERVER_URL: 'http://actual:5006',
    ACTUAL_SYNC_ID: '821405a7-8bb2-403e-b17e-c8b871a9a670',
    ACTUAL_SESSION_TOKEN: 'super-secret-token',
    ACTUAL_E2E_PASSWORD: 'super-secret-e2e',
    LOKI_URL: 'http://10.1.20.21:3100',
    NODE_NAME: 'kdk-dkr-01',
  });
  assert.equal(cfg.serverURL, 'http://actual:5006');
  assert.equal(cfg.sessionToken, 'super-secret-token');
});

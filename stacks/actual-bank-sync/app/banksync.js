#!/usr/bin/env node
// Daily Actual Budget bank-sync one-shot for the kdk-lab homelab.
//
// Actual does not sync bank data on its own: transactions only arrive when
// someone presses "Bank Sync". This job does that press once a day, headless.
//
// It authenticates to the Actual server with a SESSION TOKEN taken from a
// dedicated, never-logged-out browser session (login is Authelia OIDC only, so
// there is no password), downloads the end-to-end-encrypted budget into a
// tmpfs, runs the bank sync per linked account (one failing account does not
// stop the others), then emits ONE status line of COUNTS ONLY -- never amounts,
// payees, account names or balances. The line goes to stdout and, because the
// host's Alloy does not reliably scrape short-lived one-shot containers, is also
// pushed straight to Loki (the same pattern as stacks/advisory-watch and
// stacks/readwise-koreader). The Loki ruler alerts on status != "ok".
//
// Credentials come from env (rendered by Komodo pre_deploy from .env.tpl via
// op inject): ACTUAL_SESSION_TOKEN and ACTUAL_E2E_PASSWORD. Nothing secret is
// ever written to a log.
'use strict';

const api = require('@actual-app/api');
const lib = require('./lib');

const SIMPLEFIN = 'simpleFin';

// Push one JSON line to Loki as a {job="actual-bank-sync"} stream. Best effort:
// a push failure is reported to stderr but never changes the run result. If no
// successful line reaches Loki, the "no successful sync in 36 h" rule fires.
async function pushToLoki(lokiUrl, nodeName, record, line) {
  if (!lokiUrl) return;
  const level = record.status === 'ok' ? 'info' : 'error';
  const nanos = (BigInt(Date.now()) * 1000000n).toString();
  const body = {
    streams: [
      {
        stream: { job: lib.JOB, host: nodeName, level },
        values: [[nanos, line]],
      },
    ],
  };
  const url = `${lokiUrl.replace(/\/$/, '')}/loki/api/v1/push`;
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), 10000);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ac.signal,
    });
    if (!res.ok) process.stderr.write(`loki push failed: HTTP ${res.status}\n`);
  } catch (err) {
    process.stderr.write(`loki push failed: ${err && err.name ? err.name : 'error'}\n`);
  } finally {
    clearTimeout(timer);
  }
}

// Normalise a simplefin-batch-sync per-account result entry.
function fromSimplefin(entry, counts) {
  const res = (entry && entry.res) || {};
  const errs = res.errors || [];
  counts.new += (res.newTransactions || []).length;
  counts.matched += (res.matchedTransactions || []).length;
  counts.updated += (res.updatedAccounts || []).length;
  return { ok: errs.length === 0, code: errs.length ? errs[0].code || 'error' : null };
}

async function runSync() {
  const counts = { new: 0, matched: 0, updated: 0 };
  const newIds = [];

  // Discard everything except id + sync source the instant we read accounts, so
  // names and balances never travel further than this line.
  const rawAccounts = await api.internal.send('accounts-get');
  const linked = (rawAccounts || [])
    .filter((a) => !a.closed && a.tombstone === 0 && a.account_sync_source)
    .map((a) => ({ id: a.id, source: a.account_sync_source }));

  const sfIds = linked.filter((a) => a.source === SIMPLEFIN).map((a) => a.id);
  const otherIds = linked.filter((a) => a.source !== SIMPLEFIN).map((a) => a.id);

  const accountsPer = [];

  if (sfIds.length) {
    const results = await api.internal.send('simplefin-batch-sync', { ids: sfIds });
    for (const entry of results || []) {
      accountsPer.push(fromSimplefin(entry, counts));
      ((entry.res && entry.res.newTransactions) || []).forEach((id) => newIds.push(id));
    }
  }

  if (otherIds.length) {
    // accounts-bank-sync returns an AGGREGATE { errors[], newTransactions[], ... }
    // where each error carries its accountId. Expand it to per-account ok/fail.
    const agg = (await api.internal.send('accounts-bank-sync', { ids: otherIds })) || {};
    const errById = new Map((agg.errors || []).map((e) => [e.accountId, e]));
    counts.new += (agg.newTransactions || []).length;
    counts.matched += (agg.matchedTransactions || []).length;
    counts.updated += (agg.updatedAccounts || []).length;
    (agg.newTransactions || []).forEach((id) => newIds.push(id));
    for (const id of otherIds) {
      const e = errById.get(id);
      accountsPer.push({ ok: !e, code: e ? e.code || 'error' : null });
    }
  }

  const rulesCategorised = await countRuleCategorised(newIds);
  return { accountsPer, counts, rulesCategorised };
}

// Best-effort count of freshly-imported transactions that a rule gave a
// category. Uses an AQL COUNT so no amount or payee leaves the process; any
// failure degrades to null rather than failing the run.
async function countRuleCategorised(newIds) {
  if (!newIds.length) return 0;
  try {
    const query = api
      .q('transactions')
      .filter({ id: { $oneof: newIds }, category: { $ne: null } })
      .calculate({ $count: 'id' });
    const result = await api.aqlQuery(query);
    const data = result && result.data;
    if (typeof data === 'number') return data;
    if (Array.isArray(data)) return data.length;
    return null;
  } catch (_err) {
    return null;
  }
}

async function main() {
  const started = Date.now();
  let cfg;
  try {
    cfg = lib.loadConfig(process.env);
  } catch (err) {
    // No sink config available yet; print to stdout only.
    const rec = lib.buildFatalRecord({
      status: 'config_error',
      errorCode: 'config_error',
      durationMs: Date.now() - started,
    });
    process.stdout.write(`${lib.formatLogLine(rec)}\n`);
    return;
  }

  const emit = async (record) => {
    const line = lib.formatLogLine(record);
    process.stdout.write(`${line}\n`);
    await pushToLoki(cfg.lokiUrl, cfg.nodeName, record, line);
  };

  let record;
  try {
    await api.init({
      dataDir: cfg.dataDir,
      serverURL: cfg.serverURL,
      sessionToken: cfg.sessionToken,
    });
    await api.downloadBudget(cfg.syncId, { password: cfg.e2ePassword });
    const { accountsPer, counts, rulesCategorised } = await runSync();
    await api.sync(); // push the imported transactions up to the server
    const summary = lib.summarizeAccounts(accountsPer);
    record = lib.buildRecord({
      summary,
      counts,
      rulesCategorised,
      durationMs: Date.now() - started,
    });
  } catch (err) {
    const { status, errorCode } = lib.classifyFatal(err);
    record = lib.buildFatalRecord({ status, errorCode, durationMs: Date.now() - started });
  } finally {
    try {
      await api.shutdown();
    } catch (_err) {
      /* ignore: shutdown best-effort */
    }
  }
  await emit(record);
  // Exit 0 regardless of status: alerting is the Loki ruler's job, and a
  // non-zero exit would also trip Komodo's own procedure failure alert (double
  // page). The status field + the Loki rule are the single alert path.
}

main().then(
  () => process.exit(0),
  (err) => {
    // Last-resort guard: never crash without a safe, data-free line.
    process.stderr.write(`fatal: ${err && err.name ? err.name : 'error'}\n`);
    const rec = lib.buildFatalRecord({ status: 'error', errorCode: 'error', durationMs: 0 });
    process.stdout.write(`${lib.formatLogLine(rec)}\n`);
    process.exit(0);
  },
);

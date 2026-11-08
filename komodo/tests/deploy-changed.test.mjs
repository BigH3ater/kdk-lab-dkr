// Tests for the Komodo `deploy-changed` action (komodo/resources.toml).
//
// Komodo is not run here. The action's file_contents is read straight out of
// resources.toml, so the tests exercise the exact source that ships:
//   - the plain-JS decision block (headGate / nextFailCount / decideOwn /
//     decideForeign) is evaluated on its own;
//   - the whole action is written to a temp .mts (Node strips the TypeScript
//     annotations) and run against a mock `komodo` client and a mock `fetch`.
//
// Run: node --test komodo/tests/   (Node >= 22.18 for type stripping; CI uses
// the pinned node image in .github/workflows/komodo-actions.yml).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const toml = readFileSync(join(here, "..", "resources.toml"), "utf8");

function actionSource(name) {
  const at = toml.indexOf(`[[action]]\ntags = ["kdk-lab"]\nname = "${name}"`);
  assert.ok(at >= 0, `action ${name} not found in resources.toml`);
  const open = toml.indexOf("file_contents = '''\n", at) + "file_contents = '''\n".length;
  const close = toml.indexOf("\n'''", open);
  return toml.slice(open, close);
}
const SRC = actionSource("deploy-changed");

// ---- the decision block on its own ----
const BEGIN = "// ---- decision logic:", END = "// ---- end decision logic ----";
assert.ok(SRC.includes(BEGIN) && SRC.includes(END), "decision-logic markers missing");
const block = SRC.slice(SRC.indexOf(BEGIN), SRC.indexOf(END));
const L = new Function(`${block}\nreturn { headGate, nextFailCount, decideOwn, decideForeign };`)();

const HEAD = "a".repeat(40);

test("headGate: no HEAD skips the tick with the alert line", () => {
  assert.deepEqual(L.headGate(HEAD, ""), { run: true, line: "" });
  assert.deepEqual(L.headGate("", "TypeError: fetch failed"), {
    run: false,
    line: "head lookup failed: TypeError: fetch failed; skipping deploy-changed this tick",
  });
  assert.equal(L.headGate("", "").line, "head lookup failed: no sha; skipping deploy-changed this tick");
});

test("nextFailCount: counts up from absent/garbage, resets on success", () => {
  assert.equal(L.nextFailCount(null, false), 1);
  assert.equal(L.nextFailCount("3", false), 4);
  assert.equal(L.nextFailCount("junk", false), 1);
  assert.equal(L.nextFailCount("7", true), 0);
});

test("decideOwn: unchanged normal-path decisions", () => {
  assert.equal(L.decideOwn(HEAD, "aaaaaaa", [], "stacks/x").deploy, false);            // at HEAD
  assert.match(L.decideOwn(HEAD, "1111111", null, "stacks/x").why, /compare unknown/);   // compare failed
  const mine = L.decideOwn(HEAD, "1111111", ["stacks/x/compose.yaml", "stacks/y/a"], "stacks/x/");
  assert.deepEqual(mine, { deploy: true, why: "1111111->aaaaaaa 1 file(s): stacks/x/compose.yaml" });
  assert.match(L.decideOwn(HEAD, "1111111", ["komodo/resources.toml"], "stacks/x").why, /resources.toml changed/);
  assert.deepEqual(L.decideOwn(HEAD, "1111111", ["stacks/xy/a"], "stacks/x"), { deploy: false, why: "untouched" });
});

test("decideForeign: latest_hash comparison for other repos", () => {
  assert.equal(L.decideForeign("1111111", "1111111").deploy, false);
  assert.equal(L.decideForeign("1111111", undefined).deploy, false);
  assert.deepEqual(L.decideForeign("1111111", "2222222"), { deploy: true, why: "1111111->2222222 (latest_hash)" });
});

// ---- the whole action against mocks ----
const dir = mkdtempSync(join(tmpdir(), "deploy-changed-"));
const mod = join(dir, "action.mts");
writeFileSync(mod, `export default async function (komodo: any, fetch: any, setTimeout: any, console: any) {\n${SRC}\n}\n`);
const run = (await import(pathToFileURL(mod).href)).default;

// Five own-repo stacks, four of them "behind" Komodo's latest_hash (the
// 2026-11-08 state), plus one stack from another repo.
function world({ variable = null, compareFiles = [] } = {}) {
  const calls = [], logs = [];
  const vars = new Map(variable === null ? [] : [["DEPLOY_CHANGED_HEAD_FAILS", variable]]);
  const stacks = ["apps", "loki", "proxy", "nut", "untouched", "foreign"].map((name) => ({
    name, tags: ["t1"], info: { repo: "x", state: "running" },
  }));
  const deployed = {
    apps: "1111111", loki: "1111111", proxy: "1111111", nut: HEAD.slice(0, 7),
    untouched: "1111111", foreign: "f000000",
  };
  const komodo = {
    async read(m, p) {
      calls.push(["read", m, p]);
      if (m === "ListTags") return [{ name: "kdk-lab", _id: { $oid: "t1" } }];
      if (m === "ListServers") return [{ id: "s1" }];
      if (m === "ListStacks") return stacks;
      if (m === "GetStack") return {
        info: { deployed_hash: deployed[p.stack], latest_hash: p.stack === "foreign" ? "f111111" : "e7c9744" },
        config: {
          repo: p.stack === "foreign" ? "BigH3ater/network-sre-learning" : "BigH3ater/kdk-lab-dkr",
          run_directory: `stacks/${p.stack}`,
        },
      };
      if (m === "GetVariable") {
        if (!vars.has(p.name)) throw new Error("no variable");
        return { name: p.name, value: vars.get(p.name) };
      }
      throw new Error(`unexpected read ${m}`);
    },
    async write(m, p) {
      calls.push(["write", m, p]);
      if (m === "CreateVariable" || m === "UpdateVariableValue") vars.set(p.name, p.value);
      if (m === "DeleteVariable") vars.delete(p.name);
      return {};
    },
    async execute(m, p) { calls.push(["execute", m, p]); return {}; },
  };
  const con = { log: (...a) => logs.push(a.join(" ")) };
  const deploys = () => calls.filter((c) => c[1] === "DeployStack").map((c) => c[2].stack);
  const writes = (m) => calls.filter((c) => c[0] === "write" && c[1] === m);
  return { komodo, con, calls, logs, vars, deploys, writes, compareFiles };
}
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const immediate = (fn) => fn();
const githubUp = (w) => async (url) =>
  url.endsWith("/commits/main") ? ok({ sha: HEAD }) : ok({ files: w.compareFiles.map((filename) => ({ filename })) });

test("HEAD lookup throws: no deploys, no latest_hash fallback, counter created", async () => {
  const w = world();
  await run(w.komodo, async () => { throw new TypeError("fetch failed"); }, immediate, w.con);
  assert.deepEqual(w.deploys(), []);
  assert.equal(w.calls.filter((c) => c[1] === "RefreshStackCache").length, 0);
  assert.ok(w.logs.includes("head lookup failed: TypeError: fetch failed; skipping deploy-changed this tick"));
  assert.equal(w.writes("CreateVariable").length, 1);
  assert.equal(w.vars.get("DEPLOY_CHANGED_HEAD_FAILS"), "1");
  assert.ok(w.logs.includes("deploy-changed: queued 0 deploys"));
});

test("HEAD lookup 504 on the 4th tick in a row: counter updated, still no deploys", async () => {
  const w = world({ variable: "3" });
  await run(w.komodo, async () => ({ ok: false, status: 504, json: async () => ({}) }), immediate, w.con);
  assert.deepEqual(w.deploys(), []);
  assert.ok(w.logs.includes("head lookup failed: Error: GitHub /commits/main: HTTP 504; skipping deploy-changed this tick"));
  assert.equal(w.writes("UpdateVariableValue").length, 1);
  assert.equal(w.writes("CreateVariable").length, 0);
  assert.equal(w.vars.get("DEPLOY_CHANGED_HEAD_FAILS"), "4");
});

test("HEAD reachable after failures: counter deleted, only touched stacks deploy", async () => {
  const w = world({ variable: "2", compareFiles: ["stacks/apps/compose.yaml", "stacks/loki/rules/komodo.yml"] });
  await run(w.komodo, githubUp(w), immediate, w.con);
  assert.equal(w.writes("DeleteVariable").length, 1);
  assert.equal(w.vars.has("DEPLOY_CHANGED_HEAD_FAILS"), false);
  // apps + loki touched; proxy/untouched not; nut at HEAD; foreign behind on latest_hash.
  assert.deepEqual(w.deploys(), ["apps", "loki", "foreign"]);
});

test("normal path, no counter: no variable writes; resources.toml change deploys every own stack not at HEAD", async () => {
  const w = world({ compareFiles: ["komodo/resources.toml"] });
  await run(w.komodo, githubUp(w), immediate, w.con);
  assert.equal(w.calls.filter((c) => c[0] === "write" && /Variable/.test(c[1])).length, 0);
  assert.deepEqual(w.deploys(), ["apps", "loki", "proxy", "untouched", "foreign"]);
});

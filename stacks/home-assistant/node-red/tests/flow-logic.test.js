#!/usr/bin/env node
/*
 * Unit tests for the Roborock flow's function-node logic (tab rr_tab_1).
 *
 * Node-RED is not run here. Each function node's `func` body is loaded straight
 * out of flows.json and executed with a mock Node-RED scope (node/msg/flow/global),
 * so the tests exercise the exact source that ships. Run: node flow-logic.test.js
 *
 * Covers the 2026-11-02 restart-resilience / mop-failure changes:
 *   - unavailable/unknown transitions are ignored (trigger config + f_ev_idle)
 *   - a restart with an active run -> interrupted notice, no room stamps
 *   - Wednesday mop due / Thursday catch-up after a failed Wednesday / no double mop
 *   - resume-drop after recharge -> mop gate, no stamping
 *   - every mop-failure path emits exactly one rr_mop_failed notify
 *
 * 2026-11-04 (fix/roborock-mop-catchup-water, after the Tue 11-03 double mop):
 *   - shared rrMopDue rule: Wed unless a mop ran in 48 h; catch-up Thu/Fri only
 *   - f_mopchk needs a 20-min quiet window + robot home; water shortage = failed
 *   - mid-pass water watch (t_mopwater -> f_mopwater -> f_mopfail), one alert
 *   - f_rc: a sweep under 10 min with no progress does not stamp the 12 rooms
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FLOWS = path.join(__dirname, "..", "flows.json");
const SETTINGS = path.join(__dirname, "..", "settings.js");
const flows = JSON.parse(fs.readFileSync(FLOWS, "utf8"));
const byId = Object.fromEntries(flows.map((n) => [n.id, n]));

// ---- tiny assert harness --------------------------------------------------
let passed = 0, failed = 0;
function ok(cond, name) {
  if (cond) { passed++; } else { failed++; console.log("  FAIL:", name); }
}
function eq(a, b, name) { ok(JSON.stringify(a) === JSON.stringify(b), name + ` (got ${JSON.stringify(a)})`); }

// ---- mocks ----------------------------------------------------------------
function mkFlow(init) {
  const s = Object.assign({}, init);
  return { _s: s, get: (k) => s[k], set: (k, v) => { s[k] = v; } };
}
function mkGlobal(states) {
  return { get: (k) => (k === "homeassistant" ? { srv: { states } } : undefined) };
}
const nodeMock = { warn() {}, status() {}, error() {} };

function state(map) {
  const out = {};
  for (const [k, v] of Object.entries(map)) {
    out[k] = typeof v === "object" ? v : { state: String(v) };
  }
  return out;
}

// run a function node's body with the given scope; returns its return value
function run(id, msg, flow, global) {
  const body = byId[id].func;
  const fn = new Function("node", "msg", "flow", "global", "context", "env", "RED", body);
  return fn(nodeMock, msg, flow || mkFlow({}), global || mkGlobal({}), {}, { get() {} }, {});
}

// run with a fixed "now" (controls new Date() / Date.now() inside the body)
function withDate(fixedMs, fn) {
  const Real = Date;
  class Fake extends Real {
    constructor(...a) { if (a.length) super(...a); else super(fixedMs); }
    static now() { return fixedMs; }
  }
  global.Date = Fake;
  try { return fn(); } finally { global.Date = Real; }
}

// Wednesday 2026-11-04 09:00 local, and Thursday 2026-11-05 09:00 local
const WED = new Date(2026, 10, 4, 9, 0, 0).getTime();
const THU = new Date(2026, 10, 5, 9, 0, 0).getTime();
const lastMop = (ms) => ({ state: "x", attributes: { timestamp: Math.floor(ms / 1000) } });

// ===========================================================================
console.log("Item 1 -- trigger ignore flags + f_ev_idle");
for (const id of ["t_idle", "t_dock", "t_croom"]) {
  ok(byId[id].ignorePrevStateUnavailable === true, `${id} ignorePrevStateUnavailable`);
  ok(byId[id].ignorePrevStateUnknown === true, `${id} ignorePrevStateUnknown`);
}
for (const k of ["ignorePrevStateUnavailable", "ignorePrevStateUnknown",
                 "ignoreCurrentStateUnavailable", "ignoreCurrentStateUnknown"]) {
  ok(byId["t_86"][k] === true, `t_86 ${k}`);
}
// f_ev_idle: returning -> idle must NOT cancel (returns null); any other prev -> idle event
eq(run("f_ev_idle", { data: { old_state: { state: "returning" } } }), null, "f_ev_idle returning->idle suppressed");
ok(run("f_ev_idle", { data: { old_state: { state: "cleaning" } } }).rr_evt === "idle", "f_ev_idle cleaning->idle fires");

console.log("Item 1/3 -- context routed to the file store");
ok(/flow\.get\('rr_run', 'file'\)/.test(byId["f_track"].func), "f_track reads rr_run from file store");
ok(/flow\.set\('rr_run', run, 'file'\)/.test(byId["f_track"].func), "f_track writes rr_run to file store");
ok(/flow\.get\('vik_fill_last', 'file'\)/.test(byId["f_vk3"].func), "f_vk3 reads vik_fill_last from file store");
ok(fs.readFileSync(SETTINGS, "utf8").includes("file: { module: \"localfilesystem\""), "settings.js defines file store");

console.log("Item 2 -- HA reconnect recovery routing");
{
  const g = mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "vacuum.mr_slave_2": "docked" }));
  // active run stuck at zone A (w_42 armed), robot already docked -> output 0
  let f = mkFlow({ rr_run: { active: true, phase: "Master bedroom — zone A" }, rr_pending: "w_42", rr_waitgen: 1 });
  let r = run("f_hareconn", { payload: { event: "states_loaded" } }, f, g);
  ok(r[0] && !r[1] && !r[2] && !r[3], "reconnect zone A -> out0 only");
  ok(f.get("rr_waitgen") === 2 && f.get("rr_pending") === "" && r[0].rr_wgen === 2, "recovery bumps gen + clears pending + stamps msg");
  // second burst event is throttled (reconnAt set) -> no output
  let r2 = run("f_hareconn", { payload: { event: "running" } }, f, g);
  eq(r2, [null, null, null, null], "reconnect burst throttled");
  // mop phase (w_62 armed) -> out3 (g_w62 path)
  f = mkFlow({ rr_run: { active: true, phase: "Mop pass (all floors)" }, rr_pending: "w_62", rr_waitgen: 7 });
  r = run("f_hareconn", { payload: { event: "running" } }, f, g);
  ok(!r[0] && !r[1] && !r[2] && r[3], "reconnect mop -> out3 only");
  // mop phase but w_62 already fired (mid w_mopoff loop, pending cleared) -> no inject
  f = mkFlow({ rr_run: { active: true, phase: "Mop pass (all floors)" }, rr_pending: "", rr_waitgen: 7 });
  eq(run("f_hareconn", { payload: { event: "running" } }, f, g), [null, null, null, null], "reconnect mop, wait not pending -> nothing");
  // robot NOT docked -> no recovery (wait still valid)
  const g2 = mkGlobal(state({ "sensor.mr_slave_status_2": "segment_cleaning" }));
  f = mkFlow({ rr_run: { active: true, phase: "Master bedroom — zone A" }, rr_pending: "w_42" });
  eq(run("f_hareconn", { payload: { event: "running" } }, f, g2), [null, null, null, null], "reconnect not-docked -> nothing");
  // no active run -> nothing
  f = mkFlow({ rr_run: { active: false }, rr_pending: "w_42" });
  eq(run("f_hareconn", { payload: { event: "running" } }, f, g), [null, null, null, null], "reconnect inactive -> nothing");
  // non-ready client event -> nothing
  f = mkFlow({ rr_run: { active: true, phase: "Master bedroom — zone A" }, rr_pending: "w_42" });
  eq(run("f_hareconn", { payload: { event: "disconnected" } }, f, g), [null, null, null, null], "reconnect disconnected -> nothing");
}

console.log("Item 2 -- stale-wait guard (generation token) after a reconnect recovery");
{
  const g = mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "vacuum.mr_slave_2": "docked" }));
  // arm w_42, then recover the run on reconnect
  const f = mkFlow({ rr_run: { active: true, phase: "Master bedroom — zone A" } });
  const armedMsg = {};
  run("arm_42", armedMsg, f);
  ok(f.get("rr_waitgen") === 1 && f.get("rr_pending") === "w_42" && armedMsg.rr_wgen === 1, "arm_42 sets gen+pending+stamp");
  const rec = run("f_hareconn", { payload: { event: "states_loaded" } }, f, g);
  ok(rec[0] && rec[0].rr_wgen === 2, "f_hareconn recovers with bumped gen");
  // the recovery message itself passes the success guard (current gen)
  ok(Array.isArray(run("f_short_A", rec[0], f)), "recovery msg passes f_short_A");
  // (1) the abandoned w_42 success firing LATER is dropped (stale gen)
  eq(run("f_short_A", armedMsg, f), null, "stale w_42 success dropped -> no double advance");
  // (2) the abandoned w_42 timeout firing later is dropped -> no stall/cancel
  eq(run("f_to_w_42", armedMsg, f), null, "stale w_42 timeout dropped -> no spurious cancel");

  // (3) the normal, non-recovered path still works (gen matches)
  const f2 = mkFlow({});
  const okMsg = {};
  run("arm_42", okMsg, f2);
  ok(Array.isArray(run("f_short_A", okMsg, f2)), "normal w_42 success passes");
  ok(f2.get("rr_pending") === "", "guard clears pending on a normal fire");
  const f3 = mkFlow({});
  const toMsg = {};
  run("arm_48", toMsg, f3);
  const to = run("f_to_w_48", toMsg, f3);
  ok(to && to.rr_stage === "Master bedroom zone B" && to.rr_mins === 40, "normal w_48 timeout passes -> stall msg");
  // g_w62 guard: stale mop-dock dropped, fresh passes
  const f4 = mkFlow({}); const mm = {};
  run("arm_62", mm, f4);
  ok(run("g_w62", { rr_wgen: mm.rr_wgen }, f4) !== null, "g_w62 passes current mop-dock");
  eq(run("g_w62", { rr_wgen: 0 }, f4), null, "g_w62 drops stale mop-dock");
}

console.log("Item 3 -- startup check (f_boot) + f_track interrupted (no stamps)");
{
  // no active run
  eq(run("f_boot", {}, mkFlow({ rr_run: { active: false } }), mkGlobal(state({ "sensor.mr_slave_status_2": "charging" }))),
     [null, null], "f_boot no active run -> nothing");
  // active + not cleaning -> interrupted evt (out0) + rr_restart notify (out1)
  let r = run("f_boot", {}, mkFlow({ rr_run: { active: true, phase: "Main floor sweep (12 rooms)" } }),
              mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "binary_sensor.mr_slave_cleaning_2": "off" })));
  ok(r[0] && r[0].rr_evt === "failed" && r[0].rr_phase === "Interrupted by a restart", "f_boot interrupted -> failed evt");
  ok(r[1] && r[1].notify.tag === "rr_restart" && r[1].notify.urgent === false, "f_boot interrupted -> rr_restart notify");
  // active + still cleaning -> notify only, no evt (don't kill a live run)
  r = run("f_boot", {}, mkFlow({ rr_run: { active: true } }),
          mkGlobal(state({ "sensor.mr_slave_status_2": "segment_cleaning", "binary_sensor.mr_slave_cleaning_2": "on" })));
  ok(r[0] === null && r[1] && r[1].notify.tag === "rr_restart", "f_boot still-cleaning -> notify only, no evt");
  // states not loaded -> skip
  eq(run("f_boot", {}, mkFlow({ rr_run: { active: true } }), mkGlobal({})), [null, null], "f_boot no states -> skip");

  // f_track 'failed' must stamp NOTHING and clear active
  const f = mkFlow({ rr_run: { active: true, phase: "x", cancelled: false, near: "r" } });
  const tr = run("f_track", { rr_evt: "failed", rr_phase: "Interrupted by a restart" }, f);
  ok(Array.isArray(tr[1]) && tr[1].length === 0, "f_track failed -> no stamp output");
  ok(f.get("rr_run").active === false && f.get("rr_run").cancelled === true, "f_track failed -> run inactive+cancelled");
  // sanity: 'restdone' DOES stamp every room (so we know the no-stamp result is meaningful)
  const f2 = mkFlow({ rr_run: { active: true, phase: "x", cancelled: false, near: "" } });
  const tr2 = run("f_track", { rr_evt: "restdone" }, f2);
  ok(tr2[1].length === 1 && Array.isArray(tr2[1][0].payload.stamp) && tr2[1][0].payload.stamp.length === 12, "f_track restdone -> 12 room stamps");
}

console.log("Item 4a -- f_58 mop due / catch-up / no double mop");
{
  const g = (lastMopMs) => mkGlobal(state({ "input_datetime.rr_last_mop_run": lastMop(lastMopMs) }));
  // Wednesday, last mop a week ago -> due, proceed (out0)
  let r = withDate(WED, () => run("f_58", {}, mkFlow({}), g(WED - 7 * 864e5)));
  ok(r[0] && !r[1] && !r[2], "Wednesday -> mop proceeds (out0)");
  // Thursday, last mop was PREVIOUS week (failed Wednesday) -> catch-up due (out0)
  r = withDate(THU, () => run("f_58", {}, mkFlow({}), g(WED - 7 * 864e5)));
  ok(r[0] && !r[1] && !r[2], "Thursday catch-up after missed Wednesday -> proceeds");
  // Thursday, last mop was THIS Wednesday -> NOT due, no double mop (out1 = vacuum only)
  r = withDate(THU, () => run("f_58", {}, mkFlow({}), g(WED)));
  ok(!r[0] && r[1] && !r[2], "Thursday after a good Wednesday -> no double mop (out1)");
  // Wednesday but preflight flagged a problem -> skip routes to mop-failure (out2) with reason
  r = withDate(WED, () => run("f_58", {}, mkFlow({ rr_mop_skip: true }), g(WED - 7 * 864e5)));
  ok(!r[0] && !r[1] && r[2] && /skipped/.test(r[2].rr_mop_reason), "mop-day skip -> out2 with reason");
}

console.log("Item 4b -- f_rcpoll resume / resume-drop");
{
  // resumed on its own -> out0 (back to wait dock rest)
  let r = run("f_rcpoll", {}, mkFlow({}), mkGlobal(state({ "sensor.mr_slave_status_2": "segment_cleaning" })));
  ok(r[0] && !r[1] && !r[2] && !r[3], "resume detected -> out0");
  // progress reset to 0 while charging -> drop -> out1 (d_55) + out2 notify, NOT a stamp
  r = run("f_rcpoll", {}, mkFlow({}),
          mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "sensor.mr_slave_cleaning_progress_2": "0", "sensor.mr_slave_battery_2": "35" })));
  ok(!r[0] && r[1] && r[2] && !r[3], "resume-drop -> out1(d_55)+out2(notify)");
  ok(r[2].notify.tag === "rr_no_resume" && r[2].notify.urgent === true, "resume-drop notify tag rr_no_resume urgent");
  // still charging, progress>0, battery low -> loop (out3)
  r = run("f_rcpoll", { rr_rc_polls: 3 }, mkFlow({}),
          mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "sensor.mr_slave_cleaning_progress_2": "40", "sensor.mr_slave_battery_2": "55" })));
  ok(!r[0] && !r[1] && !r[2] && r[3], "still charging -> loop (out3)");
  // charged & full for the 5th consecutive poll -> drop
  r = run("f_rcpoll", { rr_rc_full: 4 }, mkFlow({}),
          mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "sensor.mr_slave_cleaning_progress_2": "40", "sensor.mr_slave_battery_2": "97" })));
  ok(r[1] && r[2] && /did not resume/.test(r[2].notify.message), "charged+full 10 min -> drop");
  // 150-min cap
  r = run("f_rcpoll", { rr_rc_polls: 74 }, mkFlow({}),
          mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "sensor.mr_slave_cleaning_progress_2": "40", "sensor.mr_slave_battery_2": "60" })));
  ok(r[1] && r[2] && /within 150 min/.test(r[2].notify.message), "150-min cap -> drop");
}

console.log("Item 5 -- every mop-failure path emits exactly one rr_mop_failed");
{
  function chain(setterId, msg, flowInit) {
    const m1 = run(setterId, msg || {}, mkFlow(flowInit || {}));
    // f_mopchk/f_58 give-up return multi-output arrays; pick the message routed to f_mopfail
    let m = Array.isArray(m1) ? m1.find((x) => x && x.rr_mop_reason) : m1;
    const f = mkFlow({ rr_mop_pending: true });
    const out = run("f_mopfail", m, f);
    return { notify: out[0], passOn: out[1], flow: f };
  }
  const cases = {
    "w_62 timeout (f_to_w_62)": () => chain("f_to_w_62", {}),
    "battery timeout (f_mopbat_to)": () => chain("f_mopbat_to", {}),
    "routine timeout (f_mopoff_to)": () => chain("f_mopoff_to", {}),
    "mop-day skip (f_58)": () => {
      const r = withDate(WED, () => run("f_58", {}, mkFlow({ rr_mop_skip: true }),
        mkGlobal(state({ "input_datetime.rr_last_mop_run": lastMop(WED - 7 * 864e5) }))));
      const f = mkFlow({ rr_mop_pending: true });
      return { notify: run("f_mopfail", r[2], f)[0], flow: f };
    },
    "mop give-up (f_mopchk n>=40)": () => {
      const r = run("f_mopchk", { rr_mop_docks: 40 }, mkFlow({}),
        mkGlobal(state({ "binary_sensor.mr_slave_cleaning_2": { state: "on", last_changed: new Date().toISOString() } })));
      const f = mkFlow({ rr_mop_pending: true });
      return { notify: run("f_mopfail", r[2], f)[0], flow: f };
    },
  };
  for (const [name, fn] of Object.entries(cases)) {
    const { notify, flow } = fn();
    ok(notify && notify.notify && notify.notify.tag === "rr_mop_failed" && notify.notify.urgent === true,
       `${name} -> one rr_mop_failed notify`);
    ok(flow.get("rr_mop_pending") === false, `${name} -> clears rr_mop_pending (no double-notify via stall)`);
  }
  // f_mopfail does NOT stamp last_mop_run (routes to restore+done, not via f_64/s_65)
  ok(JSON.stringify(byId["f_mopfail"].wires) === JSON.stringify([["lo_n_mopfail"], ["s_66"]]), "f_mopfail out1 -> s_66 (restore), never s_65/last_mop_run");
}

console.log("Wiring -- structural rewires and deletions");
{
  ok(!byId["w_rc"], "w_rc deleted (replaced by d_rcpoll/f_rcpoll)");
  ok(!byId["lo_stop_62"] && !byId["lo_n_mop"], "orphaned link-outs removed");
  eq(byId["f_rc"].wires, [["d_55", "f_ev_restdone"], ["d_rcpoll", "lo_tr_rch"], ["d_55"], ["lo_n_short"]],
     "f_rc recharge -> d_rcpoll, short sweep -> d_55 (no restdone) + notify");
  eq(byId["f_58"].wires, [["w_mopbat"], ["s_67"], ["f_mopfail"]], "f_58 out2 -> f_mopfail");
  eq(byId["f_mopchk"].wires, [["s_63"], ["w_mopoff"], ["f_mopfail"]], "f_mopchk out2 -> f_mopfail");
  eq(byId["w_mopbat"].wires, [["lo_mop"], ["f_mopbat_to"]], "w_mopbat timeout -> f_mopbat_to");
  eq(byId["w_mopoff"].wires, [["d_mopchk"], ["f_mopoff_to"]], "w_mopoff timeout -> f_mopoff_to");
  eq(byId["f_to_w_62"].wires, [["f_mopfail"]], "f_to_w_62 -> f_mopfail");
  // arm nodes spliced before each dock-wait; w_62 success guarded by g_w62
  eq(byId["s_37"].wires, [["arm_42"]], "s_37 -> arm_42");
  eq(byId["s_43"].wires, [["arm_48", "f_ev_zb"]], "s_43 -> arm_48 (+f_ev_zb)");
  eq(byId["s_49"].wires, [["arm_54", "f_ev_rest"]], "s_49 -> arm_54 (+f_ev_rest)");
  eq(byId["s_61"].wires, [["arm_62", "f_ev_mop"]], "s_61 -> arm_62 (+f_ev_mop)");
  eq(byId["arm_42"].wires, [["w_42"]], "arm_42 -> w_42");
  eq(byId["arm_48"].wires, [["w_48"]], "arm_48 -> w_48");
  eq(byId["arm_54"].wires, [["w_54"]], "arm_54 -> w_54");
  eq(byId["arm_62"].wires, [["w_62"]], "arm_62 -> w_62");
  eq(byId["w_62"].wires, [["g_w62"], ["f_to_w_62"]], "w_62 success -> g_w62");
  eq(byId["g_w62"].wires, [["d_mopchk"]], "g_w62 -> d_mopchk");
  eq(byId["f_rcpoll"].wires, [["arm_54", "f_ev_rest"], ["d_55"], ["lo_n_rc"], ["d_rcpoll"]], "f_rcpoll resume re-arms via arm_54");
  eq(byId["f_hareconn"].wires, [["f_short_A"], ["f_short_B"], ["d_rc"], ["g_w62"]], "f_hareconn out3 -> g_w62");
}


// ===========================================================================
// 2026-11-04: mop catch-up / routine-end / water / short-sweep fixes
const DAY = 864e5;
const at = (d, h, m) => new Date(2026, 10, d, h, m || 0, 0).getTime();   // November 2026, local
const MON = at(2, 9), TUE = at(3, 9), WED2 = at(4, 9), THU2 = at(5, 9), FRI = at(6, 9), SAT = at(7, 12);
const PREV_WED_MOP = at(4, 10) - 7 * DAY;     // Wed 10-28 10:00 (a good mop last week)
const OLD_MOP = at(4, 10) - 12 * DAY;         // Fri 10-23 10:00 (the stamp before 11-03)
const TUE_MOP = at(3, 10, 4);                 // Tue 11-03 10:04 (the catch-up that drained the tank)
const gMop = (lastMopMs, extra) => mkGlobal(Object.assign(state({ "input_datetime.rr_last_mop_run": lastMop(lastMopMs) }), extra || {}));
const mopGate = (now, lastMopMs, flowInit) => withDate(now, () => run("f_58", {}, mkFlow(flowInit || {}), gMop(lastMopMs)));
const isMop = (r) => !!(r[0] && !r[1] && !r[2]);
const isVacOnly = (r) => !!(!r[0] && r[1] && !r[2]);

console.log("A -- rrMopDue helper is one rule in every node");
{
  const re = /\/\/ --- rrMopDue:[\s\S]*?\/\/ --- end rrMopDue ---/;
  const copies = ["f_35", "f_pre", "f_58", "f_nx"].map((id) => (byId[id].func.match(re) || [null])[0]);
  ok(copies.every((c) => c), "rrMopDue block present in f_35, f_pre, f_58, f_nx");
  ok(copies.every((c) => c === copies[0]), "rrMopDue block byte-identical in all four nodes");
  ok(!flows.some((n) => n.func && /wedMid/.test(n.func)), "old wedMid rule gone from every node");
}

console.log("A -- f_58 mop gate: no Mon/Tue catch-up, Thu/Fri catch-up, 48 h rule");
{
  ok(isVacOnly(mopGate(MON, OLD_MOP)), "Mon with a stale stamp -> no catch-up (vacuum only)");
  ok(isVacOnly(mopGate(TUE, OLD_MOP)), "Tue with a stale stamp -> no catch-up (the 11-03 double mop)");
  ok(isVacOnly(mopGate(SAT, OLD_MOP)), "Sat with a stale stamp -> no catch-up");
  ok(isMop(mopGate(WED2, PREV_WED_MOP)), "Wed, last mop a week ago -> mop");
  ok(isMop(mopGate(WED2, OLD_MOP)), "Wed, last mop 12 days ago -> mop");
  ok(isVacOnly(mopGate(WED2, TUE_MOP)), "Wed within 48 h of a Tue mop -> scheduled mop skipped");
  ok(isMop(mopGate(THU2, PREV_WED_MOP)), "Thu after a missed Wed -> catch-up mop");
  ok(isMop(mopGate(FRI, PREV_WED_MOP)), "Fri after a missed Wed (and Thu) -> catch-up mop");
  ok(isVacOnly(mopGate(THU2, at(4, 10))), "Thu after a good Wed -> no double mop");
  ok(isVacOnly(mopGate(FRI, at(4, 10))), "Fri after a good Wed -> no double mop");
  ok(isVacOnly(mopGate(THU2, TUE_MOP)), "Thu after a Tue mop (Wed skipped) -> week already mopped");
  ok(isVacOnly(mopGate(FRI, TUE_MOP)), "Fri after a Tue mop (Wed skipped) -> week already mopped");
  ok(isVacOnly(mopGate(THU2, at(3, 20))), "Thu 09:00 after a Tue 20:00 mop -> within 48 h, no mop");
  // catch-up day still honours the start-of-run water gate
  const r = mopGate(THU2, PREV_WED_MOP, { rr_mop_skip: true });
  ok(!r[0] && !r[1] && r[2] && /skipped/.test(r[2].rr_mop_reason), "Thu catch-up with an empty tank at start -> skip -> mop-failed");
  // f_35 / f_pre use the same answer
  const msg35 = withDate(TUE, () => run("f_35", {}, mkFlow({}), gMop(OLD_MOP)));
  ok(msg35.mopDay === false, "f_35 Tue stale stamp -> mopDay false");
  ok(withDate(THU2, () => run("f_35", {}, mkFlow({}), gMop(PREV_WED_MOP))).mopDay === true, "f_35 Thu catch-up -> mopDay true");
  const fp = mkFlow({});
  withDate(TUE, () => run("f_pre", {}, fp, gMop(OLD_MOP)));
  ok(fp.get("rr_mop_pending") === false, "f_pre Tue stale stamp -> rr_mop_pending false");
  const fp2 = mkFlow({});
  const pre = withDate(WED2, () => run("f_pre", {}, fp2, gMop(PREV_WED_MOP, state({ "binary_sensor.mr_slave_water_shortage_2": "on" }))));
  ok(fp2.get("rr_mop_skip") === true && /mop pass will be skipped/.test(pre[1].notify.message), "f_pre Wed + empty tank -> skip + notice");
  // f_35 resets the mop-pass water watch for a new run
  const f35 = mkFlow({ rr_mop_inpass: 123, rr_mop_water: true });
  withDate(TUE, () => run("f_35", {}, f35, gMop(OLD_MOP)));
  ok(f35.get("rr_mop_inpass") === 0 && f35.get("rr_mop_water") === false, "f_35 clears the water-watch flags");
}

console.log("A -- f_nx next-mop display follows the same rule");
{
  const nextMop = (now, g) => {
    const out = withDate(now, () => run("f_nx", {}, mkFlow({}), g))[0];
    const m = out.find((o) => o.payload.ent === "input_datetime.rr_next_mop");
    return m && m.payload.ts * 1000;
  };
  eq(nextMop(TUE, gMop(OLD_MOP)), at(4, 8, 30), "Tue, stale stamp -> next mop Wed 08:30 (not today)");
  eq(nextMop(WED2, gMop(PREV_WED_MOP)), at(5, 8, 30), "Wed after the 08:30 slot, not mopped -> Thu 08:30 catch-up");
  eq(nextMop(THU2, gMop(at(4, 10))), at(11, 8, 30), "Thu after a good Wed -> next Wed");
  eq(nextMop(MON, gMop(at(1, 12) - DAY)), at(4, 8, 30), "Mon after a Sat mop -> Wed (outside 48 h)");
  eq(nextMop(TUE, mkGlobal({})), at(4, 8, 30), "states not loaded -> plain next Wednesday");
}

console.log("B -- f_mopchk: routine end needs a 20-min quiet window + robot home");
{
  const T = at(3, 10, 5);
  const iso = (ms) => new Date(ms).toISOString();
  const g = (offMs, extra) => mkGlobal(Object.assign(state({
    "binary_sensor.mr_slave_cleaning_2": { state: "off", last_changed: iso(offMs) },
    "sensor.mr_slave_status_2": "charging", "vacuum.mr_slave_2": "docked",
    "binary_sensor.mr_slave_water_shortage_2": "off" }), extra || {}));
  const chk = (msg, flow, gl) => withDate(T, () => run("f_mopchk", msg, flow || mkFlow({}), gl));
  // the 11-03 case: 3-5 min into an 8-min gap between routine steps -> NOT done
  let r = chk({}, null, g(T - 4 * 60e3));
  ok(!r[0] && r[1] && !r[2], "4-min gap between routine steps (docked) -> keep waiting, no stamp");
  r = chk({}, null, g(T - 19 * 60e3));
  ok(!r[0] && r[1], "19 min quiet -> still waiting");
  const fdone = mkFlow({ rr_mop_inpass: T - 3 * 3600e3 });
  r = chk({}, fdone, g(T - 21 * 60e3));
  ok(r[0] && !r[1] && !r[2], "21 min quiet + docked/charging -> pass done (out0 -> stamp)");
  ok(fdone.get("rr_mop_inpass") === 0, "pass done closes the water watch");
  r = chk({}, null, g(T - 30 * 60e3, state({ "vacuum.mr_slave_2": "idle", "sensor.mr_slave_status_2": "idle" })));
  ok(!r[0] && r[1], "30 min quiet but robot not home -> not done");
  r = chk({}, null, g(T - 30 * 60e3, state({ "sensor.mr_slave_status_2": "washing_the_mop" })));
  ok(!r[0] && r[1], "30 min quiet but dock washing -> not done");
  // polling through the quiet window doesn't burn the dock-cycle budget
  const gq = g(T - 5 * 60e3);
  const m1 = chk({}, null, gq)[1];
  const m2 = chk(m1, null, gq)[1];
  ok(m1.rr_mop_docks === 1 && m2.rr_mop_docks === 1 && m2.rr_mop_polls === 2, "same off-transition polled twice -> 1 dock, 2 polls");
  // poll cap: off the dock forever -> give up via f_mopfail
  r = chk({ rr_mop_polls: 119, rr_mop_docks: 3 }, null, g(T - 30 * 60e3, state({ "vacuum.mr_slave_2": "idle", "sensor.mr_slave_status_2": "idle" })));
  ok(!r[0] && !r[1] && r[2] && /6 h/.test(r[2].rr_mop_reason), "120 checks without settling -> give up (out2)");
  // tank ran dry -> failed, never done
  const fw = mkFlow({ rr_mop_inpass: T - 3600e3 });
  r = chk({}, fw, g(T - 25 * 60e3, state({ "binary_sensor.mr_slave_water_shortage_2": "on" })));
  ok(!r[0] && !r[1] && r[2] && /ran out/.test(r[2].rr_mop_reason), "water_shortage on at the check -> mop failed, not done");
  ok(fw.get("rr_mop_water") === true, "water failure flagged for the rest of the pass");
  // after the water watch fired, the loop stops silently
  eq(chk({}, mkFlow({ rr_mop_water: true }), g(T - 25 * 60e3)), [null, null, null], "rr_mop_water set -> loop ends, no second outcome");
}

console.log("B -- mid-pass water watch: t_mopwater -> f_mopwater -> f_mopfail");
{
  const t = byId["t_mopwater"];
  ok(t && t.type === "server-state-changed" && t.entities.entity[0] === "binary_sensor.mr_slave_water_shortage_2" && t.ifState === "on",
     "t_mopwater watches water_shortage_2 -> on");
  ok(t.ignorePrevStateUnavailable && t.ignoreCurrentStateUnavailable && t.ignorePrevStateUnknown, "t_mopwater ignores unavailable/unknown");
  eq(t.wires, [["f_mopwater"], []], "t_mopwater -> f_mopwater");
  eq(byId["f_mopwater"].wires, [["f_mopfail"]], "f_mopwater -> f_mopfail");
  ok(byId["g_sweep"].nodes.includes("t_mopwater") && byId["g_sweep"].nodes.includes("f_mopwater"), "new nodes are in g_sweep");
  // f_ev_mop arms the watch
  const fe = mkFlow({ rr_mop_water: true });
  withDate(TUE, () => run("f_ev_mop", {}, fe));
  ok(fe.get("rr_mop_inpass") === TUE && fe.get("rr_mop_water") === false, "f_ev_mop arms the water watch");
  // water runs out 2 h 18 min into the pass (11-03 12:06)
  const f = mkFlow({ rr_mop_inpass: at(3, 9, 48), rr_run: { active: true }, rr_mop_pending: true });
  const m = withDate(at(3, 12, 6), () => run("f_mopwater", { payload: "on" }, f));
  ok(m && m.rr_mop_water === true && /138 min into the mop pass/.test(m.rr_mop_reason), "f_mopwater mid-pass -> mop-failed msg with minutes");
  const out = run("f_mopfail", m, f);
  ok(out[0] && out[0].notify.tag === "rr_mop_failed" && out[0].notify.urgent === true && out[1], "-> one urgent rr_mop_failed + restore");
  ok(/Thursday or Friday/.test(out[0].notify.message), "mop-failed wording names the Thu/Fri catch-up");
  // later failure paths of the same pass are dropped (no second alert)
  eq(run("f_mopfail", run("f_mopoff_to", {}, f), f), [null, null], "later routine timeout after the water alert -> dropped");
  // outside a pass / no active run / already flagged -> nothing
  eq(run("f_mopwater", {}, mkFlow({ rr_mop_inpass: 0, rr_run: { active: true } })), null, "water shortage outside a mop pass -> ignored");
  eq(run("f_mopwater", {}, mkFlow({ rr_mop_inpass: 5, rr_run: { active: false } })), null, "no active run -> ignored");
  eq(run("f_mopwater", {}, mkFlow({ rr_mop_inpass: 5, rr_run: { active: true }, rr_mop_water: true })), null, "already flagged -> ignored");
  // the watch never stamps rr_last_mop_run: f_mopfail still routes to restore only
  eq(byId["f_mopfail"].wires, [["lo_n_mopfail"], ["s_66"]], "f_mopfail wiring unchanged (never s_65)");
}

console.log("C -- f_rc: a short sweep does not stamp the 12 rooms");
{
  const T = at(3, 9, 25);
  const g = (extra) => mkGlobal(Object.assign(state({ "sensor.mr_slave_battery_2": "66", "sensor.mr_slave_cleaning_progress_2": "0" }), extra || {}));
  const rc = (msg, gl) => withDate(T, () => run("f_rc", Object.assign({ rr_wgen: 1 }, msg), mkFlow({ rr_waitgen: 1 }), gl || g()));
  // the 11-03 case: home ~3.5 min after the sweep started, progress 0
  let r = rc({ rr_sweep_t0: T - 210e3 });
  ok(!r[0] && !r[1] && r[2] && r[3], "3.5-min sweep -> out2 (d_55, no restdone) + out3 notify");
  ok(r[3].notify.tag === "rr_sweep_short" && /3\.5 min/.test(r[3].notify.message), "short-sweep notice says how long");
  r = rc({ rr_sweep_t0: T - 75 * 60e3 });
  ok(r[0] && !r[1] && !r[2] && !r[3], "75-min sweep -> done, rooms stamped (out0)");
  r = rc({}, g(state({ "sensor.mr_slave_cleaning_time_2": "102" })));
  ok(!r[0] && r[2], "no start time on the msg -> falls back to cleaning_time (102 s) -> short");
  r = rc({}, g());
  ok(r[0] && !r[2], "no start time and no cleaning_time -> unchanged behaviour (stamp)");
  r = rc({ rr_sweep_t0: T - 4 * 60e3 }, g(state({ "sensor.mr_slave_battery_2": "14", "sensor.mr_slave_cleaning_progress_2": "40" })));
  ok(!r[0] && r[1] && !r[2], "low battery mid-sweep -> recharge path unchanged");
  // arm_54 stamps the sweep start once and keeps it across a resume
  const m = {};
  withDate(T - 60e3, () => run("arm_54", m, mkFlow({})));
  const first = m.rr_sweep_t0;
  withDate(T, () => run("arm_54", m, mkFlow({})));
  ok(first === T - 60e3 && m.rr_sweep_t0 === first, "arm_54 sets rr_sweep_t0 once (kept across a resume)");
  ok(byId["li_notify"].links.includes("lo_n_short") && byId["lo_n_short"].links[0] === "li_notify", "lo_n_short <-> li_notify linked");
}

// ===========================================================================
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

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
 *
 * 2026-11-04 (fix/roborock-dashboard-2, after the robot's own mop routine):
 *   - f_ev_idle: error -> idle is not "Stopped by hand"
 *   - f_track 'job': a robot-started job replaces the last run's final phase
 *
 * 2026-11-07 (feat/roborock-dock-watcher, after zone A could not dock):
 *   - stage dock watcher f_dw: retry once, alert, hold 30 min, continue or cancel
 *
 * 2026-11-12 (feat/roborock-mop-day-mop-only, owner: "Wednesday just mops"):
 *   - f_pre out 2: a mop-due day with a clean dock skips stages 1-4, mop pass only
 *   - dock/water problem on a mop day -> normal vacuum run; non-mop days unchanged
 *
 * 2026-11-18 (fix/roborock-hand-stop-and-mop-steps, owner design interview):
 *   - hand stop (person / robot button / stop / dashboard cancel) cancels the
 *     rest of the run, ends the robot's job, docks it, one quiet note
 *   - Node-RED runs the 7 mop steps itself; nothing presses button.mr_slave_mopping
 *   - rrMopDue = Wednesday only (no Thu/Fri catch-up); rr_last_mop_run only after 7/7
 *   - "Mop all floors" runs the same 7 steps on any day
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FLOWS = path.join(__dirname, "..", "flows.json");
const SETTINGS = path.join(__dirname, "..", "settings.js");
const flows = JSON.parse(fs.readFileSync(FLOWS, "utf8"));
const byId = Object.fromEntries(flows.map((n) => [n.id, n]));
// Rachel's office as its own last job (2026-11-05)
const OFFICE = {
  settle: "9622cf35f026db07", empty: "01106f5dbc94d4f5", collect: "94d58a61d89a1ad3",
  cancel: "ca1916c27f53f848", clean: "81b9a3c1de858b4b", arm: "04fdb2cbff7f4717",
  wait: "207dee7e272ef8ca", done: "00542ec58ae4b642", timeout: "901de950dde3341c",
  evt: "6c199d35c11dfb67", evtDone: "91b1b1f09a52fd7f",
  armBat: "0a16ecfa0db17379", waitBat: "48ec4456670321ea", guardBat: "e51e862e40c73c54",
  batTimeout: "9029a00410cdd730", modeSet: "2ee2d6a013693735", modeSettle: "48ad94fcac20fbaa",
  modeCheck: "767583b46867c07c", modeRetry: "0b65a9ea45b037e3",
};

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
// f_ev_idle (now the hand-stop classifier) is covered in section H below.

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
  // mop step phase: the steps poll HA state (f_mstepchk), nothing to recover
  f = mkFlow({ rr_run: { active: true, phase: "Mop pass 3/7 — Living room" }, rr_pending: "", rr_waitgen: 7 });
  eq(run("f_hareconn", { payload: { event: "running" } }, f, g), [null, null, null, null], "reconnect during a mop step -> nothing");
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
}

console.log("Item 3 -- startup check (f_boot) + f_track interrupted (no stamps)");
{
  // no active run
  eq(run("f_boot", {}, mkFlow({ rr_run: { active: false } }), mkGlobal(state({ "sensor.mr_slave_status_2": "charging" }))),
     [null, null], "f_boot no active run -> nothing");
  // active + not cleaning -> interrupted evt (out0) + rr_restart notify (out1)
  let r = run("f_boot", {}, mkFlow({ rr_run: { active: true, phase: "Main floor sweep (11 rooms)" } }),
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
  // sanity: 'restdone' DOES stamp the 11 sweep rooms (so we know the no-stamp result is meaningful)
  const f2 = mkFlow({ rr_run: { active: true, phase: "x", cancelled: false, near: "" } });
  const tr2 = run("f_track", { rr_evt: "restdone" }, f2);
  ok(tr2[1].length === 1 && Array.isArray(tr2[1][0].payload.stamp) && tr2[1][0].payload.stamp.length === 11 && !tr2[1][0].payload.stamp.some((s) => /rachels_office/.test(s)), "f_track restdone -> 11 room stamps, office not among them");
}

console.log("Item 4a -- f_58 mop due / no catch-up / no double mop");
{
  const g = (lastMopMs) => mkGlobal(state({ "input_datetime.rr_last_mop_run": lastMop(lastMopMs) }));
  // Wednesday, last mop a week ago -> due, proceed (out0)
  let r = withDate(WED, () => run("f_58", {}, mkFlow({}), g(WED - 7 * 864e5)));
  ok(r[0] && !r[1] && !r[2], "Wednesday -> mop proceeds (out0)");
  // Thursday, last mop was PREVIOUS week (failed Wednesday) -> no catch-up since 2026-11-18
  r = withDate(THU, () => run("f_58", {}, mkFlow({}), g(WED - 7 * 864e5)));
  ok(!r[0] && r[1] && !r[2], "Thursday after a missed Wednesday -> no catch-up (out1, vacuum only)");
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
  const viaFail = (m) => { const f = mkFlow({ rr_mop_pending: true }); return { notify: run("f_mopfail", m, f)[0], flow: f }; };
  const cases = {
    "battery timeout (f_mopbat_to)": () => chain("f_mopbat_to", {}),
    "mop-day skip (f_58)": () => {
      const r = withDate(WED, () => run("f_58", {}, mkFlow({ rr_mop_skip: true }),
        mkGlobal(state({ "input_datetime.rr_last_mop_run": lastMop(WED - 7 * 864e5) }))));
      return viaFail(r[2]);
    },
    "tank empty at pass start (f_59)": () => {
      const r = run("f_59", {}, mkFlow({}), mkGlobal(state({ "binary_sensor.mr_slave_water_shortage_2": "on" })));
      return viaFail(r[1]);
    },
    "step cap 240 min (f_mstepchk)": () => {
      const T = Date.now();
      const r = withDate(T, () => run("f_mstepchk", { rr_mopgen: 1, rr_mop_idx: 2, rr_step_t0: T - 241 * 60e3, rr_step_started: T - 230 * 60e3 },
        mkFlow({ rr_mopgen: 1 }), mkGlobal(state({ "binary_sensor.mr_slave_cleaning_2": "on", "sensor.mr_slave_status_2": "segment_cleaning", "vacuum.mr_slave_2": "cleaning" }))));
      return viaFail(r[2]);
    },
    "step retries used up (f_mretry)": () => {
      const r = run("f_mretry", { rr_mopgen: 1, rr_step_retry: 4, error: { message: "Connection lost" } }, mkFlow({ rr_mopgen: 1 }));
      return viaFail(r[1]);
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
  eq(byId["f_rc"].wires, [[OFFICE.settle, "f_ev_restdone"], ["d_rcpoll", "lo_tr_rch"], ["d_55"], ["lo_n_short"]],
     "f_rc done -> office stage (+restdone), recharge -> d_rcpoll, short sweep -> d_55 (no restdone, no office) + notify");
  eq(byId["f_58"].wires, [["w_mopbat"], ["s_67"], ["f_mopfail"]], "f_58 out2 -> f_mopfail");
  eq(byId["w_mopbat"].wires, [["lo_mop"], ["f_mopbat_to"]], "w_mopbat timeout -> f_mopbat_to");
  // the routine-based mop pass is gone (2026-11-18)
  for (const id of ["s_60", "s_61", "arm_62", "w_62", "g_w62", "d_mopchk", "f_mopchk", "w_mopoff", "f_mopoff_to", "f_to_w_62", "s_69", "f_ev_mop"]) {
    ok(!byId[id], `${id} removed (routine mop pass)`);
  }
  ok(!byId["s_29"] && !byId["s_30"] && !byId["f_ev_cxl"] && !byId["lo_tr_cxl"], "old cancel chain removed (f_28 -> f_hstop)");
  // arm nodes spliced before each dock-wait
  eq(byId["s_37"].wires, [["arm_42"]], "s_37 -> arm_42");
  eq(byId["s_43"].wires, [["arm_48", "f_ev_zb"]], "s_43 -> arm_48 (+f_ev_zb)");
  eq(byId["s_49"].wires, [["arm_54", "f_ev_rest"]], "s_49 -> arm_54 (+f_ev_rest)");
  eq(byId["arm_42"].wires, [["w_42"]], "arm_42 -> w_42");
  eq(byId["arm_48"].wires, [["w_48"]], "arm_48 -> w_48");
  eq(byId["arm_54"].wires, [["w_54"]], "arm_54 -> w_54");
  eq(byId["f_rcpoll"].wires, [["arm_54", "f_ev_rest"], ["d_55"], ["lo_n_rc"], ["d_rcpoll"]], "f_rcpoll resume re-arms via arm_54");
  eq(byId["f_hareconn"].wires, [["f_short_A"], ["f_short_B"], ["d_rc"], [OFFICE.done]], "f_hareconn out3 -> office done? (no mop wait)");
  ok(byId["f_hareconn"].outputs === 4, "f_hareconn has 4 outputs");
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

console.log("A -- f_58 mop gate: Wednesday only (no catch-up since 2026-11-18), 48 h rule");
{
  ok(isVacOnly(mopGate(MON, OLD_MOP)), "Mon with a stale stamp -> no mop (vacuum only)");
  ok(isVacOnly(mopGate(TUE, OLD_MOP)), "Tue with a stale stamp -> no mop (the 11-03 double mop)");
  ok(isVacOnly(mopGate(SAT, OLD_MOP)), "Sat with a stale stamp -> no mop");
  ok(isVacOnly(mopGate(at(8, 12), OLD_MOP)), "Sun with a stale stamp -> no mop");
  ok(isMop(mopGate(WED2, PREV_WED_MOP)), "Wed, last mop a week ago -> mop");
  ok(isMop(mopGate(WED2, OLD_MOP)), "Wed, last mop 12 days ago -> mop");
  ok(isMop(mopGate(WED2, 0)), "Wed, never mopped -> mop");
  ok(isVacOnly(mopGate(WED2, TUE_MOP)), "Wed within 48 h of a Tue mop -> scheduled mop skipped");
  ok(isVacOnly(mopGate(THU2, PREV_WED_MOP)), "Thu after a missed Wed -> NO catch-up");
  ok(isVacOnly(mopGate(FRI, PREV_WED_MOP)), "Fri after a missed Wed (and Thu) -> NO catch-up");
  ok(isVacOnly(mopGate(THU2, at(4, 10))), "Thu after a good Wed -> no double mop");
  ok(isVacOnly(mopGate(FRI, at(4, 10))), "Fri after a good Wed -> no double mop");
  // f_35 / f_pre use the same answer
  const msg35 = withDate(TUE, () => run("f_35", {}, mkFlow({}), gMop(OLD_MOP)));
  ok(msg35.mopDay === false, "f_35 Tue stale stamp -> mopDay false");
  ok(withDate(THU2, () => run("f_35", {}, mkFlow({}), gMop(PREV_WED_MOP))).mopDay === false, "f_35 Thu after a missed Wed -> mopDay false");
  ok(withDate(WED2, () => run("f_35", {}, mkFlow({}), gMop(PREV_WED_MOP))).mopDay === true, "f_35 Wed -> mopDay true");
  const f35m = mkFlow({ rr_mop_manual: true });
  withDate(TUE, () => run("f_35", {}, f35m, gMop(OLD_MOP)));
  ok(f35m.get("rr_mop_manual") === false, "f_35 clears rr_mop_manual (daily run)");
  ok(!/Thu\/Fri|catchDay|weekStart/.test(byId["f_58"].func), "Thu/Fri catch-up branch gone from rrMopDue");
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
  eq(nextMop(WED2, gMop(PREV_WED_MOP)), at(11, 8, 30), "Wed after the 08:30 slot, not mopped -> next Wed (no Thu catch-up)");
  eq(nextMop(at(4, 7), gMop(PREV_WED_MOP)), at(4, 8, 30), "Wed 07:00, not mopped -> today 08:30");
  eq(nextMop(THU2, gMop(at(4, 10))), at(11, 8, 30), "Thu after a good Wed -> next Wed");
  eq(nextMop(MON, gMop(at(1, 12) - DAY)), at(4, 8, 30), "Mon after a Sat mop -> Wed (outside 48 h)");
  eq(nextMop(TUE, mkGlobal({})), at(4, 8, 30), "states not loaded -> plain next Wednesday");
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
  // f_59 (mop pass start) arms the watch
  const fe = mkFlow({ rr_mop_water: true });
  withDate(TUE, () => run("f_59", {}, fe, mkGlobal(state({ "binary_sensor.mr_slave_water_shortage_2": "off" }))));
  ok(fe.get("rr_mop_inpass") === TUE && fe.get("rr_mop_water") === false, "f_59 arms the water watch");
  // water runs out 2 h 18 min into the pass (11-03 12:06)
  const f = mkFlow({ rr_mop_inpass: at(3, 9, 48), rr_run: { active: true }, rr_mop_pending: true, rr_mopgen: 3 });
  const m = withDate(at(3, 12, 6), () => run("f_mopwater", { payload: "on" }, f));
  ok(m && m.rr_mop_water === true && /138 min into the mop pass/.test(m.rr_mop_reason), "f_mopwater mid-pass -> mop-failed msg with minutes");
  const out = run("f_mopfail", m, f);
  ok(out[0] && out[0].notify.tag === "rr_mop_failed" && out[0].notify.urgent === true && out[1], "-> one urgent rr_mop_failed + restore");
  ok(/next Wednesday/.test(out[0].notify.message) && !/Thursday|Friday|catch-up mop runs/.test(out[0].notify.message),
     "mop-failed wording: no catch-up, next Wednesday");
  ok(f.get("rr_mopgen") === 4, "f_mopfail bumps rr_mopgen (the step poll of this pass stops)");
  // the step poll of the same pass now stops silently
  eq(run("f_mstepchk", { rr_mopgen: 3 }, f), [null, null, null, null], "step poll after the water alert -> dropped");
  // later failure paths of the same pass are dropped (no second alert)
  eq(run("f_mopfail", run("f_mopbat_to", {}, f), f), [null, null], "later failure after the water alert -> dropped");
  // outside a pass / no active run / already flagged -> nothing
  eq(run("f_mopwater", {}, mkFlow({ rr_mop_inpass: 0, rr_run: { active: true } })), null, "water shortage outside a mop pass -> ignored");
  eq(run("f_mopwater", {}, mkFlow({ rr_mop_inpass: 5, rr_run: { active: false } })), null, "no active run -> ignored");
  eq(run("f_mopwater", {}, mkFlow({ rr_mop_inpass: 5, rr_run: { active: true }, rr_mop_water: true })), null, "already flagged -> ignored");
  // the watch never stamps rr_last_mop_run: f_mopfail still routes to restore only
  eq(byId["f_mopfail"].wires, [["lo_n_mopfail"], ["s_66"]], "f_mopfail wiring unchanged (never s_65)");
}

console.log("C -- f_rc: a short sweep does not stamp the 11 rooms");
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
console.log("D -- robot-started jobs replace a stale phase (error -> idle: see section H)");
{
  // cleaning_2 -> on trigger wiring
  const t = byId["t_jobon"];
  ok(t && t.type === "server-state-changed" && t.entities.entity[0] === "binary_sensor.mr_slave_cleaning_2" && t.ifState === "on",
     "t_jobon watches cleaning_2 -> on");
  ok(t.ignorePrevStateUnavailable === true && t.ignorePrevStateUnknown === true && t.ignoreCurrentStateUnavailable === true,
     "t_jobon ignores unavailable/unknown");
  ok(t.wires[0][0] === "f_ev_job" && byId["f_ev_job"].wires[0][0] === "f_track", "t_jobon -> f_ev_job -> f_track");
  ok(byId["g_track"].nodes.includes("t_jobon") && byId["g_track"].nodes.includes("f_ev_job"), "new nodes in the tracker group");
  eq(run("f_ev_job", {}).rr_evt, "job", "f_ev_job emits job");
  // no Node-RED run: the stale phase is replaced, near cleared
  const f1 = mkFlow({ rr_run: { active: false, phase: "Stopped by hand — rest of run cancelled", cancelled: true, near: "Bedroom1" } });
  const r1 = run("f_track", { rr_evt: "job" }, f1);
  ok(/outside the daily run/.test(r1[0][0].payload.phase) && r1[0][0].payload.near === "", "job with no run -> phase replaced, near cleared");
  ok(r1[1].length === 0, "job -> no room stamps");
  // second flip (wash visit) writes the same text
  const r1b = run("f_track", { rr_evt: "job" }, f1);
  eq(r1b[0][0].payload.phase, r1[0][0].payload.phase, "repeat job flip -> same phase");
  // inside a Node-RED run or mop pass: untouched
  const f2 = mkFlow({ rr_run: { active: true, phase: "Main floor sweep (11 rooms)", cancelled: false, near: "" } });
  eq(run("f_track", { rr_evt: "job" }, f2), null, "job during an active run -> ignored");
  eq(f2.get("rr_run").phase, "Main floor sweep (11 rooms)", "active run phase kept");
  const f3 = mkFlow({ rr_run: { active: false, phase: "Mop pass (all floors)", cancelled: false, near: "" }, rr_mop_inpass: 1 });
  eq(run("f_track", { rr_evt: "job" }, f3), null, "job during a mop pass -> ignored");
}

// ===========================================================================
console.log("E -- Rachel's office runs as its own last job (2026-11-05)");
{
  const segs = (id) => JSON.parse(byId[id].data).params[0].segments;
  eq(segs("s_49"), [1, 4, 5, 3, 13, 12, 11, 6, 7, 8, 10], "main sweep: 11 segments, no office");
  eq(segs(OFFICE.clean), [9], "office job: segment 9 only");
  ok(JSON.parse(byId[OFFICE.clean].data).command === "app_segment_clean", "office job is app_segment_clean");
  ok(byId["cat_mid"].scope.includes(OFFICE.clean), "office job failure -> catch mid-run (alert + cancel)");
  ok(!byId["cat_mid"].scope.includes(OFFICE.empty), "office empty stays best-effort (not caught)");
  // chain: f_rc done -> settle -> empty + collect -> cancel? -> clean -> arm -> wait -> done? -> d_55
  eq(byId[OFFICE.settle].wires, [[OFFICE.empty, OFFICE.collect]], "settle -> empty + collect");
  ok(JSON.parse(byId[OFFICE.empty].data).command === "app_start_collect_dust", "office empty = collect dust");
  eq(byId[OFFICE.collect].wires, [[OFFICE.cancel]], "collect -> cancel?");
  eq(byId[OFFICE.cancel].wires, [[OFFICE.armBat]], "cancel? -> arm battery wait");
  eq(byId[OFFICE.armBat].wires, [[OFFICE.waitBat]], "arm -> wait battery");
  const wb = byId[OFFICE.waitBat];
  ok(wb.type === "ha-wait-until" && wb.entityId === "sensor.mr_slave_battery_2" && wb.property === "state"
     && wb.comparator === "gte" && wb.value === "30" && wb.valueType === "num" && wb.timeout === "90"
     && wb.timeoutUnits === "minutes" && wb.checkCurrentState === true, "wait battery >=30%, 90 min, checks current state");
  eq(wb.wires, [[OFFICE.guardBat], [OFFICE.batTimeout]], "battery ok -> guard, timeout -> skip");
  eq(byId[OFFICE.guardBat].wires, [[OFFICE.modeSet]], "guard -> mode: vacuum (O)");
  eq(byId[OFFICE.batTimeout].wires, [["d_55"], ["lo_tr_restd"], ["lo_n_short"]], "battery timeout -> d_55 + tracker + notify (no stall)");
  // mode chain mirrors (R): set vacuum -> settle -> check -> one retry -> clean
  for (const [o, r] of [["modeSet", "s_mv_R"], ["modeRetry", "s_mv2_R"]]) {
    eq(byId[OFFICE[o]].data, byId[r].data, `${o} same call as ${r}`);
  }
  eq(byId[OFFICE.modeSettle].timeout, byId["d_mv_R"].timeout, "mode settle same timing as (R)");
  eq(byId[OFFICE.modeSet].wires, [[OFFICE.modeSettle]], "mode set -> settle");
  eq(byId[OFFICE.modeSettle].wires, [[OFFICE.modeCheck]], "settle -> check");
  eq(byId[OFFICE.modeCheck].wires, [[OFFICE.clean], [OFFICE.modeRetry]], "check -> clean / retry");
  eq(byId[OFFICE.modeRetry].wires, [[OFFICE.clean]], "retry -> clean");
  ok(byId[OFFICE.modeCheck].func === byId["f_mv_R"].func.replace("(R)", "(O)"), "mode check body = f_mv_R");
  let mc = run(OFFICE.modeCheck, {}, mkFlow({}), mkGlobal(state({ "select.mr_slave_cleaning_mode": "vac_and_mop" })));
  ok(!mc[0] && mc[1], "mode still vac_and_mop -> retry");
  mc = run(OFFICE.modeCheck, {}, mkFlow({}), mkGlobal(state({ "select.mr_slave_cleaning_mode": "vacuum" })));
  ok(mc[0] && !mc[1], "mode vacuum -> clean");
  ok(byId["cat_mid"].scope.includes(OFFICE.modeSet) && byId["cat_mid"].scope.includes(OFFICE.modeRetry), "office mode calls caught like (R)");
  eq(byId[OFFICE.clean].wires, [[OFFICE.arm, OFFICE.evt]], "clean office -> arm + evt office");
  eq(byId[OFFICE.arm].wires, [[OFFICE.wait]], "arm -> wait dock (office)");
  const w = byId[OFFICE.wait];
  ok(w.type === "ha-wait-until" && w.entityId === "sensor.mr_slave_status_2" && w.value === "charging"
     && w.timeout === "40" && w.checkCurrentState === false, "wait dock (office): charging, 40 min, change-only");
  eq(w.wires, [[OFFICE.done], [OFFICE.timeout]], "wait success -> done?, timeout -> timeout fn");
  eq(byId[OFFICE.done].wires, [["d_55", OFFICE.evtDone], ["d_55"], ["lo_n_short"]], "done? -> d_55 (+officedone) / d_55 / notify");
  eq(byId[OFFICE.timeout].wires, [["lo_stop_54"]], "office timeout -> stop");
  eq(byId[OFFICE.evt].wires, [["lo_tr_rest"]], "evt office -> tracker");
  eq(byId[OFFICE.evtDone].wires, [["lo_tr_restd"]], "evt officedone -> tracker");
  const sweep = byId["g_sweep"].nodes;
  ok(Object.values(OFFICE).every((id) => sweep.includes(id)), "office nodes are in group 3");
  // per-room button for the office is untouched
  eq(segs("s_18"), [9], "room button: Rachel's office still segment 9");

  // cancel between stages
  eq(run(OFFICE.cancel, {}, mkFlow({ rr_cancel: true })), null, "cancel? drops when the run is cancelled");
  ok(run(OFFICE.cancel, { a: 1 }, mkFlow({})).a === 1, "cancel? passes otherwise");

  // arm + done guard
  const T = new Date(2026, 10, 5, 10, 0, 0).getTime();
  const f = mkFlow({ rr_waitgen: 4 });
  const m = {};
  withDate(T, () => run(OFFICE.arm, m, f));
  ok(f.get("rr_waitgen") === 5 && f.get("rr_pending") === OFFICE.wait && m.rr_wgen === 5 && m.rr_office_t0 === T,
     "arm: gen bump, pending = office wait, start time on msg");
  let r = withDate(T + 9 * 60e3, () => run(OFFICE.done, Object.assign({}, m), f));
  ok(r[0] && !r[1] && !r[2] && f.get("rr_pending") === "", "9-min office -> done (out0, stamps)");
  r = withDate(T + 60e3, () => run(OFFICE.done, Object.assign({}, m), mkFlow({ rr_waitgen: 5 })));
  ok(!r[0] && r[1] && r[2] && r[2].notify.tag === "rr_short_zone", "1-min office -> no stamp, run moves on, notice");
  eq(run(OFFICE.done, { rr_wgen: 3 }, mkFlow({ rr_waitgen: 5 })), null, "stale office wait success dropped");
  eq(run(OFFICE.done, { rr_wgen: 5 }, mkFlow({ rr_waitgen: 5, rr_cancel: true })), null, "office done dropped when cancelled");
  ok(run(OFFICE.done, { rr_wgen: 5 }, mkFlow({ rr_waitgen: 5 }))[0], "no start time (reconnect recovery) -> done");
  const to = run(OFFICE.timeout, { rr_wgen: 5 }, mkFlow({ rr_waitgen: 5 }));
  ok(to && to.rr_stage === "Rachel's office (last)" && to.rr_mins === 40, "office timeout -> stall msg");
  eq(run(OFFICE.timeout, { rr_wgen: 4 }, mkFlow({ rr_waitgen: 5 })), null, "stale office timeout dropped");

  // battery gate: generation guard + skip path
  const fb = mkFlow({ rr_waitgen: 7 });
  const bm = {};
  run(OFFICE.armBat, bm, fb);
  ok(fb.get("rr_waitgen") === 8 && fb.get("rr_pending") === OFFICE.waitBat && bm.rr_wgen === 8, "battery arm: gen bump + pending");
  ok(run(OFFICE.guardBat, Object.assign({}, bm), fb).rr_wgen === 8 && fb.get("rr_pending") === "", "battery ok passes guard, clears pending");
  eq(run(OFFICE.guardBat, { rr_wgen: 7 }, mkFlow({ rr_waitgen: 8 })), null, "stale battery success dropped");
  eq(run(OFFICE.guardBat, { rr_wgen: 8 }, mkFlow({ rr_waitgen: 8, rr_cancel: true })), null, "battery ok dropped when cancelled");
  const sk = run(OFFICE.batTimeout, { rr_wgen: 8 }, mkFlow({ rr_waitgen: 8 }));
  ok(sk[0] && sk[1].rr_evt === "officeskip" && sk[2].notify.tag === "rr_office_skipped" && sk[2].notify.urgent === false,
     "battery timeout -> continue + officeskip + notice");
  ok(!("rr_stage" in sk[0]), "battery timeout is not a stall");
  eq(run(OFFICE.batTimeout, { rr_wgen: 7 }, mkFlow({ rr_waitgen: 8 })), null, "stale battery timeout dropped");
  const fs1 = mkFlow({ rr_run: { active: true, phase: "Dock — emptying before Rachel's office", cancelled: false, near: "" } });
  const ts = run("f_track", { rr_evt: "officeskip" }, fs1);
  ok(ts[0][0].payload.phase === "Rachel's office skipped (battery)" && ts[1].length === 0, "officeskip phase, no stamp");
  const gh = mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "vacuum.mr_slave_2": "docked" }));
  eq(run("f_hareconn", { payload: { event: "states_loaded" } },
     mkFlow({ rr_run: { active: true, phase: "Dock — emptying before Rachel's office" }, rr_pending: OFFICE.waitBat, rr_waitgen: 8 }), gh),
     [null, null, null, null], "reconnect during the battery wait -> nothing (wait re-checks on change)");

  // tracker: phases + the office stamp
  const ft = mkFlow({ rr_run: { active: true, phase: "Main floor sweep (11 rooms)", cancelled: false, near: "" } });
  let tr = run("f_track", { rr_evt: "restdone" }, ft);
  eq(tr[0][0].payload.phase, "Dock — emptying before Rachel's office", "restdone phase");
  tr = run("f_track", { rr_evt: "office" }, ft);
  eq(tr[0][0].payload.phase, "Rachel's office (last)", "office phase");
  ok(tr[1].length === 0, "office start stamps nothing");
  tr = run("f_track", { rr_evt: "officedone" }, ft);
  ok(tr[1].length === 1 && tr[1][0].payload.stamp === "input_datetime.rr_last_clean_rachels_office", "officedone stamps the office");
  eq(tr[0][0].payload.phase, "Main sweep complete", "officedone phase");
  const fc = mkFlow({ rr_run: { active: true, phase: "Rachel's office (last)", cancelled: false, near: "" }, rr_cancel: true });
  ok(run("f_track", { rr_evt: "officedone" }, fc)[1].length === 0, "officedone on a cancelled run stamps nothing");

  // HA reconnect while the office wait is pending -> out4
  const g = mkGlobal(state({ "sensor.mr_slave_status_2": "charging", "vacuum.mr_slave_2": "docked" }));
  const fr = mkFlow({ rr_run: { active: true, phase: "Rachel's office (last)" }, rr_pending: OFFICE.wait, rr_waitgen: 5 });
  r = run("f_hareconn", { payload: { event: "states_loaded" } }, fr, g);
  ok(!r[0] && !r[1] && !r[2] && r[3] && r[3].rr_wgen === 6, "reconnect office -> out3 with bumped gen");
  ok(Array.isArray(run(OFFICE.done, r[3], fr)), "recovery msg passes office done?");
  const fr2 = mkFlow({ rr_run: { active: true, phase: "Dock — emptying before Rachel's office" }, rr_pending: "", rr_waitgen: 5 });
  eq(run("f_hareconn", { payload: { event: "states_loaded" } }, fr2, g), [null, null, null, null], "reconnect during the empty -> nothing");
}

// ===========================================================================
// 2026-11-07 stage dock watcher (f_dw, group 9), after zone A came home and
// could not dock: 20 min of shuffling beside the dock, the alert only at 40 min.
console.log("Stage dock watcher -- wiring and timings");
{
  const tab = flows.filter((n) => n.z === "rr_tab_1");
  const ids = new Set(flows.map((n) => n.id));
  // every wire / link / group member in the tab points at a node that exists
  for (const n of tab) {
    for (const w of n.wires || []) for (const t of w) ok(ids.has(t), `${n.id} wire -> ${t} exists`);
    for (const t of n.links || []) ok(ids.has(t), `${n.id} link -> ${t} exists`);
    for (const t of n.nodes || []) ok(ids.has(t), `${n.id} member ${t} exists`);
  }
  eq(byId["f_dw"].wires, [["f_short_A"], ["f_short_B"], ["d_rc"], [OFFICE.done], ["s_dw_ret"],
     ["lo_n_dw"], ["lo_tr_dw"], ["lo_stop_dw"]], "f_dw outputs wired");
  ok(byId["f_dw"].outputs === 8, "f_dw has 8 outputs");
  eq(byId["t_dw"].wires, [["f_dw"]], "t_dw -> f_dw");
  eq(byId["t_dw"].entities.entity, ["sensor.mr_slave_status_2"], "t_dw watches the status sensor");
  ok(byId["t_dw"].ignorePrevStateUnavailable && byId["t_dw"].ignoreCurrentStateUnavailable, "t_dw ignores unavailable");
  ok(byId["i_dw"].repeat === "30" && byId["i_dw"].wires[0][0] === "f_dw", "i_dw ticks f_dw every 30 s");
  ok(byId["s_dw_ret"].action === "vacuum.return_to_base", "s_dw_ret = vacuum.return_to_base");
  ok(byId["li_notify"].links.includes("lo_n_dw") && byId["li_tr"].links.includes("lo_tr_dw") &&
     byId["li_stop"].links.includes("lo_stop_dw"), "link-ins list the new link-outs");
  const C = (byId["f_dw"].func.match(/const C = (\{[^}]*\})/) || [])[1];
  eq(C, "{ LEFT_MIN: 3, RETURN_MAX_MIN: 6, RETRY_MIN: 5, HOLD_MIN: 30 }", "dock watcher timings");
  // stage caps unchanged (they bound a stage that never comes home; zone A alone takes 20-26 min)
  eq(["w_42", "w_48", "w_54", OFFICE.wait].map((i) => byId[i].timeout), ["40", "40", "150", "40"], "stage caps");
  for (const id of ["f_to_w_42", "f_to_w_48", "f_to_w_54", OFFICE.timeout]) {
    ok(byId[id].func.includes("_dw.force = true"), `${id} hands a near-dock timeout to f_dw`);
  }
}

console.log("Stage dock watcher -- behaviour");
{
  const T0 = Date.UTC(2026, 10, 7, 18, 26, 30);
  const M = 60000;
  const S = (status, vac, extra) => mkGlobal(state(Object.assign({
    "sensor.mr_slave_status_2": status, "vacuum.mr_slave_2": vac, "sensor.mr_slave_battery_2": "75",
    "binary_sensor.rr_recharge_pause": "off" }, extra || {})));
  const runFlow = (pend, extra) => mkFlow(Object.assign({
    rr_run: { active: true, phase: "Master bedroom — zone A", cancelled: false }, rr_pending: pend, rr_waitgen: 3 }, extra || {}));
  const dw = (f, g, t) => withDate(t, () => run("f_dw", { payload: "tick" }, f, g));
  const only = (r, i) => r.every((x, j) => (j === i ? x : x === null));

  // 11-07 replay: returning from 18:26:30 and never charging
  let f = runFlow("w_42");
  ok(dw(f, S("returning_home", "returning"), T0).every((x) => x === null), "returning: nothing yet");
  ok(dw(f, S("returning_home", "returning"), T0 + 5.5 * M).every((x) => x === null), "returning 5.5 min: nothing yet");
  let r = dw(f, S("returning_home", "returning"), T0 + 6 * M);
  ok(r[4] && r[6] && r[6].rr_phase === "Docking retry — Master bedroom zone A" && !r[5], "returning 6 min -> one return_to_base + retry phase, no alert yet");
  ok(f.get("rr_waitgen") === 4 && f.get("rr_pending") === "" && f.get("rr_dw").state === "retry", "takeover bumps gen + clears pending");
  eq(run("f_to_w_42", { rr_wgen: 3 }, f), null, "old w_42 timeout dropped after takeover");
  eq(run("f_short_A", { rr_wgen: 3 }, f), null, "old w_42 success dropped after takeover");
  ok(dw(f, S("returning_home", "returning"), T0 + 10.5 * M).every((x) => x === null), "retry 4.5 min: still waiting");
  r = dw(f, S("idle", "idle"), T0 + 11 * M);
  ok(r[5] && r[5].notify.urgent === true && r[5].notify.tag === "rr_dock_failed", "5 min after the retry -> one urgent rr_dock_failed alert");
  ok(r[5].notify.message.startsWith("Roborock could not dock after Master bedroom zone A — needs a hand (battery 75 %)."), "alert text");
  ok(r[6] && r[6].rr_phase === "Could not dock — Master bedroom zone A", "phase Could not dock — <stage>");
  ok(!r[4] && !r[7], "no second retry, no cancel");
  ok(dw(f, S("idle", "idle"), T0 + 30 * M).every((x) => x === null), "holding: no repeat alert");
  // owner re-docks within the hold -> run continues to zone B's path
  r = dw(f, S("charging", "docked"), T0 + 40 * M);
  ok(r[0] && r[0].rr_wgen === 5 && r[5] && r[5].notify.urgent === false && !r[7], "docked in the hold -> continue + back-on-dock note");
  const cont = withDate(T0 + 40 * M, () => run("f_short_A", r[0], mkFlow({ rr_waitgen: 5, rr_zone_t: T0 - 26 * M })));
  ok(Array.isArray(cont) && cont[0], "continue msg passes f_short_A's guard");
  eq(f.get("rr_dw"), { state: "idle" }, "watcher reset after continue");

  // hold runs out -> f_stall cancels with the could-not-dock wording
  f = runFlow("w_48");
  dw(f, S("returning_home", "returning"), T0);
  dw(f, S("idle", "idle"), T0 + 1 * M);
  ok(dw(f, S("idle", "idle"), T0 + 3.5 * M).every((x) => x === null), "idle 2.5 min after the return: nothing yet");
  r = dw(f, S("idle", "idle"), T0 + 4 * M);
  ok(r[4] && r[6].rr_phase === "Docking retry — Master bedroom zone B", "idle 3 min after a return -> retry");
  r = dw(f, S("idle", "idle"), T0 + 9 * M);
  ok(r[5] && r[5].notify.urgent, "alert 8 min after it went idle");
  r = dw(f, S("idle", "idle"), T0 + 39 * M);
  ok(only(r, 7) && r[7].rr_nodock && r[7].rr_stage === "Master bedroom zone B" && r[7].rr_mins === 30, "hold ran out -> stall");
  const sf = mkFlow({});
  const s = run("f_stall", r[7], sf, S("idle", "idle"));
  ok(s[0].notify.tag === "rr_run_failed" && s[0].notify.urgent && /could not dock/.test(s[0].notify.message), "f_stall: rr_run_failed, could-not-dock wording");
  eq(s[1].rr_phase, "Could not dock — Master bedroom zone B", "f_stall phase keeps the could-not-dock text");
  ok(sf.get("rr_cancel") === true, "f_stall cancels the run");
  eq(run("f_stall", { rr_stage: "Main floor sweep", rr_mins: 150 }, mkFlow({}), S("segment_cleaning", "cleaning"))[1].rr_phase,
     "Stalled — Main floor sweep", "plain stall keeps Stalled — <stage>");

  // docked during the retry (before the alert): continue, no notify; office and sweep route right
  f = runFlow(OFFICE.wait);
  dw(f, S("returning_home", "returning"), T0);
  dw(f, S("returning_home", "returning"), T0 + 6 * M);
  r = dw(f, S("charging", "docked"), T0 + 12 * M);
  ok(only(r, 3) && r[3].rr_wgen === 5, "office: docked on the retry -> out3 only, no note");
  f = runFlow("w_54");
  dw(f, S("returning_home", "returning"), T0);
  dw(f, S("returning_home", "returning"), T0 + 6 * M);
  r = dw(f, S("charging", "docked"), T0 + 11 * M);
  ok(only(r, 2), "main sweep: docked on the retry -> out2 (d_rc) only");

  // normal docking, recharge pause, cleaning again, no run, cancel: no action
  f = runFlow("w_42");
  dw(f, S("returning_home", "returning"), T0);
  ok(dw(f, S("charging", "docked"), T0 + 2 * M).every((x) => x === null) && !f.get("rr_dw").retAt, "normal dock: nothing, return clock cleared");
  f = runFlow("w_54");
  ok(dw(f, S("charging", "docked", { "binary_sensor.rr_recharge_pause": "on" }), T0 + 60 * M).every((x) => x === null), "recharge pause: nothing");
  dw(f, S("returning_home", "returning"), T0);
  dw(f, S("segment_cleaning", "cleaning"), T0 + 1 * M);
  ok(dw(f, S("paused", "paused"), T0 + 20 * M).every((x) => x === null), "paused mid-clean (no return since) -> nothing");
  f = mkFlow({ rr_run: { active: false }, rr_pending: "w_42", rr_waitgen: 3 });
  dw(f, S("returning_home", "returning"), T0);
  ok(dw(f, S("idle", "idle"), T0 + 20 * M).every((x) => x === null), "no active run -> nothing");
  f = runFlow("w_62");
  dw(f, S("returning_home", "returning"), T0);
  ok(dw(f, S("idle", "idle"), T0 + 20 * M).every((x) => x === null), "mop wait is not watched");
  f = runFlow("w_42");
  dw(f, S("returning_home", "returning"), T0);
  dw(f, S("returning_home", "returning"), T0 + 6 * M);
  f.set("rr_cancel", true);
  ok(dw(f, S("idle", "idle"), T0 + 16 * M).every((x) => x === null) && f.get("rr_dw").state === "idle", "cancelled during the retry -> silent reset");

  // the stage cap runs out while the robot is near the dock -> hand-off, not a stall
  f = runFlow("w_42");
  dw(f, S("returning_home", "returning"), T0);
  eq(run("f_to_w_42", { rr_wgen: 3 }, f), null, "w_42 timeout near the dock -> no stall");
  ok(f.get("rr_dw").force === true && f.get("rr_pending") === "w_42", "hand-off flag set, wait still pending");
  r = dw(f, S("returning_home", "returning"), T0 + 1 * M);
  ok(r[4] && f.get("rr_dw").state === "retry", "next tick takes over");
  f = runFlow("w_42");
  dw(f, S("zoned_cleaning", "cleaning"), T0);
  ok(run("f_to_w_42", { rr_wgen: 3 }, f).rr_stage === "Master bedroom zone A", "w_42 timeout while still cleaning -> plain stall");

  // the old any-time retry loop (f_dockevt) stands aside during a watched stage
  const ev = { payload: "idle", data: { old_state: { state: "returning" } } };
  eq(run("f_dockevt", ev, runFlow("w_42")), null, "f_dockevt: watched stage -> stands aside");
  eq(run("f_dockevt", ev, mkFlow({ rr_pending: "", rr_dw: { state: "hold", wid: "w_42" } })), null, "f_dockevt: watcher holding -> stands aside");
  const fo = mkFlow({ rr_pending: "" });
  r = run("f_dockevt", ev, fo);
  ok(r && r[0] && fo.get("rr_dock").busy === true, "f_dockevt: outside a run -> its 3-retry loop as before");
}

// ===========================================================================
// 2026-11-12 (owner: "Wednesday just mops"): a mop-due day skips stages 1-4
console.log("F -- mop day: mop pass only, no vacuum stages");
{
  // every node reachable from a list of node ids (wires + link out -> link in)
  const reach = (start) => {
    const seen = new Set(), todo = [...start];
    while (todo.length) {
      const id = todo.pop();
      if (seen.has(id) || !byId[id]) continue;
      seen.add(id);
      for (const out of byId[id].wires || []) todo.push(...out);
      if (byId[id].type === "link out") todo.push(...(byId[id].links || []));
    }
    return seen;
  };
  const STAGE_JOBS = ["s_37", "s_43", "s_49", OFFICE.clean];   // zone A, zone B, 11-room sweep, office

  // wiring: f_pre out 2 -> tracker start + mop gate; scheduled and button runs share f_34 -> f_35 -> f_pre
  eq(byId["f_pre"].outputs, 3, "f_pre has 3 outputs");
  eq(byId["f_pre"].wires, [["lo_runA", "f_ev_start"], ["lo_n_pre"], ["f_ev_start", "f_58"]],
     "f_pre out0 -> vacuum run, out1 -> notify, out2 -> evt start + f_58 mop gate");
  ok(byId["i_31"].wires[0].includes("f_34") && byId["t_33"].wires[0].includes("f_34"),
     "8:30 inject and Daily clean button both enter via f_34");
  eq(byId["f_34"].wires, [["f_35"]], "f_34 -> f_35");
  eq(byId["f_35"].wires, [["f_pre"]], "f_35 -> f_pre");
  const mopPath = reach(byId["f_pre"].wires[2]);
  ok(STAGE_JOBS.every((id) => !mopPath.has(id)), "mop-only path reaches no zone A/B, sweep or office job");
  ok(mopPath.has("w_mopbat") && mopPath.has("f_mstep") && mopPath.has("s_mseg"), "mop-only path reaches the battery wait and the Node-RED mop steps");
  const vacPath = reach(byId["f_pre"].wires[0]);
  ok(STAGE_JOBS.every((id) => vacPath.has(id)) && vacPath.has("f_58"), "vacuum path still runs stages 1-4 then the mop gate");

  // Wednesday, clean dock -> out 2 only, then f_58 starts the mop pass
  let fp = mkFlow({});
  let r = withDate(WED2, () => run("f_pre", {}, fp, gMop(PREV_WED_MOP)));
  ok(!r[0] && !r[1] && r[2] && r[2].rr_mop_only === true, "Wed + clean dock -> mop-only output, no vacuum stage");
  ok(fp.get("rr_mop_skip") === false && fp.get("rr_mop_pending") === true, "Wed + clean dock -> mop pending, not skipped");
  ok(isMop(withDate(WED2, () => run("f_58", r[2], fp, gMop(PREV_WED_MOP)))), "f_58 on the mop-only msg -> battery wait -> mop pass");

  // no Thursday catch-up (2026-11-18): Thursday after a missed Wednesday is a normal vacuum run
  r = withDate(THU2, () => run("f_pre", {}, mkFlow({}), gMop(PREV_WED_MOP)));
  ok(r[0] && !r[2], "Thu after a missed Wed + clean dock -> vacuum run, no mop");

  // Wednesday after the mop already ran (Daily clean pressed again) -> normal vacuum run
  r = withDate(at(4, 18), () => run("f_pre", {}, mkFlow({}), gMop(at(4, 16))));
  ok(r[0] && !r[2], "Wed, mop done today -> vacuum run");

  // Wednesday, water gate fails -> normal vacuum run; mop gate then reports the failure
  fp = mkFlow({});
  r = withDate(WED2, () => run("f_pre", {}, fp, gMop(PREV_WED_MOP, state({ "binary_sensor.mr_slave_water_shortage_2": "on" }))));
  ok(r[0] && !r[2] && r[0].rr_mop_only === undefined, "Wed + empty tank -> vacuum run (out 0), not mop-only");
  ok(r[1] && /mop pass will be skipped/.test(r[1].notify.message), "Wed + empty tank -> preflight notice");
  r = withDate(WED2, () => run("f_58", {}, fp, gMop(PREV_WED_MOP)));
  ok(!r[0] && !r[1] && r[2] && /dock had a problem/.test(r[2].rr_mop_reason), "after the vacuum run f_58 -> f_mopfail (rr_mop_failed)");
  fp = mkFlow({});
  r = withDate(WED2, () => run("f_pre", {}, fp, gMop(PREV_WED_MOP, state({ "sensor.mr_slave_dock_dock_error": "water_empty" }))));
  ok(r[0] && !r[2], "Wed + dock error -> vacuum run");

  // non-mop day unchanged
  fp = mkFlow({});
  r = withDate(TUE, () => run("f_pre", {}, fp, gMop(PREV_WED_MOP)));
  ok(r[0] && !r[1] && !r[2] && r[0].rr_mop_only === undefined, "Tue -> vacuum run only, as before");
  ok(isVacOnly(withDate(TUE, () => run("f_58", r[0], fp, gMop(PREV_WED_MOP)))), "Tue -> f_58 vacuum only");

  // tracker: mop-only start phase; a whole mop-only run stamps no room
  const ft = mkFlow({});
  let t = run("f_track", { rr_evt: "start", rr_mop_only: true }, ft);
  ok(/^Mop day/.test(ft.get("rr_run").phase) && t[1].length === 0, "f_track mop-only start -> 'Mop day' phase, no stamp");
  ok(!/zone A/.test(ft.get("rr_run").phase), "mop-only start phase does not name zone A");
  t = run("f_track", { rr_evt: "mopstep", rr_phase: "Mop pass 1/7 — Front door entry" }, ft);
  eq(ft.get("rr_run").phase, "Mop pass 1/7 — Front door entry", "mopstep event -> per-step phase");
  t = run("f_track", { rr_evt: "done" }, ft);
  ok(t[1].length === 0 && ft.get("rr_run").phase === "Complete", "mop-only done -> Complete, no room stamps");
  const fv = mkFlow({});
  run("f_track", { rr_evt: "start" }, fv);
  eq(fv.get("rr_run").phase, "Master bedroom — zone A", "normal start phase unchanged");
}

// ===========================================================================
// 2026-11-18 (owner design interview): hand stop, Node-RED mop steps, manual mop
const NR = "1da6c199cd2a41b799695107b1bf6de3";      // Node-RED's own HA user (learned by a_uid at runtime)
const PERSON = "603f5da2aaaaaaaaaaaaaaaaaaaaaaaa";   // a household member (shape only)
const LIVE = () => ({ rr_run: { active: true, phase: "Main floor sweep (11 rooms)", cancelled: false, near: "" } });
const chg = (old, nu, uid) => ({ payload: nu, data: { old_state: { state: old }, new_state: { state: nu, context: { id: "c", user_id: uid || null } } } });
const classify = (old, nu, uid, flowExtra, now) => withDate(now || Date.now(),
  () => run("f_ev_idle", chg(old, nu, uid), mkFlow(Object.assign(LIVE(), { rr_nr_uid: NR }, flowExtra || {}))));
const fires = (r) => !!(r && r.rr_src === "hand");

console.log("H1 -- hand-stop classifier (f_ev_idle) fires");
{
  const t = byId["t_idle"];
  ok(t.entities.entity[0] === "vacuum.mr_slave_2" && t.ifState === "", "t_idle watches every vacuum.mr_slave_2 state change");
  ok(t.outputProperties.some((p) => p.property === "data" && p.valueType === "eventData"), "t_idle outputs msg.data (old/new state + context)");
  eq(t.wires, [["f_ev_idle"]], "t_idle -> f_ev_idle");
  eq(byId["f_ev_idle"].wires, [["f_hstop"]], "f_ev_idle -> f_hstop");
  // (i) a person on the change (HA card, HomeKit, dashboard)
  ok(fires(classify("cleaning", "paused", PERSON)), "(i) cleaning -> paused by a person (HA card pause, 11-14 13:20)");
  ok(fires(classify("paused", "returning", PERSON)), "(i) paused -> returning by a person");
  ok(fires(classify("cleaning", "returning", PERSON)), "(i) cleaning -> returning by a person (11-18 18:09)");
  ok(fires(classify("cleaning", "idle", PERSON)), "(i) cleaning -> idle by a person (11-15 12:11)");
  ok(fires(classify("returning", "idle", PERSON)), "(i) returning -> idle by a person (11-18 18:25 stop)");
  ok(fires(classify("docked", "paused", PERSON)), "(i) pause while docked mid-step");
  // (ii) the robot's own button: paused -> returning / docked with no user
  ok(fires(classify("paused", "returning", null)), "(ii) paused -> returning, no user (robot button, 11-18 17:54)");
  ok(fires(classify("paused", "docked", null)), "(ii) paused -> docked, no user");
  // (iii) a stop -> idle
  ok(fires(classify("cleaning", "idle", null)), "(iii) cleaning -> idle, no user");
  ok(fires(classify("docked", "idle", null)), "(iii) docked -> idle still fires");
  // the own-user id is not learned yet: a user id counts as a person outside the own-command window
  const r = withDate(Date.now(), () => run("f_ev_idle", chg("cleaning", "paused", PERSON), mkFlow(LIVE())));
  ok(fires(r), "rr_nr_uid unknown + no own command -> a user id is a person");
  // mop passes are live runs too
  ok(fires(withDate(Date.now(), () => run("f_ev_idle", chg("cleaning", "paused", PERSON),
    mkFlow({ rr_run: { active: true, phase: "Mop pass 3/7 — Living room" }, rr_nr_uid: NR })))), "fires during a mop step");
}

console.log("H2 -- hand-stop classifier must not fire");
{
  ok(!fires(classify("cleaning", "error", null)), "cleaning -> error");
  ok(!fires(classify("error", "paused", null)), "error -> paused (robot's own recovery, 11-18 09:52)");
  ok(!fires(classify("paused", "cleaning", null)), "paused -> cleaning (recovery resumes)");
  ok(!fires(classify("error", "idle", null)), "error -> idle (error cleared)");
  ok(!fires(classify("cleaning", "returning", null)), "cleaning -> returning, no user (natural end / recharge / mid-job wash)");
  ok(!fires(classify("returning", "docked", null)), "returning -> docked, no user");
  ok(!fires(classify("returning", "idle", null)), "returning -> idle, no user (gave up docking: group 8 / 9)");
  ok(!fires(classify("docked", "cleaning", NR)), "docked -> cleaning by Node-RED (next stage)");
  ok(!fires(classify("cleaning", "returning", NR)), "Node-RED return_to_base (cancel path)");
  ok(!fires(classify("idle", "returning", NR)), "Node-RED return_to_base from idle (group 8, 11-18 18:47:41)");
  ok(!fires(classify("cleaning", "idle", NR)), "Node-RED's own stop");
  ok(!fires(classify("paused", "returning", null, { rr_dw: { state: "retry" } })), "group 9 dock-watcher retry from paused, no user on the change");
  ok(!fires(classify("paused", "returning", null, { rr_dock: { busy: true } })), "group 8 retry loop busy, no user on the change");
  const T = Date.now();
  ok(!fires(classify("paused", "returning", null, { rr_own_cmd_at: T - 60e3 }, T)), "within 2 min of Node-RED's own command, no user");
  ok(fires(classify("paused", "returning", null, { rr_own_cmd_at: T - 3 * 60e3 }, T)), "own command 3 min ago -> the button counts again");
  ok(!fires(withDate(T, () => run("f_ev_idle", chg("cleaning", "returning", NR), mkFlow(Object.assign(LIVE(), { rr_dock: { busy: true } }))))),
     "rr_nr_uid unknown + group 8 busy -> its user id is Node-RED");
  // no live run: nothing (outside a run, a cancelled run, a second press of the same stop)
  eq(run("f_ev_idle", chg("cleaning", "paused", PERSON), mkFlow({ rr_run: { active: false }, rr_nr_uid: NR })), null, "no run -> nothing");
  eq(run("f_ev_idle", chg("paused", "returning", PERSON), mkFlow(Object.assign(LIVE(), { rr_cancel: true, rr_nr_uid: NR }))), null,
     "already cancelled (second press of the same stop) -> nothing");
  eq(run("f_ev_idle", chg("cleaning", "cleaning", PERSON), mkFlow(Object.assign(LIVE(), { rr_nr_uid: NR }))), null, "attribute-only change -> nothing");
}

console.log("H3 -- f_hstop: cancel, end the job, dock, one quiet note");
{
  const S = (status, vac, cleaning) => mkGlobal(state({ "sensor.mr_slave_status_2": status, "vacuum.mr_slave_2": vac,
    "binary_sensor.mr_slave_cleaning_2": cleaning }));
  ok(byId["f_hstop"].outputs === 5, "f_hstop has 5 outputs");
  eq(byId["f_hstop"].wires, [["s_hs_stop"], ["d_hs"], ["lo_n_hs"], ["w_restore"], ["lo_tr_hs"]], "f_hstop wiring");
  ok(byId["s_hs_stop"].action === "vacuum.stop" && byId["s_hs_ret"].action === "vacuum.return_to_base", "stop + return_to_base calls");
  eq(byId["s_hs_stop"].wires, [["d_hs"]], "stop -> settle 30 s");
  ok(byId["d_hs"].timeout === "30", "d_hs = 30 s");
  eq(byId["d_hs"].wires, [["f_hs_home"]], "d_hs -> f_hs_home");
  eq(byId["f_hs_home"].wires, [["s_hs_stop"], ["s_hs_ret"]], "f_hs_home -> stop again / return_to_base");
  eq(byId["cat_hs"].scope, ["s_hs_stop", "s_hs_ret"], "cat_hs catches the stop and the return");
  eq(byId["cat_hs"].wires, [["d_hs"]], "a failed stop / return loops back via d_hs");
  ok(byId["li_notify"].links.includes("lo_n_hs") && byId["li_tr"].links.includes("lo_tr_hs"), "link-ins list the hand-stop link-outs");
  eq(byId["f_28"].wires, [["f_hstop"]], "dashboard cancel -> f_hstop");
  ok(run("f_28", {}).rr_src === "button", "f_28 marks the dashboard button");

  // mid-step hand stop (robot cleaning): everything cancelled, stop sent, note once
  const f = mkFlow(Object.assign(LIVE(), { rr_waitgen: 5, rr_pending: "w_54", rr_mopgen: 2, rr_mop_inpass: 9 }));
  let o = run("f_hstop", { rr_src: "hand", rr_hand: "person" }, f, S("segment_cleaning", "cleaning", "on"));
  ok(f.get("rr_cancel") === true && f.get("rr_waitgen") === 6 && f.get("rr_pending") === "" && f.get("rr_mopgen") === 3 && f.get("rr_mop_inpass") === 0,
     "cancel flag, wait gen + mop gen bumped, pending cleared, water watch off");
  ok(o[0] && !o[1], "job open -> vacuum.stop first");
  ok(o[2] && o[2].notify.urgent === false && o[2].notify.tag === "rr_hand_stop" && o[2].notify.title === "Vacuum: stopped by hand",
     "one quiet rr_hand_stop note");
  ok(/rest of today's run is cancelled/.test(o[2].notify.message) && /Tap Daily clean to restart/.test(o[2].notify.message), "note text");
  ok(o[3] && o[3].payload === "restore", "restore mode once home (w_restore)");
  eq(o[4], { rr_evt: "idle" }, "tracker: Stopped by hand");
  // the run's stale waits are dropped by their existing guards
  eq(run("f_rc", { rr_wgen: 5 }, f), [null, null, null, null], "stale w_54 success dropped after a hand stop");
  eq(run("f_short_A", { rr_wgen: 6 }, f), null, "even a current-gen dock-wait is dropped (rr_cancel)");
  // second event of the same stop sequence: no run is live any more -> no second note
  const fr = mkFlow({ rr_run: { active: false, cancelled: true }, rr_cancel: true });
  o = run("f_hstop", { rr_src: "hand" }, fr, S("returning_home", "returning", "on"));
  ok(!o[2], "second stop of the same sequence -> no second note");
  // already docked with no job open: no stop, straight to the home check
  o = run("f_hstop", { rr_src: "hand" }, mkFlow(LIVE()), S("charging", "docked", "off"));
  ok(!o[0] && o[1], "docked, no job -> no stop, home check only");
  // docked with the job still open (pause + dock, the self-resume case): stop it
  o = run("f_hstop", { rr_src: "hand" }, mkFlow(LIVE()), S("charging", "docked", "on"));
  ok(o[0], "docked with the job open -> stop (it cannot resume by itself)");
  // already returning after the robot button: stop, then return
  o = run("f_hstop", { rr_src: "hand" }, mkFlow(LIVE()), S("returning_home", "returning", "on"));
  ok(o[0], "returning with the job paused -> stop first");
  // dashboard cancel outside a run: dock it, no note
  const fb = mkFlow({ rr_run: { active: false } });
  o = run("f_hstop", { rr_src: "button" }, fb, S("segment_cleaning", "cleaning", "on"));
  ok(o[0] && !o[2] && o[4].rr_evt === "cancel", "cancel button outside a run -> stop + dock, no note, tracker cancel");
  // dashboard cancel during a run: note once
  o = run("f_hstop", { rr_src: "button" }, mkFlow(LIVE()), S("segment_cleaning", "cleaning", "on"));
  ok(o[2] && o[2].notify.urgent === false && o[4].rr_evt === "cancel", "cancel button during a run -> one quiet note");
  // manual mop wording
  o = run("f_hstop", { rr_src: "hand" }, mkFlow(Object.assign(LIVE(), { rr_mop_manual: true })), S("segment_cleaning", "cleaning", "on"));
  ok(/Tap Mop all floors to restart/.test(o[2].notify.message), "manual mop note says Mop all floors");
  // tracker phases
  const ft = mkFlow(LIVE());
  run("f_track", { rr_evt: "idle" }, ft);
  ok(ft.get("rr_run").phase === "Stopped by hand — rest of run cancelled" && ft.get("rr_run").active === false, "tracker: stopped by hand");

  // f_hs_home: get it home, safely
  const home = (status, vac, cleaning, msg, fl) => run("f_hs_home", msg || {}, fl || mkFlow({ rr_cancel: true }), S(status, vac, cleaning));
  eq(home("charging", "docked", "off"), [null, null], "docked -> nothing");
  eq(home("washing_the_mop", "docked", "off"), [null, null], "washing on the dock -> nothing");
  eq(home("returning_home", "returning", "off"), [null, null], "heading home with the job ended -> nothing");
  let h = home("idle", "idle", "off");
  ok(!h[0] && h[1], "idle on the floor after the stop -> return_to_base");
  h = home("paused", "paused", "on");
  ok(h[0] && !h[1], "job still open -> stop again");
  eq(home("idle", "idle", "off", { rr_hs_try: 4 }), [null, null], "gives up after 4 tries");
  eq(home("idle", "idle", "off", {}, mkFlow({ rr_cancel: false })), [null, null], "a new run started meanwhile -> nothing");
}

console.log("H4 -- no stage or mop step after a cancel");
{
  const cx = () => mkFlow({ rr_cancel: true, rr_mopgen: 1 });
  for (const id of ["f_mv_A", "f_mv_B", "f_mv_R", OFFICE.modeCheck]) {
    eq(run(id, {}, cx(), mkGlobal(state({ "select.mr_slave_cleaning_mode": "vacuum" }))), [null, null], `${id} drops when cancelled (no next stage)`);
  }
  eq(run("f_59", {}, cx()), [null, null], "f_59: no mop pass after a cancel");
  eq(run("f_mstep", { rr_mopgen: 1, rr_mop_idx: 3 }, cx()), [null, null, null], "f_mstep: no next step after a cancel");
  eq(run("f_mstepchk", { rr_mopgen: 1 }, cx()), [null, null, null, null], "f_mstepchk: poll stops after a cancel");
  eq(run("f_mretry", { rr_mopgen: 1 }, cx()), [null, null], "f_mretry: no retry after a cancel");
  eq(run("f_64", { rr_mopgen: 1 }, cx()), null, "f_64: no stamp after a cancel");
  // a stale pass (gen bumped by f_hstop / f_mopfail, then a new pass started) is dropped too
  eq(run("f_mstep", { rr_mopgen: 1 }, mkFlow({ rr_mopgen: 2 })), [null, null, null], "stale pass msg dropped by f_mstep");
  eq(run("f_mstepchk", { rr_mopgen: 1 }, mkFlow({ rr_mopgen: 2 })), [null, null, null, null], "stale pass poll dropped");
}

console.log("H5 -- mop steps: order, params, wiring");
{
  const EXPECT = [["Front door entry", [11]], ["Hallway", [10]], ["Living room", [12]], ["Kitchen", [13]],
    ["Bathroom 1", [5]], ["Master bathroom", [4]], ["Half bath + Garage entrance + Laundry", [7, 6, 8]]];
  const f = mkFlow({ rr_mopgen: 1 });
  for (let i = 0; i < 7; i++) {
    const o = run("f_mstep", { rr_mopgen: 1, rr_mop_idx: i }, f);
    ok(o[0] && !o[2] && o[0].rr_step.name === EXPECT[i][0] && JSON.stringify(o[0].rr_step.segments) === JSON.stringify(EXPECT[i][1]),
       `step ${i + 1}: ${EXPECT[i][0]} ${JSON.stringify(EXPECT[i][1])}`);
    eq(o[1], { rr_evt: "mopstep", rr_phase: `Mop pass ${i + 1}/7 — ${EXPECT[i][0]}` }, `step ${i + 1} phase`);
  }
  const done = run("f_mstep", { rr_mopgen: 1, rr_mop_idx: 7 }, f);
  ok(!done[0] && !done[1] && done[2], "after 7/7 -> out2 (f_64) only");
  eq(byId["f_mstep"].wires, [["s_mc1"], ["lo_tr_mop"], ["f_64"]], "f_mstep -> modes chain / tracker / f_64");
  const data = (id) => JSON.parse(byId[id].data);
  eq([byId["s_mc1"], byId["s_mc2"], byId["s_mc3"]].map((n) => [n.action, data(n.id).command, data(n.id).params]),
     [["vacuum.send_command", "set_custom_mode", [102]], ["vacuum.send_command", "set_water_box_custom_mode", [201]],
      ["vacuum.send_command", "set_mop_mode", [300]]], "per-step modes 102 / 201 / 300 (vacuum + mop)");
  eq([byId["s_mc1"].wires, byId["s_mc2"].wires, byId["s_mc3"].wires], [[["s_mc2"]], [["s_mc3"]], [["s_mseg"]]], "modes in order, then the segment clean");
  const seg = byId["s_mseg"];
  ok(seg.action === "vacuum.send_command" && seg.dataType === "jsonata"
     && /"command": "app_segment_clean"/.test(seg.data) && /"segments": \$\$\.rr_step\.segments/.test(seg.data) && /"repeat": 1/.test(seg.data),
     "s_mseg: app_segment_clean with the step's segments, repeat 1");
  ok(!flows.some((n) => n.data && /set_mop_template_id/.test(n.data)), "set_mop_template_id is never sent (unsupported)");
  eq(seg.wires, [["d_mstep"]], "segment clean -> poll");
  ok(byId["d_mstep"].timeout === "30" && byId["d_mstep"].wires[0][0] === "f_mstepchk", "poll every 30 s");
  eq(byId["f_mstepchk"].wires, [["f_mstep"], ["d_mstep"], ["f_mopfail"], ["f_mretry"]], "f_mstepchk outputs");
  eq(byId["cat_mstep"].scope, ["s_mc1", "s_mc2", "s_mc3", "s_mseg"], "step commands caught by cat_mstep");
  eq(byId["cat_mstep"].wires, [["f_mretry"]], "cat_mstep -> f_mretry");
  eq(byId["f_mretry"].wires, [["d_mretry"], ["f_mopfail"]], "f_mretry -> 60 s wait / mop failed");
  ok(byId["d_mretry"].timeout === "60" && byId["d_mretry"].wires[0][0] === "f_mstep", "retry waits 60 s, then the same step again");
  for (const id of ["s_mc1", "s_mc2", "s_mc3", "s_mseg", "s_63"]) ok(!byId["cat_mid"].scope.includes(id), `${id} not in cat_mid (a step error retries, never cancels)`);
  eq(byId["f_59"].wires, [["f_mstep"], ["f_mopfail"]], "f_59 -> step 1 / tank empty");
  // nothing presses the Roborock server's Mopping routine any more
  ok(!flows.some((n) => n.type === "api-call-service" && /mr_slave_mopping/.test(n.data || "")), "no node presses button.mr_slave_mopping");
  ok(!flows.some((n) => n.type === "api-call-service" && n.action === "select.select_option" && /"option": "mop"/.test(n.data || "")),
     "no 'mode: mop' select any more (the step modes replace it)");
  const sweep = byId["g_sweep"].nodes;
  ok(["f_mstep", "s_mc1", "s_mc2", "s_mc3", "s_mseg", "d_mstep", "f_mstepchk", "cat_mstep", "f_mretry", "d_mretry"].every((id) => sweep.includes(id)),
     "step nodes are in group 3");

  // f_mretry: transient "Connection lost" -> retry (up to 4), then mop failed
  const fr = mkFlow({ rr_mopgen: 1 });
  let r = run("f_mretry", { rr_mopgen: 1, rr_mop_idx: 2, rr_step: { n: 3, of: 7, name: "Living room" }, error: { message: "Connection lost" } }, fr);
  ok(r[0] && !r[1] && r[0].rr_step_retry === 1 && r[0].rr_mop_idx === 2 && !r[0].error, "Connection lost -> retry 1, same step");
  r = run("f_mretry", { rr_mopgen: 1, rr_step_retry: 4, rr_step: { n: 3, of: 7, name: "Living room" }, error: { message: "Connection lost" } }, fr);
  ok(!r[0] && r[1] && /3\/7 \(Living room\).*4 retries.*Connection lost/.test(r[1].rr_mop_reason), "5th failure -> mop failed with the reason");
}

console.log("H6 -- mop step end detection (f_mstepchk)");
{
  const T0 = new Date(2026, 10, 18, 18, 0, 0).getTime();
  const M = 60e3;
  const iso = (ms) => new Date(ms).toISOString();
  const G = (status, vac, cleaning, changedAt, extra) => mkGlobal(Object.assign(state({
    "sensor.mr_slave_status_2": status, "vacuum.mr_slave_2": vac,
    "binary_sensor.mr_slave_cleaning_2": { state: cleaning, last_changed: iso(changedAt || T0 - 30 * M) },
    "binary_sensor.mr_slave_water_shortage_2": "off" }), extra || {}));
  const fl = mkFlow({ rr_mopgen: 1 });
  const base = () => ({ rr_mopgen: 1, rr_mop_idx: 2, rr_step: { n: 3, of: 7, name: "Living room" }, rr_step_t0: T0 });
  const chk = (m, g, t) => withDate(t, () => run("f_mstepchk", m, fl, g));
  const only = (r, i) => r.every((x, j) => (j === i ? !!x : x === null));
  // not started yet (robot still on the dock) -> poll; after 5 min -> retry
  let m = base();
  let r = chk(m, G("charging", "docked", "off"), T0 + 30e3);
  ok(only(r, 1) && !r[1].rr_step_started, "still docked 30 s after the send -> poll");
  r = chk(base(), G("charging", "docked", "off"), T0 + 5 * M);
  ok(only(r, 3) && /did not start/.test(r[3].rr_mop_reason), "not started after 5 min -> retry the step");
  // started: cleaning
  r = chk(base(), G("segment_cleaning", "cleaning", "off"), T0 + 30e3);
  ok(only(r, 1) && r[1].rr_step_started, "vacuum cleaning -> started, poll");
  m = r[1];
  // mid-job mop wash: cleaning_2 stays on -> not finished (11-18 17:40, 18:55)
  r = chk(m, G("washing_the_mop", "docked", "on", T0 + 40e3), T0 + 12 * M); ok(only(r, 1), "mid-job mop wash -> not finished");
  // recharge pause: charging with cleaning_2 on -> not finished
  r = chk(m, G("charging", "docked", "on", T0 + 40e3), T0 + 20 * M); ok(only(r, 1), "recharge pause (charging, job open) -> not finished");
  r = chk(r[1], G("charging", "docked", "on", T0 + 40e3), T0 + 50 * M); ok(only(r, 1) && !r[1].rr_step_home, "still recharging -> not finished");
  // job closed, dock empties the bin / washes the mop after the job -> not finished yet
  const off = T0 + 60 * M;
  r = chk(r[1], G("returning_home", "returning", "off", off), off + 20e3); ok(only(r, 1), "job closed, returning -> not finished");
  r = chk(r[1], G("washing_the_mop", "docked", "off", off), off + 3 * M); ok(only(r, 1) && !r[1].rr_step_home, "post-job wash -> not finished");
  r = chk(r[1], G("emptying_the_bin", "docked", "off", off), off + 4 * M); ok(only(r, 1), "emptying the bin -> not finished");
  // charging and quiet: 90 s settle
  r = chk(r[1], G("charging", "docked", "off", off), off + 5 * M); ok(only(r, 1) && r[1].rr_step_home === off + 5 * M, "charging, settle starts");
  const s1 = Object.assign({}, r[1]);
  r = chk(Object.assign({}, s1), G("washing_the_mop", "docked", "off", off), off + 5.5 * M); ok(only(r, 1) && r[1].rr_step_home === 0, "wash starts after charging -> settle resets");
  r = chk(Object.assign({}, s1), G("charging", "docked", "off", off), off + 6 * M); ok(only(r, 1), "charging 60 s -> not yet");
  r = chk(r[1], G("charging", "docked", "off", off), off + 6.5 * M);
  ok(only(r, 0) && r[0].rr_mop_idx === 3 && r[0].rr_step_retry === 0, "charging 90 s with the job closed -> step finished, next step");
  // idle beside the dock after the job (dock failed): not finished, not failed
  r = chk(Object.assign(base(), { rr_step_started: T0 }), G("idle", "idle", "off", off), off + 10 * M); ok(only(r, 1), "idle off the dock -> keep polling (group 8 retries)");
  // a very short job that opened and closed between two polls still counts as started
  r = chk(base(), G("charging", "docked", "off", T0 + 20e3), T0 + 60e3); ok(only(r, 1) && r[1].rr_step_started, "cleaning_2 changed since the send -> started");
  // water ran out -> failed, never finished
  const fw = mkFlow({ rr_mopgen: 1 });
  r = withDate(off + 7 * M, () => run("f_mstepchk", Object.assign(base(), { rr_step_started: T0 }), fw,
    G("charging", "docked", "off", off, state({ "binary_sensor.mr_slave_water_shortage_2": "on" }))));
  ok(only(r, 2) && /ran out during mop step 3\/7 \(Living room\)/.test(r[2].rr_mop_reason) && fw.get("rr_mop_water") === true, "water shortage -> mop failed");
  // step cap
  r = chk(Object.assign(base(), { rr_step_started: T0 }), G("segment_cleaning", "cleaning", "on"), T0 + 241 * M);
  ok(only(r, 2) && /within 240 min/.test(r[2].rr_mop_reason), "240 min without finishing -> mop failed");
}

console.log("H7 -- rr_last_mop_run only after 7/7");
{
  // the only writers of rr_last_mop_run: s_65, fed only by f_64, fed only by f_mstep out2
  const writers = flows.filter((n) => n.type === "api-call-service" && /rr_last_mop_run/.test(n.data || ""));
  eq(writers.map((n) => n.id), ["s_65"], "s_65 is the only node that sets rr_last_mop_run");
  const feeders = (id) => flows.filter((n) => (n.wires || []).some((w) => w.includes(id))).map((n) => n.id);
  eq(feeders("s_65"), ["f_64"], "only f_64 feeds s_65");
  eq(feeders("f_64"), ["f_mstep"], "only f_mstep feeds f_64");
  ok(byId["f_mstep"].wires[2][0] === "f_64" && byId["f_mstep"].wires[0][0] !== "f_64", "f_64 is f_mstep's 'all 7 finished' output");
  eq(byId["f_64"].wires, [["s_65", "s_66", "s_63"]], "f_64 -> stamp, restore + done, last wash (leaves)");
  eq(byId["s_65"].wires, [[]], "stamp is a leaf (not a gate for done)");

  // full simulated pass: 7 steps, each with a mid-job wash, then the job closes and the robot settles
  const fl = mkFlow({ rr_cancel: false, rr_mop_water: false });
  let t = new Date(2026, 10, 18, 8, 31, 0).getTime();
  const M = 60e3;
  let cleanOff = t - 60 * M;
  const G = (status, vac, cleaning) => mkGlobal(state({ "sensor.mr_slave_status_2": status, "vacuum.mr_slave_2": vac,
    "binary_sensor.mr_slave_cleaning_2": { state: cleaning, last_changed: new Date(cleanOff).toISOString() },
    "binary_sensor.mr_slave_water_shortage_2": "off" }));
  let msg = withDate(t, () => run("f_59", {}, fl, G("charging", "docked", "off")))[0];
  const phases = [];
  let stamps = 0, sends = 0;
  for (let guard = 0; guard < 200 && msg; guard++) {
    const o = withDate(t, () => run("f_mstep", msg, fl));
    if (o[2]) { const s = withDate(t, () => run("f_64", o[2], fl)); if (s && s.payload.timestamp) stamps++; break; }
    phases.push(o[1].rr_phase); sends++;
    let m = o[0];
    // the robot runs the job: cleaning -> mid-job wash -> cleaning -> job closes -> post-job wash -> charging
    const seq = [["segment_cleaning", "cleaning", "on", 1], ["washing_the_mop", "docked", "on", 6], ["segment_cleaning", "cleaning", "on", 10],
      ["returning_home", "returning", "off", 15], ["washing_the_mop", "docked", "off", 17], ["charging", "docked", "off", 21],
      ["charging", "docked", "off", 21.5], ["charging", "docked", "off", 22], ["charging", "docked", "off", 22.6]];
    const s0 = t;
    let next = null;
    for (const [st, vac, cl, min] of seq) {
      t = s0 + min * M;
      if (cl === "off" && cleanOff < s0) cleanOff = t;
      const r = withDate(t, () => run("f_mstepchk", m, fl, G(st, vac, cl)));
      if (r[0]) { next = r[0]; break; }
      ok(!r[2] && !r[3], `pass: no failure/retry at ${st}`);
      m = r[1];
    }
    msg = next;
    ok(stamps === 0, "pass: no stamp before 7/7");
  }
  eq(sends, 7, "simulated pass: 7 steps sent");
  eq(phases.map((p) => p.replace(/^Mop pass (\d)\/7 — .*/, "$1")), ["1", "2", "3", "4", "5", "6", "7"], "simulated pass: phases 1/7 .. 7/7 in order");
  eq(stamps, 1, "simulated pass: rr_last_mop_run stamped once, after 7/7");
  ok(fl.get("rr_mop_pending") === false && fl.get("rr_mop_inpass") === 0, "pass done closes the mop flags");
  // a pass cancelled at step 4 never stamps
  const fc = mkFlow({});
  const m1 = run("f_59", {}, fc, mkGlobal(state({ "binary_sensor.mr_slave_water_shortage_2": "off" })))[0];
  m1.rr_mop_idx = 3;
  run("f_hstop", { rr_src: "hand" }, fc, mkGlobal(state({ "sensor.mr_slave_status_2": "segment_cleaning", "vacuum.mr_slave_2": "cleaning" })));
  eq(run("f_mstep", m1, fc), [null, null, null], "cancelled at step 4 -> no further step, no stamp");
  eq(run("f_mstep", Object.assign({}, m1, { rr_mop_idx: 7 }), fc), [null, null, null], "even a 7/7 msg after a cancel does not reach f_64");
}

console.log("H8 -- Mop all floors button (any day, same steps, no room stamps)");
{
  eq(byId["t_68"].entities.entity, ["input_button.rr_mop_all_floors"], "t_68 = Mop all floors button");
  eq(byId["t_68"].wires, [["f_mopman"], []], "t_68 -> f_mopman");
  eq(byId["f_mopman"].wires, [["lo_mop_man"], ["lo_tr_man"]], "f_mopman -> mop pass link / tracker link");
  ok(byId["li_mop"].links.includes("lo_mop_man") && byId["lo_mop_man"].links[0] === "li_mop", "lo_mop_man <-> li_mop");
  ok(byId["li_tr"].links.includes("lo_tr_man") && byId["lo_tr_man"].links[0] === "li_tr", "lo_tr_man <-> li_tr");
  const reach = (start) => {
    const seen = new Set(), todo = [...start];
    while (todo.length) {
      const id = todo.pop();
      if (seen.has(id) || !byId[id]) continue;
      seen.add(id);
      for (const out of byId[id].wires || []) todo.push(...out);
      if (byId[id].type === "link out") todo.push(...(byId[id].links || []));
    }
    return seen;
  };
  const p = reach(["t_68"]);
  ok(["f_59", "f_mstep", "s_mc1", "s_mseg", "f_mstepchk", "f_64"].every((id) => p.has(id)), "button reaches the 7 Node-RED mop steps");
  ok(!p.has("f_58") && !p.has("w_mopbat"), "button skips the Wednesday gate and the battery wait");
  ok(!["s_37", "s_43", "s_49", OFFICE.clean].some((id) => p.has(id)), "button reaches no vacuum stage (room stamps only come from those stages' tracker events)");
  // any day
  const f = mkFlow({ rr_cancel: true, rr_mop_skip: true });
  const o = withDate(TUE, () => run("f_mopman", {}, f));
  ok(o[0] && o[0].rr_mop_manual && o[1].rr_evt === "start" && o[1].rr_mop_manual, "Tuesday press -> starts (no day check)");
  ok(f.get("rr_cancel") === false && f.get("rr_mop_manual") === true && f.get("rr_mop_skip") === false, "clears the old cancel, flags a manual mop");
  const ft = mkFlow({});
  const tr = run("f_track", o[1], ft);
  ok(ft.get("rr_run").active === true && /^Mop all floors/.test(ft.get("rr_run").phase) && tr[1].length === 0, "tracker: manual mop phase, no stamps");
  const r2 = withDate(TUE, () => run("f_59", o[0], f, mkGlobal(state({ "binary_sensor.mr_slave_water_shortage_2": "off" }))));
  ok(r2[0] && r2[0].rr_mop_idx === 0, "f_59 starts step 1 for the button");
  // ignored while a run is live
  eq(run("f_mopman", {}, mkFlow(LIVE())), [null, null], "ignored during a live daily run");
  // hand stop during a manual mop cancels it like a daily run (classifier sees the live run)
  ok(fires(withDate(Date.now(), () => run("f_ev_idle", chg("cleaning", "paused", PERSON), mkFlow({ rr_run: { active: true, phase: "Mop pass 2/7 — Hallway" }, rr_mop_manual: true, rr_nr_uid: NR })))),
     "hand stop during a manual mop -> cancels it");
  // manual failure wording
  const mf = run("f_mopfail", { rr_mop_reason: "x" }, mkFlow({ rr_mop_manual: true }));
  ok(/Tap Mop all floors to try again/.test(mf[0].notify.message) && !/Wednesday/.test(mf[0].notify.message), "manual mop failure wording");
}

console.log("H9 -- own HA user is learned, not hard-coded; no catch-up promises left");
{
  ok(byId["a_uid"].type === "ha-api" && byId["a_uid"].protocol === "websocket" && JSON.parse(byId["a_uid"].data).type === "auth/current_user",
     "a_uid asks HA who the Node-RED user is (websocket auth/current_user)");
  ok(byId["i_uid"].once === true && byId["i_uid"].repeat === "3600" && byId["i_uid"].wires[0][0] === "a_uid", "at startup and hourly");
  eq(byId["a_uid"].wires, [["f_uid"]], "a_uid -> f_uid");
  const fu = mkFlow({});
  run("f_uid", { payload: { id: NR, name: "node-red" } }, fu);
  ok(fu.get("rr_nr_uid") === NR, "f_uid stores rr_nr_uid");
  ok(!flows.some((n) => JSON.stringify(n).includes(NR)), "Node-RED's user id is not hard-coded anywhere in flows.json");
  // user-facing wording: no catch-up promises
  const msgs = flows.filter((n) => n.func).map((n) => [n.id, (n.func.match(/(['"])(?:(?!\1)[^\\]|\\.)*\1/g) || []).join(" ")]);
  const promises = msgs.filter(([, s]) => /catch-up mop runs|Thursday|Friday/.test(s)).map(([id]) => id);
  eq(promises, [], "no user-facing message promises a Thu/Fri catch-up");
  ok(/next Wednesday/.test(byId["f_stall"].func) && /next Wednesday/.test(byId["f_fail"].func), "stall / fail mop note says next Wednesday");
  const s = run("f_stall", { rr_stage: "Main floor sweep", rr_mins: 150 }, mkFlow({ rr_mop_pending: true }), mkGlobal(state({ "sensor.mr_slave_status_2": "charging" })));
  ok(/there is no catch-up, the next mop is next Wednesday/.test(s[0].notify.message), "f_stall on a mop day -> next Wednesday");
  // flows.json stays secret-free
  ok(!/Bearer\s+[A-Za-z0-9._-]{20,}|eyJ[A-Za-z0-9_-]{20,}\./.test(fs.readFileSync(FLOWS, "utf8")), "no bearer token / JWT in flows.json");
  eq([...new Set(flows.filter((n) => n.z).map((n) => n.z))], ["rr_tab_1"], "single tab");
}

// ===========================================================================
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

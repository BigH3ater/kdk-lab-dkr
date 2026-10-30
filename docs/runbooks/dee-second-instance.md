---
title: Add a second DEE instance (kdk-dee-02)
---

# Add a second DEE instance (kdk-dee-02)

The lossless-audio transcode backlog is bottlenecked on the **single-slot DeeZy
wrapper** on [[kdk-dee-01]] (DTS-HD MA / TrueHD → Dolby DD+/Atmos). Measured DEE
encode times (job reports, 2026-10-28/29): TrueHD 5.1 → DD+ **~31 min**
(Godfather III), TrueHD Atmos 7.1 → Atmos **~1 h 48 m** (Pacific Rim Uprising).
The queue is ~all Atmos remuxes, so one slot clears ~24/day and the ~198-file
backlog finishes ~2026-11-07. A **second DEE slot** on a cloned VM roughly halves
the DEE-bound time (ETA ~2026-11-03). GPU is idle (0 %); this is audio-only.

> This is a **one-time backlog** fix. Steady-state daily intake is a handful of
> lossless titles/day — one slot is plenty. Decide whether a permanent second
> licensed DEE VM is worth it, or clone it just to drain the backlog and shut it
> down after.

> **Status (2026-10-30):** kdk-dee-02 (10.1.1.131) is cloned and online. The
> Dolby licence go/no-go **PASSED** — one real DD+ encode ran on kdk-dee-02 via
> `POST /encode` (HTTP 200, ~21 s, valid E-AC-3 5.1 output), so the clone's
> licence works on the new hardware. `stacks/dee-lb` fronts both slots at
> `10.1.20.20:8484` and the Tdarr flow points at the raw LB IP. Registration,
> DNS, flow and monitoring land in PR `feat/dee-lb-rollout` (attended deploy).

## Facts (read-only, 2026-10-30)

| Field | kdk-dee-01 (source) | Source |
|---|---|---|
| VMID | **220** on kdk-hyp-01 | `qm config 220` |
| vCPU / RAM | **4 cores / 8192 MB**, `cpu host`, 1 socket | `qm config 220` |
| Disk | `local-zfs:vm-220-disk-0` **200 G** scsi0 (+ efidisk 1M, tpmstate 4M) | `qm config 220` |
| Firmware | BIOS **OVMF/UEFI**, q35, win11, Secure Boot keys pre-enrolled | `qm config 220` |
| NIC | `net0 virtio=BC:24:11:D8:47:C9, bridge=vmbr0, tag=10` (VLAN 10) | `qm config 220` |
| IP / host | **10.1.1.130**, Windows 11, hostname `kdk-dee-01` | docs/hosts |
| Wrapper | `C:\dee-wrapper\main.py` (FastAPI), launched by `run.cmd`; **scheduled task `dee-wrapper`** (BootTrigger, principal `kdkadmin`, Highest) | runbook windows-vm-dee |
| Port / health | **8484**, `GET /healthz`, encode `POST /encode` | verify.sh, flow |
| DEE binary | `DEEZY_PATH=C:\Dolby Converter\deezy.exe` (wraps the licensed Dolby Encoding Engine) | windows-vm-dee |
| Exchange | `\\10.1.3.5\dee-exchange` (TrueNAS SMB, user `tdarr-smb`) | windows-vm-dee |
| Flow addresses DEE as | **raw IP** `{ host: '10.1.1.130', port: 8484 }`, `POST /encode` — one endpoint, hardcoded in step `dee1` ("3b. DEE dispatch") | `stacks/media/tdarr-flows/hevc-pipeline.json` |
| Next free VMID | 100 (`cluster/nextid`); **recommend 221** to keep the 2xx infra group | `pvesh get /cluster/nextid` |
| Hyp headroom | 24 cores / 188 GiB, ~107 GiB free | `nproc` / `free -g` on kdk-hyp-01 |

**Each DEE encode uses ~1–2 of the VM's 4 cores** (DEE is lightly threaded; the VM
has only 4 cores and runs one encode at a time). A clone with the same 4c/8G is
correctly sized for **one** slot — do not try to run two slots inside one VM
(see Option A below).

## GO / NO-GO: the Dolby licence (do this FIRST, do not skip)

DeeZy drives the **Dolby Encoding Engine**, which is **licensed**. A full clone
copies `C:\Dolby Converter\` (binary + any licence file), but activation is often
**machine-bound** (hostname / MAC / hardware UUID). `qm clone` assigns a **new
MAC** and a **new SMBIOS UUID**, which can invalidate a hardware-locked licence.

**Go/no-go test (on kdk-dee-02 after first boot):** run **one** real encode
through the wrapper (`POST /encode`, or deezy's own version/licence check) and
confirm it produces output.
- Encode succeeds → **GO**, continue.
- Encode fails with a licence / activation / "not licensed" error → **NO-GO**:
  a clone does not carry the licence to new hardware. You need a separately
  licensed DEE install on the second VM, or abandon the second slot.

Do **not** open or copy the licence file to inspect its terms — test by
encoding. **Check the DEE licence for whether concurrent instances are
permitted** before running two slots in production.

## 1. Clone the VM (owner, on kdk-hyp-01)

```sh
# as kdkadmin on 10.1.1.100
sudo qm clone 220 221 --name kdk-dee-02 --full
# confirm the clone got a NEW, distinct MAC (qm clone generates one):
sudo qm config 221 | grep -E 'net0|name'
# expect: net0: virtio=BC:24:11:XX:XX:XX,bridge=vmbr0,tag=10   (XX != D8:47:C9)
sudo qm set 221 --startup order=4          # boot after dee-01
sudo qm start 221
```

The clone inherits 4c/8G/200G, VLAN 10, UEFI+Secure Boot, and the whole Windows
install including the wrapper and its BootTrigger task.

## 2. Network: reserve the next IP (owner, UniFi)

1. UniFi → Settings → Networks → the VLAN 10 network → **DHCP reservation** (or
   static) for the clone's **new MAC** to the next free address, e.g.
   **10.1.1.131** (verify it is unused first: `ping`/`arp` is firewalled, so
   check UniFi's client list / DHCP leases).
2. Note the chosen IP for steps 3–5 below. This runbook assumes **10.1.1.131**.

## 3. First boot inside Windows (owner, `ssh kdkadmin@10.1.1.131` → PowerShell)

Staging a script is more reliable than inline quoting (see
[windows-vm-dee](windows-vm-dee.md)).

1. **Rename** the host and reboot once:
   ```powershell
   Rename-Computer -NewName kdk-dee-02 -Restart
   ```
2. **Verify IP**: `Get-NetIPAddress -AddressFamily IPv4` shows 10.1.1.131.
3. **Firewall**: the wrapper port must be open on the new box (the clone carries
   dee-01's rule, confirm it exists):
   ```powershell
   Get-NetFirewallRule -DisplayName '*dee*','*8484*' | Format-Table DisplayName,Enabled,Direction
   # if missing:
   New-NetFirewallRule -DisplayName 'dee-wrapper 8484' -Direction Inbound -Protocol TCP -LocalPort 8484 -Action Allow
   ```
4. **Exchange mount + wrapper**: the BootTrigger task should start the wrapper on
   boot. If not, launch it session-independently:
   ```powershell
   Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
     -Arguments @{ CommandLine='cmd /c C:\dee-wrapper\run.cmd'; CurrentDirectory='C:\dee-wrapper' }
   ```
   Note: `DEE_WRAPPER_TOKEN` is a **machine env var** sourced from
   `op://kdk-cluster/kdk-dee-01-api-token`. The clone copies it, so both VMs
   share the same token — the Tdarr flow sends one token, so this is what we
   want. (If you rotate it, rotate on both.)
5. **GO/NO-GO licence test** (section above). Stop here if NO-GO.

## 4. DNS (owner / next gitops-sync)

Add the new host and the load-balancer name to the `EXTRA` dict in
`stacks/dns-reconcile/reconcile.py`, then let `dns-reconcile` push them to
AdGuard (or run the stack):

```python
    "kdk-dee-02.kmkdp.com": "10.1.1.131",
    "dee-lb.kmkdp.com":     "10.1.20.20",   # HAProxy on kdk-dkr-01 (step 6)
```

(`kdk-*` names are never pruned; `dee-lb` answers to kdk-dkr-01's Traefik host IP,
but the LB listens on its own port — see step 6.)

## 5. Smoke test (from kdk-dkr-01, read-only)

```sh
curl -s http://10.1.1.131:8484/healthz        # expect {"status":"ok","deezy_present":true,...}
curl -s http://kdk-dee-02.kmkdp.com:8484/healthz
```

Then queue one lossless title and confirm it encodes on the new box (watch the
wrapper log on kdk-dee-02, or the Tdarr job report shows `[dee] done`).

## 6. Make Tdarr use both slots — HAProxy in front (no per-host flow logic)

The flow calls **one** endpoint (`10.1.1.130:8484/encode`). The DeeZy wrapper has
**no multi-backend support** — it is a single FastAPI process per host. So the
two slots are fronted by a tiny load balancer and the flow keeps calling **one
name**. Deploy `stacks/dee-lb` (HAProxy on kdk-dkr-01):

- `balance leastconn`, **`maxconn 1` per backend** (each wrapper is single-slot;
  never send it a second job), health check `GET /healthz`.
- Long `timeout server/client` (encodes run up to ~2 h) and a `timeout queue` so
  that when both backends are busy the request **waits** for a slot instead of
  failing — this matches the flow's current "wait up to 1 h for the DEE slot"
  behaviour (compose comment, 2026-10-29).

Then point the flow at the LB with a **one-line, git-tracked** change (this is
NOT risky SQLite surgery — the flow is version-controlled and pushed by the
sanctioned sync script):

```
# stacks/media/tdarr-flows/hevc-pipeline.json, step dee1:
-  const WRAPPER = { host: '10.1.1.130', port: 8484 };
+  const WRAPPER = { host: '10.1.20.20', port: 8484 };   // dee-lb IP, not the name
scripts/tdarr-flow-sync.sh import     # pushes via cruddb + restarts tdarr
```

Use the **raw LB IP `10.1.20.20`** (HAProxy's bind on kdk-dkr-01), **not**
`dee-lb.kmkdp.com`: the flow addressed DEE by IP before, and a raw IP means a
DNS outage or the `*.kmkdp.com` search-domain wildcard can never silently break
encoding. The `dee-lb.kmkdp.com` name is still published (step 4) for humans and
curl smoke tests.

Because the flow uses a **raw IP** today, this one-line edit is strictly simpler
and lower-risk than re-IPing the two DEE VMs to put the LB on 10.1.1.130. The two
GPU workers already run (`transcodegpu:2`); with two DEE backends both proceed in
parallel instead of one blocking — **no Tdarr worker-limit change needed**.

See `stacks/dee-lb/compose.yaml` + `stacks/dee-lb/config/haproxy.cfg`.

## 7. Monitoring

- **tdarr-verify** already probes DEE `/healthz` hourly, but only
  `10.1.1.130`. Extend `stacks/tdarr-verify/verify.sh` to probe **both**
  (dee-01 and dee-02) so a dead second slot pages too. (Follow-up PR.)
- **Blackbox**: add both wrapper health URLs to a probe job in
  `stacks/monitoring/prometheus/prometheus.yml` (mirror the `blackbox-http-app`
  block; DEE is not probed by Prometheus today):
  ```yaml
          - "http://10.1.1.130:8484/healthz"   # kdk-dee-01
          - "http://10.1.1.131:8484/healthz"   # kdk-dee-02
  ```
- **HAProxy** exposes its own stats; optionally scrape `dee-lb` for per-backend
  up/down. A backend marked down in HAProxy = that DEE slot is gone.
- **Homepage**: DEE has no tile today (it is infra, not a UI). Optional: add a
  `siteMonitor` ping tile for each wrapper health URL.

## Teardown (if cloned only to drain the backlog)

Once `/etc/kdk/tdarr-backlog.list` is empty: revert the flow line to
`10.1.1.130`, `scripts/tdarr-flow-sync.sh import`, remove the `dee-lb` stack and
DNS entries, then `sudo qm stop 221 && sudo qm destroy 221` on kdk-hyp-01. One
slot handles steady-state intake.

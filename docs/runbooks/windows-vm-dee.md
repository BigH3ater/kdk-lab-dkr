---
title: Troubleshoot the Windows DEE VM
---

# Troubleshoot the Windows DEE VM

`kdk-dee-01` (`10.1.1.130`, Windows 11) runs the **DeeZy wrapper** — a FastAPI
service on `:8484` that encodes TrueHD/DTS-HD MA lossless audio to Dolby DD+
(`.ec3`) for Tdarr. It is the dependency behind every lossless-audio transcode;
if it's down, those Tdarr jobs error. See
[Tdarr troubleshooting](tdarr-troubleshooting.md) for the downstream symptom.

## How it's wired

- **Service:** `C:\dee-wrapper\.venv\Scripts\python.exe C:\dee-wrapper\main.py`,
  launched by `C:\dee-wrapper\run.cmd` (net use to the exchange, then python).
- **Scheduled task:** `dee-wrapper`, principal `kdkadmin` (Password logon,
  Highest), **BootTrigger** — so a reboot brings it back on its own.
- **Exchange:** `\\10.1.3.5\dee-exchange` (TrueNAS SMB, user `tdarr-smb`),
  mounted by `run.cmd`. DeeZy reads `inbound/`, writes `outbound/`.
- **Config (machine env):** `DEE_WRAPPER_TOKEN=op://kdk-cluster/kdk-dee-01-api-token`,
  `DEE_EXCHANGE_ROOT=\\10.1.3.5\dee-exchange`,
  `DEEZY_PATH=C:\Dolby Converter\deezy.exe`, `DEE_WRAPPER_MIN_FREE_GB`,
  `DEE_WRAPPER_WORK_DIR`.
- **Admin login:** `op://kdk-ops/kdk-dee-01-admin` (username + password).

## First: read `/healthz`

From anywhere on the LAN, or the tdarr host:

```sh
curl -s http://10.1.1.130:8484/healthz
```

```json
{"status":"ok","deezy_present":true,"exchange_reachable":true,
 "exchange_free_gb":72484.5,"work_free_gb":74.9,"min_free_gb":20}
```

- `status: ok` — healthy.
- `status: degraded` + `exchange_reachable: false` — the SMB mount dropped
  (encodes will 503; the wrapper does **not** hang, by design).
- `status: degraded` + a low `*_free_gb` — a drive is under `min_free_gb`;
  encodes are refused (507) rather than filling the disk.
- **No response / connection hangs** — the wrapper process is down or wedged;
  work through the process section below.

## SSH to the box

`ssh kdkadmin@10.1.1.130` — the shell is **PowerShell**, and inline quoting
through SSH is fragile. Stage a script instead of fighting quotes:

```sh
ssh kdkadmin@10.1.1.130 'powershell -NoProfile -Command "Set-Content -Path C:\dee-wrapper\_d.ps1 -Value ([Console]::In.ReadToEnd())"' <<'PS1'
Write-Output ("python: " + ((Get-Process python -EA SilentlyContinue).Id -join ","))
Write-Output ("8484: " + ((Get-NetTCPConnection -LocalPort 8484 -State Listen -EA SilentlyContinue|Measure-Object).Count))
Write-Output ("task: " + (Get-ScheduledTask -TaskName dee-wrapper).State)
net use | Out-String
PS1
ssh kdkadmin@10.1.1.130 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\dee-wrapper\_d.ps1'
```

`scp` is more reliable than piping for deploying files (e.g. a new `main.py`).
ICMP is firewalled off; test reachability with a TCP port check, not ping.

## Failure modes and fixes

### Exchange mount dropped (`exchange_reachable: false`)

The TrueNAS side is usually healthy — confirm with `Test-NetConnection 10.1.3.5
-Port 445` (should succeed) before suspecting the NAS. The mount drops on the
**client** side. Re-establish it and the wrapper self-heals on its next probe.

If `net use` itself hangs, the SMB **redirector is wedged** — reset it without a
reboot:

```powershell
Restart-Service LanmanWorkstation -Force
```

Then re-run the mount / restart the wrapper (below).

### Wrapper hung (healthz never responds)

A dead mount used to block the wrapper in an unkillable kernel SMB wait. The
current `main.py` bounds every exchange probe (4s), so this shouldn't recur — if
it does, the python is stuck in kernel I/O and `Stop-Process` won't touch it:

```powershell
Get-Process python | ForEach-Object { taskkill /PID $_.Id /T /F }
```

Clearing the dead SMB session (`Restart-Service LanmanWorkstation -Force`) lets
the stuck call return so the process can die.

### Wrapper won't start from the scheduled task

`Start-ScheduledTask dee-wrapper` can hang in the task's non-interactive batch
logon session when the mount/redirector is in a bad state. Recover with a
**session-independent** launch that survives the SSH disconnect and where
`net use` works:

```powershell
Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
  -Arguments @{ CommandLine = 'cmd /c C:\dee-wrapper\run.cmd'; CurrentDirectory = 'C:\dee-wrapper' }
```

(A plain interactive `Start-Process cmd /c run.cmd` also works but dies when the
SSH session closes — OpenSSH kills the child tree.)

### Reboot to clear everything

The BootTrigger task brings the wrapper back healthy on a clean boot — this is
the definitive fix when the redirector/session state is wedged:

```powershell
shutdown.exe /r /t 5 /f
```

Use `shutdown.exe`, not `Restart-Computer -Force` (it has silently no-op'd here).
A forced reboot may apply pending Windows updates, so the box can be **down 10+
minutes** — don't assume failure; recheck `/healthz` after it returns
(confirm the reboot with `(Get-CimInstance Win32_OperatingSystem).LastBootUpTime`).

## Notes

- Some steps are operator-gated for automated agents (a VM reboot, storing SMB
  creds with `cmdkey`, and `Set-ScheduledTask` — which needs the account
  password). If an agent is blocked, it hands these back to run by hand.
- Disk-full on `C:` is usually DeeZy `--keep-temp` WAV intermediates landing in
  `%LOCALAPPDATA%\deezy` when the exchange mount is gone. The wrapper's
  pre-flight now refuses an encode (507) before that can fill the drive.

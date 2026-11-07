---
title: Home Assistant housekeeping
updated: 2026-11-07
---

# Home Assistant housekeeping

Standing rules for keeping the [[home-assistant|Home Assistant]] config on
[[kdk-dkr-02]] (`/opt/kdk-lab/home-assistant/config`) small and honest. Run the
checks before an HA minor upgrade and after any clean-up PR. All checks are
read-only; the fixes are noted next to each.

## Rules

1. **No `*.bak` files in `config/packages` or `config/dashboards`.** Git is the
   backup. HA loads every `*.yaml` under `packages/`, and `pre_deploy` (`cp -f`)
   never deletes, so a stray copy can keep a retired package alive or hide which
   file is live. CI enforces it for the repo side
   (`stacks/home-assistant/tools/tests/test_treadmill_dashboards.py`); the host side
   is the check below. Back up a host-only file (like `configuration.yaml`) with a
   dated copy *next to it* in `config/`, never inside those two dirs, and delete
   it once the change is validated.
2. **Remove unused custom components.** A component with no config entry and no
   YAML is dead code that still loads at start and still needs updates. Remove it
   in HACS (HACS → the repository → ⋮ → **Remove**), then restart HA. The kept
   list is in the [service page](../services/home-assistant.md#configuration).
3. **Prune dead Zigbee devices in Zigbee2MQTT.** In `zigbee.kmkdp.com` → **Devices**,
   a device that is gone for good (sold, broken, re-paired under a new address)
   gets ⋮ → **Remove** (tick *Force remove* if it is offline and will not answer).
   Z2M then drops its MQTT discovery topics and HA removes the device.
4. **No orphans in the entity registry.** After removing an integration, a
   package or a device, check the registry (below) and remove what is left in
   Settings → Devices & services → **Entities** (filter, select, **Remove selected**).

## Checks

**kdk-dkr-02, as kdkadmin**

```bash
C=/opt/kdk-lab/home-assistant/config

# 1. stray backups in the shipped dirs (want no output)
sudo find $C/packages $C/dashboards -type f ! -name '*.yaml'

# 2 + 4. custom components without a config entry, and entity-registry orphans
sudo python3 -I - $C <<'PY'
import json, os, sys
c = sys.argv[1]
load = lambda n: json.load(open(os.path.join(c, ".storage", n)))["data"]
entries = load("core.config_entries")["entries"]
ids = {e["entry_id"] for e in entries}
domains = {e["domain"] for e in entries}
for d in sorted(os.listdir(os.path.join(c, "custom_components"))):
    print(f"{d:20} config_entry={'yes' if d in domains else 'NO'}")
ents = load("core.entity_registry")["entities"]
left = [x["entity_id"] for x in ents if x.get("config_entry_id") and x["config_entry_id"] not in ids]
print("orphans:", len(left), left[:10])                       # want 0
PY
sudo grep -lE '^auth_oidc:' $C/configuration.yaml $C/packages/*.yaml   # the YAML-configured one
```

A custom component with `config_entry=NO` is unused unless it has a YAML block
(today only `auth_oidc`, which is YAML). For a targeted check after a removal,
filter the registry on the platform or prefix instead, the pattern from the
[2026-11-06 workout retirement](workout-tracking-update.md#retirement-of-the-in-house-tracker-2026-11-06):
`[x["entity_id"] for x in ents if x["platform"] == "<domain>"]`.

## Backups before an upgrade

HA's automatic backups run **daily at about 05:15** into `/config/backups`
(`/opt/kdk-lab/home-assistant/config/backups` on the host), **3 kept**. The
"take a backup before a minor upgrade" step is satisfied by checking that
morning's file exists:

```bash
sudo ls -lt /opt/kdk-lab/home-assistant/config/backups | head -4   # newest dated today, ~05:15
```

If it is missing, take one by hand first: Settings → System → Backups →
**Back up now**.

## History

- 2026-11-07: removed the unused `blueair` and `scrypted` custom components (no
  config entry, no YAML; blueair's upstream repo was removed from the HACS
  marketplace) and merged the two identical treadmill dashboards into one
  **Treadmill** dashboard (`/treadmill-workouts`).

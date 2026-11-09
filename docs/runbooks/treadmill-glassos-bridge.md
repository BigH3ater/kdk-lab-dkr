---
title: Treadmill → Home Assistant (retired)
updated: 2026-11-08
---

# Treadmill → Home Assistant (retired)

**Retired 2026-11-08** by owner decision: the treadmill needs no MQTT connection,
only the thud manual control on the console. Removed: the `treadmill-bridge` stack
on kdk-dkr-02 (DIRCON to MQTT, image `kdk/treadmill-bridge`), the broker user
`treadmill`, the Home Assistant package `config/packages/treadmill.yaml` (`tm_*`
helpers, sessions, walker selection, totals, the thud profile webhook), the
**Treadmill** dashboard (`/treadmill-workouts`, `dashboards/workout.yaml`) and its
Kodiak sidebar link, and their CI tests. Walks are logged by hand in [[wger]].

The console HUD and its build/install loop stay:
[Treadmill console HUD (MackTrack)](treadmill-hud.md).

The removal PR body lists the host clean-up (container, deployed HA files,
`configuration.yaml` dashboard block, entity registry, retained MQTT topics,
1Password item `treadmill-mqtt`). The full old page is in the repo history.

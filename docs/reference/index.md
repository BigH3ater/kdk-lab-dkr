---
title: Reference
---

# Reference

Standards and plans that every stack follows.

- [Alerting standards](alerting-standards.md): severity, labels, annotations, `for:` defaults, inhibition, routing (Pushover vs. email), and how to test a rule.
- [Compose hardening baseline](compose-baseline.md): per-service security settings, exception labels, and the `compose-baseline` check and GitHub Action.
- [Docker network IPAM plan](docker-ipam.md): per-host Docker supernets and pinned stack subnets.
- [Docker daemon baseline](docker-daemon.md): the shared `daemon.json` (live-restore, log rotation, no-new-privileges, userland-proxy off) and how to roll it out.
- [Grocery recipe candidates](grocery-recipe-candidates.md): generated review table from `python -m grocery.seed --report` (grocery planner).

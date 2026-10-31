#!/usr/bin/env python3
"""Fail if any [[stack]] in komodo/resources.toml lacks the Compose Config redaction wrapper.

Komodo 2.3.x stores `docker compose config` output in Update logs and in
stack.info.deployed_config, with every .env value resolved (moghtech/komodo#1636).
hosts/_common/sbin/kdk-compose-config-redact masks service environment values; it only
runs for stacks that set both keys below. Stdlib only (tomllib, Python 3.11+).
"""
import sys
import tomllib

WRAPPER = "/usr/local/sbin/kdk-compose-config-redact [[COMPOSE_COMMAND]]"
with open("komodo/resources.toml", "rb") as fh:
    stacks = tomllib.load(fh).get("stack", [])
bad = [s.get("name", "?") for s in stacks
       if s.get("config", {}).get("compose_cmd_wrapper") != WRAPPER
       or s.get("config", {}).get("compose_cmd_wrapper_include") != ["config"]]
for name in bad:
    print(f"::error file=komodo/resources.toml::stack '{name}' lacks the Compose Config redaction "
          f'wrapper (compose_cmd_wrapper = "{WRAPPER}", compose_cmd_wrapper_include = ["config"])')
print(f"{len(stacks)} stacks, {len(bad)} without the redaction wrapper")
sys.exit(1 if bad else 0)

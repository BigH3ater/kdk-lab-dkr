# net-exporters (kdk-dkr-01)

These are the Prometheus exporters behind the Grafana **Network** folder (UniFi Network,
AdGuard Home, SNMP Interface Traffic). Prometheus on kdk-mon-01 scrapes them through the
`unpoller`, `adguard` and `snmp` jobs. If any of them goes unscraped for 10 minutes,
the `NetExporterDown` alert fires.

| Service | Port | Source | Credential (kdk-cluster) |
|---|---|---|---|
| unpoller v5.5.1 | 9130 | UniFi controller on kdk-gw-01 (`https://10.1.3.1`, all sites; rogue APs and DPI saved) | `unifi-api-token-ro/credential`, a read-only API key |
| adguard-exporter v1.2.1 | 9618 | AdGuard Home on dns-01/02/03 (10.1.1.120–122) | `kdk-dns-adguard-home-admin/username,password` |
| snmp-exporter v0.30.1 | 9116 | SNMPv3 IF-MIB walk of kdk-gw-01 (`unifi_v3` auth) | `unifi-snmp-ro-v3-user/password` |

- **Config.** `snmp/snmp.yml` holds the generator output from the k3s-era exporter: the
  `if_mib`, `system_info` and `arp_table` modules. It has no secrets. snmp_exporter runs
  with `--config.expand-environment-variables`, and its auth block reads `SNMP_V3_PASSWORD`
  from `.env`. The `snmp/` directory is mounted rather than the single file, so edits
  reach the container on deploy.
- **Secrets.** Komodo's `pre_deploy` renders `.env` from `.env.tpl`.
- **Checks.** After a deploy, all three should show `up == 1` on
  `http://10.1.20.20:9130/metrics`, `:9618/metrics` and
  `:9116/snmp?target=10.1.3.1&module=if_mib&auth=unifi_v3`.

# Rendered by `op inject` on kdk-mon-01 WHEN this stack is git-linked (needs the
# op CLI + /etc/komodo/op.env on mon-01). Until then the stack runs files_on_host
# on kdk-mon-01 with a hand-rendered .env; this template is the cutover source.

# iDRAC READ-ONLY exporter account (idrac-exporter). Deliberately NOT the
# write-capable fan-control admin. CONFIRM the item name/vault at cutover — the
# SA lane cannot currently see a read-only idrac item; the running .env on
# mon-01 holds the working values.
IDRAC_USERNAME="{{ op://kdk-cluster/kdk-lab-idrac-user/username }}"
IDRAC_PASSWORD="{{ op://kdk-cluster/kdk-lab-idrac-user/password }}"

# Fan-control ADMINISTRATOR account (idracrw) — write-capable, used only by the
# two tigerblue77 controllers.
IDRAC_FANCTL_USERNAME="{{ op://kdk-ops/kdk-lab-idrac-fanctl-admin/username }}"
IDRAC_FANCTL_PASSWORD="{{ op://kdk-ops/kdk-lab-idrac-fanctl-admin/password }}"

# Grafana admin login. CONFIRM the item name/vault at cutover.
GRAFANA_ADMIN_PASSWORD="{{ op://kdk-cluster/grafana-admin/password }}"

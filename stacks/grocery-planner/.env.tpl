# Rendered to .env by the Komodo pre_deploy step with 1Password inject. Every
# referenced item must already exist in 1Password before the first deploy, or the
# inject fails and the stack will not deploy. Never commit .env; never print values.
#
# Items used (vault kdk-cluster):
#   - dashboard-tandoor-rw  fields username, password  (the same dedicated Tandoor
#     account the dashboard mealplan sidecar uses; read/write at the API level,
#     this job writes meal-plan + shopping-list entries only)
#   - vikunja-api-rw  field credential  (the Vikunja token the approval contract
#     reuses; scoped to create/read tasks in the Approvals project)
#
# VIKUNJA_APPROVALS_PROJECT is the numeric id of the Vikunja Approvals project
# (not a secret). Find it per docs/runbooks/approval-tasks.md and set it below.

TANDOOR_USER={{ op://kdk-cluster/dashboard-tandoor-rw/username }}
TANDOOR_PASS={{ op://kdk-cluster/dashboard-tandoor-rw/password }}
VIKUNJA_TOKEN={{ op://kdk-cluster/vikunja-api-rw/credential }}

# Set to the Approvals project id (plain number, edit before first deploy):
VIKUNJA_APPROVALS_PROJECT=

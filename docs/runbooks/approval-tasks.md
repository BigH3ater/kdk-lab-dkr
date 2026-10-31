---
title: Approval tasks (Vikunja ⇄ Home Assistant)
---

# Approval tasks

A generic decision loop: a [[dashboard|Vikunja]] task that needs a yes/no (or
pick-one) decision raises an actionable notification on the owner's phone
([[home-assistant|Home Assistant]] Companion app). Pressing a button records the
decision **on the Vikunja task** — marks it done, adds an `approved`/`skipped`
label, writes an audit comment naming who/how/when, and stamps a
machine-readable `APPROVALS-RESULT: <id>` into the description. The phone
notification is then replaced with a "resolved" message. Resolving the task
directly in Vikunja (web or app) clears the phone the same way.

**Vikunja is the source of truth and the audit record.** Home Assistant holds
no state; it only reconciles the phone against Vikunja once a minute.

The first consumer will be the weekly grocery job (not built here); any task —
including one made by hand in Vikunja — works the same way.

## How it works

- **Polling, not a webhook.** Vikunja ([[kdk-dkr-01]], VLAN 20) cannot reach HA
  ([[kdk-dkr-02]], VLAN 30) inbound (the firewall rule is "8123 only from
  10.1.30.21"). HA polls Vikunja outbound every minute, so there is **no inbound
  path and no new firewall exception**. Notify/resolve latency is ≤ ~1 min
  (button presses record immediately). It also adds **no container** — it is one
  Home Assistant package, `stacks/home-assistant/config/packages/approval_tasks.yaml`.
- **State is a label.** An internal `notified` label marks a task HA has already
  paged for, so the poll never double-pages and never loses its place across an
  HA restart.
- **Logic:**
  - open task **without** `notified` → send the notification, add `notified`.
  - done task **with** `notified` → replace the notification with "resolved",
    remove `notified`.
  - a button press → full-object `POST` the task with `done:true` + the result
    marker, add the `approved`/`skipped` label, add the comment, remove
    `notified`, replace the notification.

Automations/scripts: `approval_poll`, `approval_action`, `script.approval_notify`,
`script.approval_resolved`, notify group `notify.approval_recipients`.

## The job contract (how a job uses it)

A job (or a person) creates an approval task and later reads its outcome. No
Home Assistant change is needed per job.

### Create the task

`PUT https://tasks.kmkdp.com/api/v1/projects/<APPROVALS_PID>/tasks` with a
non-recurring task:

```json
{
  "title": "Approve weekly grocery order ($118.40)",
  "description": "<p>23 items, est $118.40. <a href=\"https://…/cart\">Open cart</a></p><p>APPROVALS-CHOICES: newpi=Approve New Pi; target=Approve Target; skip=Skip</p>",
  "repeat_after": 0
}
```

- **Non-recurring is mandatory** (`repeat_after: 0`). Completing a recurring task
  rolls it forward instead of leaving it done, which would break the loop.
- **Optional link:** put it as an `<a href>` in the description. The notification
  body is tapped to open the Vikunja task; the cart/source link lives in the
  description there. (Vikunja tasks have no dedicated URL field.)
- **Do not** set the `approved`, `skipped`, or `notified` labels — the mechanism
  owns those.

### Due date and reminders (optional)

- Set a Vikunja `due_date` for context if you like. **The due date never
  auto-resolves the task** — a missed deadline just leaves it open.
- To re-nag while the task is still open, set Vikunja **`reminders`** on the
  task (absolute, or relative to the due date). When a reminder time arrives and
  the task is still open, the poll re-sends the notification under the same
  `tag`, replacing the old one. Each reminder fires once.
- Example (weekly grocery): created Thu 18:00 CT, `due_date` Fri 20:00, one
  reminder at Fri 12:00 (i.e. a reminder 8 h before the due date). If still open
  at noon Friday, the phone re-alerts; nothing auto-resolves at 20:00.

### Declare custom buttons (optional)

Add one line to the description:

```
APPROVALS-CHOICES: newpi=Approve New Pi; target=Approve Target; skip=Skip
```

- `id=Title`, `;`-separated. **ids are lowercase `[a-z0-9]`, no underscore**,
  ≤ 20 chars. Put this as its own paragraph (it is stripped from the notification
  body).
- **Max 3 buttons** (Android shows at most 3; tapping the body opens the task, so
  there is no separate Open button). A fourth+ choice is ignored.
- With **no** `APPROVALS-CHOICES` line the buttons are **Approve** + **Skip**.
- Every non-`skip` choice is an "approve-type" button and carries
  `authenticationRequired` (the phone must be unlocked to press it).
- **Choices are mutually exclusive: one press resolves the task.** This
  mechanism does not accumulate several approvals before closing. If you need
  "approve item A *and* item B, then close", make **one approval task per item**.

### Read the outcome

Poll `GET /api/v1/tasks/<id>` and wait for `done == true`, then read:

- **Coarse:** the `labels` array contains `approved` or `skipped`.
- **Which choice:** the description contains `APPROVALS-RESULT: <id>` (e.g.
  `APPROVALS-RESULT: newpi`). `skip` → the task also has the `skipped` label.
- **Audit:** the newest comment names who decided, how, and when.

Example (grocery): the job sees `done:true`, label `approved`, and
`APPROVALS-RESULT: newpi` → order from the New Pi list.

The job needs its own Vikunja token (it may reuse `op://kdk-cluster/vikunja-api-rw`)
scoped to create tasks in the Approvals project and read them back.

## What the owner sees / how to resolve

- **On the phone:** a notification titled "Approve: <task title>" with the task's
  buttons. Press one (Approve-type buttons need the phone unlocked). The
  notification is then replaced by "<outcome>: <title> — Resolved in Vikunja".
- **In Vikunja:** open the task (tap the notification body, or in the app) and
  mark it done, or press the button later. Either way, within a minute the phone
  notification is replaced with the resolved message.

## One-time setup (owner hand steps)

Run from the laptop (1Password desktop + `op`, SSH agent loaded). Do these in
order. Nothing is pasted into a shell where it would land in history.

1. **Create the Approvals project and three labels in the Vikunja UI**
   (tasks.kmkdp.com): a project named `Approvals`, and labels `approved`,
   `skipped`, `notified`.

2. **Look up the IDs** (read-only):

   ```sh
   TOK="$(op read op://kdk-cluster/vikunja-api-rw/credential)"
   curl -s -H "Authorization: Bearer $TOK" https://tasks.kmkdp.com/api/v1/projects \
     | python3 -c "import json,sys;[print(p['id'],p['title']) for p in json.load(sys.stdin)]"
   curl -s -H "Authorization: Bearer $TOK" https://tasks.kmkdp.com/api/v1/labels \
     | python3 -c "import json,sys;[print(l['id'],l['title']) for l in json.load(sys.stdin)]"
   unset TOK
   ```

   Note the Approvals project id and the three label ids.

3. **Mint a narrow HA token in the Vikunja UI** (Settings → API Tokens), owned
   by `jmack`, with the least scope that still lets it: read tasks in the
   Approvals project, **update** tasks, add/remove **labels** on tasks, and
   **create comments**. (Grant the Tasks read+update, Labels read, and Task
   Comments create groups; leave everything else off. It needs no project/label
   *create* and no delete beyond task-label.) Copy the token once.

4. **Store the token in 1Password** (hidden entry, no history):

   ```sh
   read -rs VTOK   # paste the token, press enter (input is hidden)
   op item create --category 'API Credential' --title vikunja-api-approvals \
     --vault kdk-cluster "credential[password]=$VTOK"
   unset VTOK
   ```

5. **Add the five keys to the host `secrets.yaml`** on kdk-dkr-02. The auth line
   comes straight from `op` (never printed); the four numeric lines are the IDs
   from step 2. Replace `<PID>/<APPROVED>/<SKIPPED>/<NOTIFIED>`:

   ```sh
   { printf 'vikunja_approvals_auth: "Bearer %s"\n' "$(op read op://kdk-cluster/vikunja-api-approvals/credential)"
     printf 'vikunja_approvals_project: %s\n' "<PID>"
     printf 'vikunja_label_approved: %s\n'   "<APPROVED>"
     printf 'vikunja_label_skipped: %s\n'    "<SKIPPED>"
     printf 'vikunja_label_notified: %s\n'   "<NOTIFIED>"
   } | ssh kdkadmin@kdk-dkr-02 'sudo tee -a /opt/kdk-lab/home-assistant/config/secrets.yaml >/dev/null \
       && sudo chown root:root /opt/kdk-lab/home-assistant/config/secrets.yaml \
       && sudo chmod 600 /opt/kdk-lab/home-assistant/config/secrets.yaml'
   ```

   Verify (length only, never the value):

   ```sh
   ssh kdkadmin@kdk-dkr-02 "sudo grep -c '^vikunja_approvals_auth:' /opt/kdk-lab/home-assistant/config/secrets.yaml"
   ```

6. **Merge the PR.** Komodo's GitOps sync deploys the `home-assistant` stack,
   which copies `approval_tasks.yaml` into the live config dir.

7. **Restart Home Assistant once** to load the new package (copying does not
   reload HA; a first-time package that adds new integrations needs a restart,
   not "Reload all YAML"): Developer Tools → **YAML** → **Restart Home
   Assistant** → *Restart*. Later edits to the automations/scripts reload with
   *Reload all YAML*; later edits to the `rest_command` block reload with
   `action: rest_command.reload`.

## Add a recipient (second phone)

Edit `stacks/home-assistant/config/packages/approval_tasks.yaml`, under the
`approval_recipients` notify group, uncomment/add one line:

```yaml
      - service: mobile_app_rachels_phone
```

Deploy (GitOps merge) and *Reload all YAML* (notify group reloads without a full
restart on an existing package).

## Troubleshooting

- **No notification arrives.** Check the task is in the Approvals project and
  **not** done. In Developer Tools → States, confirm the `approval_poll`
  automation ran (trace). A 200 with an empty body or non-200 from Vikunja stops
  the poll silently — check Settings → Logs for `rest_command` errors and confirm
  `vikunja_approvals_auth`/`vikunja_approvals_project` are correct. Token scope:
  the poll needs task-read on the Approvals project.
- **Notification arrives every minute.** The `notified` label is not sticking —
  the token lacks label-add on tasks, or `vikunja_label_notified` is wrong.
- **Button does nothing.** Look at the `approval_action` trace. If the task is
  recurring it will roll forward instead of completing — approval tasks must be
  `repeat_after: 0`. If the comment/label steps fail but done succeeds, the token
  lacks comment/label scope (the decision is still recorded as done + result
  marker).
- **"resolved" never replaces the notification.** `clear`/replace needs the app
  to have been used recently (iOS); it will reconcile on the next poll. The task
  is already correct in Vikunja regardless.
- **Who-decided says "someone".** The presser's HA user is not linked to a
  `person` entity with a matching `user_id`; the decision is still recorded.
- **Reminder did not re-nag.** The reminder only re-sends while the task is still
  open and only during the one-minute poll window it lands in; if HA was
  restarting across that minute it is skipped (the original notification is
  still on the phone). Confirm the task's `reminders` timestamp is in the future
  when created and the task is in the Approvals project.

## Rollback

The mechanism is one package file and five host secrets; nothing else references
it.

1. Remove the package: delete
   `stacks/home-assistant/config/packages/approval_tasks.yaml` in a PR, merge,
   let Komodo deploy, then on kdk-dkr-02
   `sudo rm /opt/kdk-lab/home-assistant/config/packages/approval_tasks.yaml` and
   restart HA. (Komodo's `pre_deploy` only copies; it does not delete, so remove
   the live copy by hand.)
2. Optional: delete the five `vikunja_approvals_*` keys from the host
   `secrets.yaml`, revoke the `vikunja-api-approvals` token in the Vikunja UI,
   and archive the `op://kdk-cluster/vikunja-api-approvals` item.
3. The Approvals project, its tasks, and the labels are harmless to leave.

**Related:** [[home-assistant]] · [[dashboard]] · [[kdk-dkr-01]] · [[kdk-dkr-02]]

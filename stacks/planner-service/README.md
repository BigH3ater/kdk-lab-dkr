# planner-service

Daily planner from the **Proton calendar**. Pulls the Proton *"Share via link"* ICS
URL and produces two things:

- a **Kodiak-brand PDF** delivered to the reMarkable **via rmfakecloud sync** (rmapi):
  a single **"Kodiak Planner"** doc in the cloud **Planner** folder, replaced each day
  (mkdir → rm yesterday's → put) — the tablet pulls it on its next sync, even remotely.
  No SSH into xochitl (which would race the live sync engine), and
- a **Markdown agenda** written to `90-Meta/Planners/<date>.md` in the vault (syncs to
  every client via Obsidian Sync).

Proton's client-side ICS plugin renders events live and stores nothing on disk, so the
lab can't read them from the vault — this fetches the same ICS URL directly.

## Flow (`planner.py`, daily Komodo procedure at 05:05)

1. Fetch the ICS (`ICS_URL`), expand recurrence (`recurring_ical_events`), collect
   today + the next `WEEK_DAYS` (7) days.
2. Render: header + **TODAY** (checkbox agenda) + a **NOTES** ruled area + a
   **WEEK AHEAD** strip, via `remarkable/pdf-templates/kodiak_lib.py`.
3. Write the vault note (always), then upload the PDF to rmfakecloud via `rmapi`
   (`Planner/Kodiak Planner`, replacing yesterday's). The tablet pulls it on its next
   sync; no device connection needed at run time.

Pages Pushover priority-1 on failure.

## Config (`.env` + compose, op-injected)

`ICS_URL` = `op://kdk-ops/proton-calendar-ics/url` (the Proton share link — a secret;
anyone with it reads the calendar), `WEEK_DAYS`. Delivery uses the shared rmapi device
auth mounted at `/config/.rmapi` (`RMAPI_HOST=http://rmfakecloud:3000`, registered once
against rmfakecloud — same `.rmapi` as `readwise-to-remarkable`); no device password
needed. The container joins the external `apps_default` network to reach `rmfakecloud`.

## Get the Proton ICS link

Proton Calendar → the calendar's **⋯ → Share → Share with anyone (via link)** → copy the
URL → store it at `op://kdk-ops/proton-calendar-ics/url`. Then deploy the stack.

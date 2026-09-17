# planner-service

Daily planner from the **Proton calendar**. Pulls the Proton *"Share via link"* ICS
URL and produces two things:

- a **Kodiak-brand PDF** pushed to the reMarkable (a single pinned **"Kodiak Planner"**
  doc, replaced in place each day — stable UUID in state), and
- a **Markdown agenda** written to `90-Meta/Planners/<date>.md` in the vault (syncs to
  every client via Obsidian Sync).

Proton's client-side ICS plugin renders events live and stores nothing on disk, so the
lab can't read them from the vault — this fetches the same ICS URL directly.

## Flow (`planner.py`, daily Komodo procedure at 05:05)

1. Fetch the ICS (`ICS_URL`), expand recurrence (`recurring_ical_events`), collect
   today + the next `WEEK_DAYS` (7) days.
2. Render: header + **TODAY** (checkbox agenda) + a **NOTES** ruled area + a
   **WEEK AHEAD** strip, via `remarkable/pdf-templates/kodiak_lib.py`.
3. Write the vault note (always), then push the PDF to the tablet **if it's awake**
   (dropbear SSH; restarts `xochitl`). Tablet asleep → note still written, PDF next run.

Pages Pushover priority-1 on failure.

## Config (`.env`, op-injected)

`ICS_URL` = `op://kdk-ops/proton-calendar-ics/url` (the Proton share link — a secret;
anyone with it reads the calendar). `RM_HOST` / `RM_PW` (`op://kdk-ops/remarkable-device`),
`WEEK_DAYS`.

## Get the Proton ICS link

Proton Calendar → the calendar's **⋯ → Share → Share with anyone (via link)** → copy the
URL → store it at `op://kdk-ops/proton-calendar-ics/url`. Then deploy the stack.

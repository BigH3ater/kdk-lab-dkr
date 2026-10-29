# Wizarr wizard steps (what an invitee sees after accepting)

Wizarr stores the post-invite "wizard" pages in its database (Settings → Wizard
Steps), not on disk, so git holds the **source text** and the owner pastes it in.
One file per page, in order; the front-matter `title` is the page title.

Markdown extras Wizarr understands (same as its defaults): `|||` opens/closes a
column block; `{{ widget:button url="…" text="…" }}` renders a button;
`[text](url){target=_blank}` opens in a new tab; `{{ widget:recently_added_media }}`
shows the latest additions. Plain Markdown otherwise. HTML comments (`<!-- TODO -->`)
are not shown to the invitee: they carry the owner's notes for Seerr, Libreseerr and
Audiobookshelf until those are ready.

## Load them (owner, Wizarr admin UI, ~5 minutes)

1. Settings → **Wizard Steps** → server type **Jellyfin**.
2. Delete or edit the three default pages (What is Jellyfin / Download clients / Tips).
3. Add one page per file in `jellyfin/`, in filename order, title = the front-matter
   `title`, body = everything below the front-matter.
4. Settings → **Wizard Bundles** → New bundle `household` → add the six pages in
   order. Pick that bundle on every invite (Invites → New → Wizard bundle).
5. Preview with a test invite (see `docs/services/wizarr.md`, invite test).

When the text changes in git, repeat the paste for the changed page only.

## Writing rules

Written for people who do not know what a server is: one idea per line, the exact
address to type, which button to press, what they should see. No acronyms without
the plain words next to them. Only the approved apps are listed (Neptune on Apple,
the official Jellyfin app on Android and Roku, the browser with "Sign in with SSO");
anything else is unsupported on purpose.

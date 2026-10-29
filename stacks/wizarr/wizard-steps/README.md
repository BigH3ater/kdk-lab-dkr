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

## Images

Screenshots live in `stacks/wizarr/wizard-images/` (PNG, phone-width, no personal
data, no real codes) and are bind-mounted read-only into Wizarr's static folder, so a
page references them as `![caption](/static/img/guide/<file>.png)`. Wanted set,
captured during the invite test (the pages already reference these names):

| File | Shows |
|---|---|
| `sso-first-login.png` | sso.kmkdp.com sign-in, the "enter the code from your email" box, the Add passkey / Add one-time password choice |
| `neptune-add-server.png` | Neptune: Add server with the address typed, and the sign-in screen with the Quick Connect code |
| `jellyfin-web-sso-button.png` | jellyfin.kmkdp.com login page with the "Sign in with SSO" button |
| `sso-reset-password.png` | the "Reset password?" link under the sign-in box |

Until a file exists the page shows a broken-image icon; add the files and
`RestartStack wizarr` is not needed (bind mount, served live).

Wizarr can also **export a bundle** (Settings → Wizard → Custom Bundles); keep the
export next to these files as `household.bundle.json` once the pages are loaded, so
a rebuild is an import instead of six pastes.

## Writing rules

Written for people who do not know what a server is: one idea per line, the exact
address to type, which button to press, what they should see. No acronyms without
the plain words next to them. Only the approved apps are listed (Neptune on Apple TV/iPhone/iPad,
the official Jellyfin app on Android and Roku, the browser with "Sign in with SSO");
anything else is unsupported on purpose. Say "2FA" and "TOTP code", never
"authenticator app" on its own: the readers do not know the term. Quick Connect is
fine on iPhone/iPad; on Apple TV the pages say username + password (Neptune's tvOS
add-account via Quick Connect is broken as of 1.4.2, bug JyVW5M). The Apple section
header uses the Apple logo glyph (U+F8FF): it renders on Apple devices and as a plain
box elsewhere, which the owner accepted.

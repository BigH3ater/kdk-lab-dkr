---
title: Jellyfin SSO (Authelia OIDC)
---

# Jellyfin SSO: SSO Authentication plugin + Authelia

Adds **Sign in with SSO** to the Jellyfin web login. It uses the K0lin fork of the SSO
plugin (`SSO Authentication` 5.1.1, built for Jellyfin 12) and the Authelia OIDC client
`jellyfin` (batch 5b). The password form, Quick Connect and the LDAP plugin all keep
working. Background and day-to-day use: [[jellyfin]] § SSO. Client and policy:
[[identity]].

| Item | Value | Source |
|---|---|---|
| Plugin | `SSO Authentication` 5.1.1, GUID `505ce9d1-d916-42fa-86ca-673ef241d7df`, `targetAbi 12.0.0.0` (needs Jellyfin ≥ 12.0, so 12.3 and 12.4 are fine) | `github.com/K0lin/jellyfin-plugin-sso` release `v5.1.1`, `build.yaml` |
| Repository URL | `https://raw.githubusercontent.com/k0lin/jellyfin-plugin-sso/manifest-release/manifest.json` | the fork's README, "Installing" |
| Redirect URI | `https://jellyfin.kmkdp.com/sso/OID/redirect/authelia` (`authelia` = provider name in the plugin) | `SSOController.cs` `[HttpGet("OID/redirect/{provider}")]` |
| Start URL | `https://jellyfin.kmkdp.com/sso/OID/start/authelia` | `SSOController.cs` `[HttpGet("OID/start/{provider}")]` |
| Authelia client | `jellyfin`, policy `jellyfin_access` (`two_factor`, `jellyfin-users` or `lldap_admin`), PKCE S256 required, `client_secret_post` | `stacks/identity/authelia/configuration.yml` |
| Secret | `op://kdk-cluster/jellyfin-oidc/password`: plaintext in the plugin, pbkdf2 digest in `authelia-app-secrets/jellyfin_client_secret` | this runbook |

> [!danger] Order matters
> Authelia refuses to start if `jellyfin_client_secret` is missing, because the config
> templates it in. Steps 1–2 (secret file on dmz-01) come **before** the merge and Authelia
> restart in step 3. A failed Authelia start takes every SSO login down.

Prerequisite: batch 5 (PR #70) is merged and deployed, so `authelia-secrets` is owned by
`8091` and the `identity_ldap` network exists. Every account that will use SSO must be in
`jellyfin-users` or `lldap_admin` in `users.kmkdp.com`.

## 1. Create the client secret (laptop)

`op` runs on the laptop only. The value never appears on screen: `op item create` keeps
the generated password concealed in its output.

```bash
op item create --vault kdk-cluster --category password --title jellyfin-oidc --generate-password=48,letters,digits
```

## 2. Write the digest to dmz-01 (laptop → ssh stdin)

The plaintext travels over ssh stdin. Authelia's own image hashes it on dmz-01, and only
the `$pbkdf2-sha512$…` digest is written to disk:

```bash
# a. hash the plaintext inside Authelia (nothing printed), keep the digest in 1Password
D=$(op read op://kdk-cluster/jellyfin-oidc/password | ssh kdkadmin@192.168.191.20 'sudo docker exec -i authelia authelia crypto hash generate pbkdf2 --variant sha512 --password "$(cat)"' | sed -n 's/^Digest: //p')
op item edit --vault kdk-cluster authelia-app-secrets "jellyfin_client_secret=$D"
# b. the host file comes from 1Password, like every other authelia-app-secrets file
op read op://kdk-cluster/authelia-app-secrets/jellyfin_client_secret | ssh kdkadmin@192.168.191.20 'sudo install -m 400 -o 8091 -g 8091 /dev/stdin /opt/kdk-lab/identity/authelia-secrets/authelia-app-secrets/jellyfin_client_secret'
```

Convention (from #70): `jellyfin-oidc/password` is the plaintext the plugin gets,
`authelia-app-secrets/jellyfin_client_secret` is the pbkdf2 digest Authelia reads;
the file on dmz-01 is installed from the latter, never written straight from a pipe.

Check it. This prints only the mode, the owner and the public 15-character prefix:

```bash
ssh kdkadmin@192.168.191.20 'f=/opt/kdk-lab/identity/authelia-secrets/authelia-app-secrets/jellyfin_client_secret; sudo stat -c "%a %U:%G" $f; sudo cut -c1-15 $f'
# 400 8091:8091
# $pbkdf2-sha512$
```

If the second line is empty, the `sed` found no `Digest:` line. Re-run step 2 and do not
restart Authelia until the check passes. (`sudo` must not prompt, because stdin is the
pipe. kdkadmin has NOPASSWD sudo on the compose hosts.)

## 3. Land the Authelia config (merge this PR)

Merge the PR, then in Komodo run **`RestartStack identity services=[authelia]`**. The
file is a compose `configs:` entry, so a config-only change is not picked up without the
restart. Then:

```bash
curl -s https://sso.kmkdp.com/api/health                                        # {"status":"OK"}
ssh kdkadmin@192.168.191.20 'docker logs authelia --since 5m 2>&1 | grep -iE "level=(error|fatal)|jellyfin"'   # nothing
```

## 4. Install the plugin (Jellyfin dashboard, from the LAN)

The Cloudflare edge blocks `/Plugins` and `/System/Configuration`, so do this from a LAN
or tailnet browser signed in as the Jellyfin admin (`kdkadmin`).

1. **Dashboard → Plugins → Repositories → +**. Name: `SSO Authentication (K0lin)`. URL:
   `https://raw.githubusercontent.com/k0lin/jellyfin-plugin-sso/manifest-release/manifest.json`. Save.
2. **Dashboard → Plugins → Catalog → SSO Authentication**. Pick version **5.1.1**
   (not `0.0.0.9000`: that is the nightly) → **Install**.
3. Restart Jellyfin: **Dashboard → Restart**, or Komodo `RestartStack jellyfin`.
4. **Dashboard → Plugins**: `SSO Authentication 5.1.1` reads **Active**.

## 5. Configure the provider (Dashboard → Plugins → SSO Authentication)

Copy the secret to the clipboard on the laptop. Use one of these, depending on the
desktop:

```bash
op read --no-newline op://kdk-cluster/jellyfin-oidc/password | wl-copy                    # Wayland
op read --no-newline op://kdk-cluster/jellyfin-oidc/password | xclip -selection clipboard # X11
```

Fill the form (a new name creates a provider, the same name updates it):

| Field | Value | Why |
|---|---|---|
| Name of OpenID Provider | `authelia` | becomes the `/sso/OID/redirect/authelia` path. Must match the Authelia redirect URI exactly |
| OpenID Endpoint | `https://sso.kmkdp.com` | the issuer. The plugin appends `/.well-known/openid-configuration` itself |
| OpenID Client ID | `jellyfin` | |
| OpenID client secret | paste from the clipboard | |
| Enabled | ✅ | |
| Enable Authorization by Plugin | ✅ | needed for the admin-role mapping. The plugin then sets admin/folders/Live TV on every SSO login |
| Enable All Folders | ✅ | household policy: every SSO user sees all libraries (TV + Movies). Untick and pick *Enabled Folders* if a user must be restricted |
| Content Download Permission | *Leave unchanged* | keeps each user's current flag (Wizarr invites start with downloads off) |
| Roles | `jellyfin-users` and `lldap_admin` (one per line) | second check after Authelia's `jellyfin_access`. Exact, case-sensitive |
| Admin Roles | `lldap_admin` | the same group the LDAP plugin's admin filter uses |
| Preserve Existing Admin Permissions | ✅ | SSO can grant admin but never takes it away |
| Enable Role-Based Folder Access / Live TV RBAC | ☐ | |
| Enable Live TV Access / Management By Default | ☐ (☑ Access only if Dashboard → Live TV has a tuner) | the plugin writes these on **every** SSO login, even with authorization off |
| Role Claim | `groups` | Authelia's groups claim, from UserInfo |
| OIDC Scopes | `groups` and `email` (one per line) | added to the default `openid profile`. All four are allowed on the client |
| Override default OIDC scopes | ☐ | |
| Additional OIDC Authorization Parameters | empty | |
| Set default Provider | `Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin` | this is the Jellyfin *authentication provider* written to the user at SSO login, not a default IdP. Keeping every user on the LDAP plugin keeps their LLDAP password working in apps, Seerr and Quick-Connect-less clients. It is the same value `wizarr-sync` sets |
| Set default username claim | empty (= `preferred_username`, the LLDAP uid) | existing users are matched **by username** |
| Avatar URL format | empty | |
| Scheme Override | `https` | the redirect is always built as `https://jellyfin.kmkdp.com/...`, whatever header reaches Jellyfin |
| Port Override | empty | |
| Disable Pushed Authorization | ✅ | the fork's Authelia page says to set it. Authelia advertises PAR, the plugin would try it, and it is not tested here |
| Disable HTTPS discovery / Do not validate endpoints / Do not validate issuer / Do not load profile | ☐ all | leave validation on. *Do not load profile* must stay off, or the `groups` claim is missing |

**Save**, then restart Jellyfin again (the page says configuration changes need a
restart). Clear the clipboard: `wl-copy --clear` or `printf '' | xclip -selection clipboard`.

## 6. Web login page (Dashboard → General → Branding)

The plugin adds no button by itself and has **no auto-redirect option**. The fork's wiki
(Quick-Start) puts a button in the login disclaimer:

**Login disclaimer:**

```html
<form action="https://jellyfin.kmkdp.com/sso/OID/start/authelia">
  <button class="raised block emby-button button-submit">Sign in with SSO</button>
</form>
```

**Custom CSS** (append):

```css
a.raised.emby-button { padding: 0.9em 1em; color: inherit !important; }
.disclaimerContainer { display: block; }
```

If the web client's sanitiser strips the `<form>` (the button does not appear under the
password form), use a link instead:
`<a class="raised block emby-button button-submit" href="https://jellyfin.kmkdp.com/sso/OID/start/authelia">Sign in with SSO</a>`.

Least-friction login: Jellyfin has no hook to auto-start SSO, so give people the start URL
directly: `https://jellyfin.kmkdp.com/sso/OID/start/authelia` as a browser bookmark or a
home-dashboard tile. Each kind of session behaves differently:

- **Live 2FA session on `sso.kmkdp.com`** (from any app): no prompt. You land in Jellyfin.
- **No session:** Authelia's login page opens. `jellyfin_access` is `two_factor`, and an
  Authelia 4.39 passkey counts as **one** factor. So *Sign in with a passkey* is followed
  by a password prompt, or the password is followed by a passkey/TOTP. Making a
  user-verified passkey count as 2FA needs `webauthn.experimental_enable_passkey_uv_two_factors`.
  That option is experimental and is **not** set by this PR.
- After the first login, the Jellyfin web client keeps its own token until you sign out.

## 7. Tests

| # | Test | Expect |
|---|---|---|
| 1 | LAN browser, private window → `jellyfin.kmkdp.com` → **Sign in with SSO** as `jmack` (passkey at `sso.kmkdp.com`, plus the password prompt) | back on the Jellyfin home as jmack, with watch history and resume points. Dashboard → Users → jmack: provider is still *LDAP-Authentication*. Admin only if jmack is in `lldap_admin` |
| 2 | Same from a phone off Wi-Fi (through the tunnel) | same as 1. No Cloudflare block page on `/sso/OID/...` |
| 3 | Neptune on Apple TV → Quick Connect → approve the code in the SSO web session (Settings → Quick Connect) | Apple TV signed in as jmack |
| 4 | iPhone Neptune → Quick Connect, same way | signed in |
| 5 | Komodo: run Procedure `wizarr-sync`. Open Jellystat | sync exits 0 with no changes. Jellystat shows current sessions (API keys are unaffected) |
| 6 | LAN browser: password form as `kdkadmin` (local provider, not SSO) | logs in. Do this from the LAN, because remote access for kdkadmin is meant to be off (posture H1) |
| 7 | Wizarr: new invite → invitee accepts → wait for the next `wizarr-sync` (≤ 15 min) → invitee clicks **Sign in with SSO** | Authelia asks them to register a second factor (e-mail one-time code, then TOTP or passkey), then Jellyfin opens as their Wizarr-created user. The LDAP password from the invite still works in apps |
| 8 | An LLDAP user in neither group tries **Sign in with SSO** | Authelia `access_denied`. No Jellyfin account is created |

Logs: `docker logs jellyfin 2>&1 | grep -i 'SSO\|OpenID'` (role mismatches list the claims
seen). Authelia: `docker logs authelia 2>&1 | grep -i jellyfin`.

## 8. Rollback

- **Turn SSO off:** Dashboard → Plugins → SSO Authentication → load `authelia` → untick
  **Enabled** → Save → restart Jellyfin (or uninstall the plugin). Remove the disclaimer
  button. Users keep the LDAP provider, so password and Quick Connect logins carry on.
- **The Authelia client can stay.** It is inert without the plugin.
- **Full removal:** revert this PR → `RestartStack identity services=[authelia]` → delete
  `authelia-app-secrets/jellyfin_client_secret` → archive `kdk-cluster/jellyfin-oidc`.
  Do it in this order, so Authelia never starts with a client whose secret file is gone.

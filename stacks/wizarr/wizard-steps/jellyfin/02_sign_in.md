---
title: "Step 1: Set up your sign-in"
---

## 🔐 Step 1: Set up your sign-in

Your account has **2FA** ("two-factor authentication"): after your password, you confirm it is you with a second step. You choose the second step once, now. It takes two minutes.

1. Open **https://sso.kmkdp.com** in a browser (on your phone is fine).
2. Sign in with the **username and password you just chose**.
3. The page asks you to add your second step. Before it lets you, it sends an **email from no-reply@kmkdp.com with an 8-character code**. Open the email, type the code into the page (the code works for 5 minutes; check Spam if it does not arrive).
4. Pick one of these:
   - **Passkey** (easiest): press **Add passkey**, then approve with Face ID, Touch ID, or your phone's unlock. From now on, that is your second step.
   - **TOTP code**: press **Add one-time password**, scan the square code with the Google Authenticator or Microsoft Authenticator app on your phone (free in the App Store / Google Play), then type the 6 digits the app shows. From now on, the app gives you a new 6-digit code every 30 seconds.
5. The page says you are authenticated. Done.

![Step 1 screens: sign in, code email, passkey or TOTP](/static/img/guide/sso-first-login.png)

Tick **"Remember me"** when signing in and you will not be asked again on that device for a while.

{{ widget:button url="https://sso.kmkdp.com" text="Open the sign-in page" }}

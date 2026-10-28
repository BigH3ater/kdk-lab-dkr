// Node-RED runtime settings -- kdk-dkr-02, stack home-assistant.
//
// GITOPS-OWNED (2026-10-28): source of truth is this file. The stack's
// pre_deploy copies it to /opt/kdk-lab/home-assistant/node-red/settings.js
// (the container's /data). Node-RED reads it ONLY at start: a change that
// does not also change compose.yaml needs a node-red restart to take effect.
//
// Values not set here are Node-RED 4.1's built-in defaults. The non-security
// keys below are the stock 4.1 settings.js values, written out so a reviewer
// can see them without the 600 lines of comments.
//
// Secrets come from the host-managed env file
// /opt/kdk-lab/home-assistant/node-red.env (root 600, see compose.yaml):
//   NODE_RED_ADMIN_HASH         bcrypt hash for the editor/admin API user
//   NODE_RED_CREDENTIAL_SECRET  key that encrypts flows_cred.json
// Both are read once and then removed from process.env, so a function node
// cannot read them back with env.get().

const adminHash = process.env.NODE_RED_ADMIN_HASH;
const credentialSecret = process.env.NODE_RED_CREDENTIAL_SECRET;
delete process.env.NODE_RED_ADMIN_HASH;
delete process.env.NODE_RED_CREDENTIAL_SECRET;

// Fail closed, not open: with no hash the editor and admin API refuse every
// login (flows keep running). Without this, a missing env file would bring
// Node-RED back up with no adminAuth at all.
const adminAuth = adminHash
    ? {
          type: "credentials",
          users: [{ username: "jmack", password: adminHash, permissions: "*" }],
      }
    : {
          type: "credentials",
          users: () => Promise.resolve(null),
          authenticate: () => Promise.resolve(null),
      };
if (!adminHash) {
    console.warn("[settings.js] NODE_RED_ADMIN_HASH unset: editor and admin API locked");
}

module.exports = {
    flowFile: "flows.json",
    flowFilePretty: true,

    // Before 2026-10-28 this was `false`: flows_cred.json was plaintext. With
    // a string key, Node-RED loads the existing plaintext credentials and
    // writes them back encrypted on the next deploy. Unset (env file missing)
    // keeps the old plaintext behaviour rather than inventing a key.
    // Changing the key later makes flows_cred.json unreadable: keep it in
    // 1Password and never rotate it without re-entering every credential.
    credentialSecret: credentialSecret || false,

    adminAuth,

    uiPort: process.env.PORT || 1880,

    diagnostics: { enabled: true, ui: true },
    runtimeState: { enabled: false, ui: false },

    logging: {
        console: {
            level: "info",
            metrics: false,
            // Admin API access events, with the user once adminAuth is on.
            audit: true,
        },
    },

    exportGlobalContextKeys: false,
    externalModules: {},

    editorTheme: {
        palette: {},
        projects: { enabled: false, workflow: { mode: "manual" } },
        codeEditor: { lib: "monaco", options: {} },
        markdownEditor: { mermaid: { enabled: true } },
        multiplayer: { enabled: false },
    },

    functionExternalModules: true,
    globalFunctionTimeout: 0,
    functionTimeout: 0,
    functionGlobalContext: {},
    // Palette order carried over from the pre-GitOps host settings.js (HA first).
    paletteCategories: [
        "home_assistant",
        "home_assistant entities",
        "subflows",
        "common",
        "function",
        "network",
        "sequence",
        "parser",
        "storage",
    ],
    debugMaxLength: 1000,
    mqttReconnectTime: 15000,
    serialReconnectTime: 15000,
};

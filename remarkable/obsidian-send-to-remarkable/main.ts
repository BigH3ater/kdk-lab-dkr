import {
  App,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TAbstractFile,
  TFile,
  TFolder,
  normalizePath,
} from "obsidian";

/**
 * Send to reMarkable
 *
 * The plugin owns no transport and no secrets. It writes a small JSON "request"
 * into <queueFolder> in the vault. The kdk-lab `obsidian-to-koreader` job reads
 * that folder on the lab mirror (reached through the existing Obsidian Sync),
 * renders the requested scope to PDF, pushes it to the reMarkable over SSH, and
 * moves the request to <doneFolder> with a status line, which this plugin reads
 * back to show the last result.
 */

type SendMode = "combined" | "per-note";
type Scope = "note" | "folder" | "vault";

interface SendToRemarkableSettings {
  queueFolder: string;
  doneFolder: string;
  defaultMode: SendMode;
  targetSubdir: string;
}

const DEFAULT_SETTINGS: SendToRemarkableSettings = {
  queueFolder: "_remarkable/queue",
  doneFolder: "_remarkable/done",
  defaultMode: "combined",
  targetSubdir: "",
};

interface SendRequest {
  id: string;
  scope: Scope;
  path?: string;
  mode: SendMode;
  target_subdir?: string;
  created: string;
  source: string;
}

const SOURCE = "obsidian-send-to-remarkable";

export default class SendToRemarkablePlugin extends Plugin {
  settings: SendToRemarkableSettings = DEFAULT_SETTINGS;

  async onload(): Promise<void> {
    await this.loadSettings();

    this.addRibbonIcon("tablet", "Send note to reMarkable", () => {
      void this.sendActiveNote();
    });

    this.addCommand({
      id: "send-active-note",
      name: "Send active note to reMarkable",
      callback: () => void this.sendActiveNote(),
    });

    this.addCommand({
      id: "send-whole-vault",
      name: "Send whole vault to reMarkable",
      callback: () =>
        void this.enqueue({ scope: "vault", mode: this.settings.defaultMode }, "whole vault"),
    });

    this.addCommand({
      id: "show-last-result",
      name: "Show last reMarkable result",
      callback: () => void this.showLastResult(),
    });

    // Folder context-menu action.
    this.registerEvent(
      this.app.workspace.on("file-menu", (menu, file: TAbstractFile) => {
        if (file instanceof TFolder) {
          menu.addItem((item) =>
            item
              .setTitle("Send folder to reMarkable")
              .setIcon("tablet")
              .onClick(() =>
                void this.enqueue(
                  { scope: "folder", path: file.path, mode: this.settings.defaultMode },
                  `folder "${file.path}"`,
                ),
              ),
          );
        } else if (file instanceof TFile && file.extension === "md") {
          menu.addItem((item) =>
            item
              .setTitle("Send note to reMarkable")
              .setIcon("tablet")
              .onClick(() =>
                void this.enqueue(
                  { scope: "note", path: file.path, mode: this.settings.defaultMode },
                  `note "${file.basename}"`,
                ),
              ),
          );
        }
      }),
    );

    this.addSettingTab(new SendToRemarkableSettingTab(this.app, this));
  }

  private async sendActiveNote(): Promise<void> {
    const file = this.app.workspace.getActiveFile();
    if (!file || file.extension !== "md") {
      new Notice("Send to reMarkable: no active Markdown note.");
      return;
    }
    await this.enqueue(
      { scope: "note", path: file.path, mode: this.settings.defaultMode },
      `note "${file.basename}"`,
    );
  }

  /** Write a request JSON into the queue folder. */
  private async enqueue(
    partial: { scope: Scope; path?: string; mode: SendMode },
    label: string,
  ): Promise<void> {
    try {
      const queue = normalizePath(this.settings.queueFolder);
      await this.ensureFolder(queue);
      const id = this.newId();
      const req: SendRequest = {
        id,
        scope: partial.scope,
        mode: partial.mode,
        created: new Date().toISOString(),
        source: SOURCE,
      };
      if (partial.path) req.path = partial.path;
      const sub = this.settings.targetSubdir.trim().replace(/^\/+|\/+$/g, "");
      if (sub) req.target_subdir = sub;

      const file = `${queue}/${id}.json`;
      await this.app.vault.create(file, JSON.stringify(req, null, 2) + "\n");
      new Notice(
        `Queued ${label} for reMarkable (${partial.mode}). It is delivered on the tablet's next sync run.`,
      );
    } catch (e) {
      new Notice(`Send to reMarkable failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  /** Read the newest request in the done folder and report its status. */
  private async showLastResult(): Promise<void> {
    try {
      const done = normalizePath(this.settings.doneFolder);
      const folder = this.app.vault.getFolderByPath(done);
      if (!folder) {
        new Notice("Send to reMarkable: no results yet (done folder not found).");
        return;
      }
      const jsons = folder.children.filter(
        (c): c is TFile => c instanceof TFile && c.extension === "json",
      );
      if (jsons.length === 0) {
        new Notice("Send to reMarkable: no results yet.");
        return;
      }
      jsons.sort((a, b) => b.stat.mtime - a.stat.mtime);
      const latest = jsons[0];
      const raw = await this.app.vault.read(latest);
      const rec = JSON.parse(raw) as {
        id?: string;
        status?: string;
        status_detail?: string;
        delivered?: string[];
        processed_at?: string;
      };
      const n = rec.delivered ? rec.delivered.length : 0;
      new Notice(
        `Last reMarkable result — ${rec.status ?? "?"} (${rec.id ?? latest.basename}): ` +
          `${n} PDF(s)` +
          (rec.status_detail ? `, ${rec.status_detail}` : "") +
          (rec.processed_at ? `\n${rec.processed_at}` : ""),
        10000,
      );
    } catch (e) {
      new Notice(`Send to reMarkable: could not read last result: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  /** Create a folder (and parents) if it does not exist. */
  private async ensureFolder(path: string): Promise<void> {
    const parts = path.split("/").filter(Boolean);
    let cur = "";
    for (const p of parts) {
      cur = cur ? `${cur}/${p}` : p;
      if (!this.app.vault.getFolderByPath(cur)) {
        try {
          await this.app.vault.createFolder(cur);
        } catch (e) {
          // Another concurrent create (or sync) may have won the race; ignore if it now exists.
          if (!this.app.vault.getFolderByPath(cur)) throw e;
        }
      }
    }
  }

  private newId(): string {
    const d = new Date();
    const p = (n: number, w = 2) => String(n).padStart(w, "0");
    const stamp =
      `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}` +
      `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
    const rand = Math.random().toString(36).slice(2, 7);
    return `${stamp}-${rand}`;
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, (await this.loadData()) as object);
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}

class SendToRemarkableSettingTab extends PluginSettingTab {
  plugin: SendToRemarkablePlugin;

  constructor(app: App, plugin: SendToRemarkablePlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("Queue folder")
      .setDesc(
        "Vault-relative folder where requests are written. Must match the lab job's QUEUE_SUBDIR (default _remarkable/queue). The vault's Obsidian Sync carries it to the lab mirror.",
      )
      .addText((t) =>
        t
          .setPlaceholder("_remarkable/queue")
          .setValue(this.plugin.settings.queueFolder)
          .onChange(async (v) => {
            this.plugin.settings.queueFolder = v.trim() || DEFAULT_SETTINGS.queueFolder;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Done folder")
      .setDesc("Vault-relative folder the lab job moves processed requests into (default _remarkable/done). Read by 'Show last reMarkable result'.")
      .addText((t) =>
        t
          .setPlaceholder("_remarkable/done")
          .setValue(this.plugin.settings.doneFolder)
          .onChange(async (v) => {
            this.plugin.settings.doneFolder = v.trim() || DEFAULT_SETTINGS.doneFolder;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Default mode (folder / vault)")
      .setDesc("Combined = one PDF for the whole scope. Per-note = one PDF per note (folder) or per section (vault). A single note is always one PDF.")
      .addDropdown((d) =>
        d
          .addOption("combined", "Combined (one PDF)")
          .addOption("per-note", "Per note / per section")
          .setValue(this.plugin.settings.defaultMode)
          .onChange(async (v) => {
            this.plugin.settings.defaultMode = v as SendMode;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Target subfolder (optional)")
      .setDesc("Extra subfolder under the tablet's books/kodiak-codex for the delivered PDFs. Leave empty to mirror the note's vault folder under books/kodiak-codex.")
      .addText((t) =>
        t
          .setPlaceholder("(none)")
          .setValue(this.plugin.settings.targetSubdir)
          .onChange(async (v) => {
            this.plugin.settings.targetSubdir = v.trim();
            await this.plugin.saveSettings();
          }),
      );
  }
}

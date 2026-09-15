import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

/**
 * Quartz 4 configuration for the kdk-lab docs site (docs.kmkdp.com).
 *
 * This file is a faithful copy of Quartz v4.5.2's default quartz.config.ts with
 * ONLY the theme (colors + typography), pageTitle and baseUrl changed to the
 * Kodiak design system (see Homelab/Brand.md — "Kodiak · Bernese palette").
 * The docs compose builder copies it over the pinned Quartz clone before build,
 * so it must stay structurally in sync with v4.5.2's plugin API.
 *
 *   Light mode = Kodiak PAPER ground (application-UI surface).
 *   Dark mode  = Kodiak INK ground   (rust -> ember, bark -> ash per the rules).
 */
const config: QuartzConfig = {
  configuration: {
    pageTitle: "kdk-lab",
    pageTitleSuffix: "",
    enableSPA: true,
    enablePopovers: true,
    analytics: null,
    locale: "en-US",
    baseUrl: "docs.kmkdp.com",
    ignorePatterns: ["private", "templates", ".obsidian"],
    defaultDateType: "modified",
    theme: {
      fontOrigin: "googleFonts",
      cdnCaching: true,
      // Kodiak application-UI type stack: Space Grotesk display/body,
      // JetBrains Mono for code/labels/terminal (Brand.md typography).
      typography: {
        header: "Space Grotesk",
        body: "Space Grotesk",
        code: "JetBrains Mono",
      },
      colors: {
        // Paper ground. light=--paper, borders=--paper-dark, muted=--stone,
        // text/headers=--ink, accent=--rust, hover=--danger, marks=--ember.
        lightMode: {
          light: "#ece2d0",
          lightgray: "#dfd4c0",
          gray: "#9a9183",
          darkgray: "#1b1a18",
          dark: "#1b1a18",
          secondary: "#9d4a25",
          tertiary: "#8c2f1b",
          highlight: "rgba(157, 74, 37, 0.10)",
          textHighlight: "#c9743f55",
        },
        // Ink ground. light=--ink, borders=--ink-hairline, muted=--ash,
        // text=--paper, headers=--paper-warm, accent=--ember (never rust on ink),
        // hover=--danger-ink, marks=--warn-ink tint.
        darkMode: {
          light: "#1b1a18",
          lightgray: "#423d38",
          gray: "#8d8377",
          darkgray: "#ece2d0",
          dark: "#f7f1e6",
          secondary: "#c9743f",
          tertiary: "#e0705a",
          highlight: "rgba(201, 116, 63, 0.15)",
          textHighlight: "#d9a44155",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({
        priority: ["frontmatter", "git", "filesystem"],
      }),
      Plugin.SyntaxHighlighting({
        theme: {
          light: "github-light",
          dark: "github-dark",
        },
        keepBackground: false,
      }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
      Plugin.Description(),
      Plugin.Latex({ renderEngine: "katex" }),
    ],
    filters: [Plugin.RemoveDrafts()],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({
        enableSiteMap: true,
        enableRSS: true,
      }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.Favicon(),
      Plugin.NotFoundPage(),
      // Comment out CustomOgImages to speed up build time
      Plugin.CustomOgImages(),
    ],
  },
}

export default config

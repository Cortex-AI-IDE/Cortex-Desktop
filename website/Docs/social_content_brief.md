# Cortex AI IDE — Social Content & Image-Prompt Brief

> **Purpose of this document:** paste this whole file into a fresh AI chat
> session (any platform) when you want to generate daily social media posts
> or image-generation prompts for Cortex. It contains everything that
> session needs to know — you should NOT need to explain the product again.
> Everything in here is pulled directly from the live product/website, not
> invented.

---

## 1. What Cortex AI IDE actually is

**One-line description:** Cortex is an agentic AI code editor for Windows —
a native desktop IDE (not a browser tab, not Electron) where AI agents plan,
edit, run, and test code autonomously, using whichever AI provider and API
key the user brings themselves.

**Hero tagline (from the homepage):**
> "The agentic AI IDE built for Windows."
> "Hand Cortex a goal — its agents plan, edit, run, and test code
> autonomously in a native editor. Bring your own API keys: your machine,
> your models, your rules."

**Secondary tagline (used inside the app's editor welcome screen):**
> "Think Limitless. Build Beyond."

**What makes it different (the actual differentiators — use these, don't invent others):**
- **Native Windows app**, built with Python + PyQt6 + Qt WebEngine — not an
  Electron wrapper, not a web app.
- **BYOK (Bring Your Own Key)** — the core model. Users plug in their own
  API key for a provider; Cortex never proxies or marks up token costs.
  Keys are stored locally in **Windows Credential Manager**, encrypted with
  **DPAPI**, and are **never sent to Cortex's servers** — requests go
  straight from the user's machine to the LLM provider.
- **Real agents, not autocomplete.** Agents take a goal, make a plan, edit
  files, run shell commands, read the output, and iterate — genuine
  read/execute/verify loops, not single-shot suggestions.
- **24+ agent tools**: file read/write/edit, bash, grep, glob, semantic
  search, web search, git, and more.
- **5 BYOK providers supported**: MiMo, DeepSeek, OpenAI, OpenRouter, Qwen
  (Alibaba). Users can set a default provider plus a failover chain.
- **MCP (Model Context Protocol) support** — the same open standard used by
  Claude Desktop and Cursor. Users connect databases, GitHub, web search,
  browser automation, and hundreds of other external tools, and the agent
  can use them directly. (Pro subscription feature.)
- **Live Preview** — render a local HTML file in a panel right beside the
  code, using Cortex's own embedded engine (no external browser). Reloads
  automatically on save, and the agent itself can inspect the rendered
  output and the JS console to test the pages it builds.
- **1M+ token context windows** on supported models.
- **Session persistence** — full context kept across restarts, crash-safe.

**Current version:** 2.8.0 (shipped July 2026). Headline features of this
release: MCP Servers, Live Preview, a refreshed brand/logo, and a long list
of performance fixes (large chats and Settings now open instantly instead
of freezing).

**Live stats shown on the homepage** (only use these if you need a stat —
don't inflate them): 4.8/5 developer rating · 12K+ downloads · 5 BYOK
providers.

**Pricing:**
- Pro: **$10/month**, or **$80/year** ($6.67/month, "save $40/year").
- The product is free to download; the subscription unlocks Pro features
  (MCP servers is one of the Pro-gated features).

**Platform requirement:** Windows 10/11 (64-bit / x64) only, right now.

**Website:** cortex-ide.app

---

## 2. Brand identity

- **Logo:** a hexagon outline with a bold, geometric "C" inside it, gold
  gradient (dark gold → bright light-yellow), on a black/very-dark
  background. This is the ONLY current logo — do not generate the old
  purple/cyan "brain" logo, it has been fully retired from the product.
- **Brand colors:**
  - Primary: **gold / warm yellow** gradient — approx `#8a5d08` (dark) →
    `#f5c518` (mid) → `#ffe873` (bright highlight).
  - Background: near-black / very dark neutral gray, e.g. `#1e1e1e` –
    `#0f0f0e`. NOT navy blue, NOT warm brown/coffee — those were tried and
    explicitly rejected in favor of a neutral dark + gold accent look.
  - Text on dark: off-white / light gray (`#d4d4d4`–`#ededea`).
- **Visual tone:** clean, professional, minimal — VS Code / Cursor-style
  dark IDE aesthetic. Not cartoonish, not neon-cyberpunk, not cluttered.
  Confident and technical, not "startup gradient blob" style.
- **Typography feel:** modern sans-serif (Segoe UI family in the actual
  app), bold weight for headings.

---

## 3. Audience & tone of voice

- **Audience:** software developers, especially those already comfortable
  with AI coding tools (Cursor, GitHub Copilot, Claude Code, Windsurf) who
  want that agentic experience natively on Windows, and who care about
  owning their own API keys/costs instead of paying a markup.
- **Tone:** confident, technical, no fluff. Speaks like an engineer to
  other engineers. Avoid generic AI-hype language ("revolutionize",
  "game-changing", "unleash the power of AI"). Prefer concrete, specific
  claims (e.g. "24+ agent tools", "1M+ context", "no token markups") over
  vague superlatives.
- **Things to NEVER claim:** don't invent user counts, ratings, or feature
  claims beyond what's listed in Section 1. Don't claim it works on
  macOS/Linux (Windows-only right now). Don't claim it's free forever (Pro
  features are a paid subscription).

---

## 4. Content pillars for daily posts (rotate through these)

1. **Feature spotlight** — one feature per post (MCP servers, Live Preview,
   BYOK security, agent tools, session persistence, multi-provider support).
2. **Developer pain point → Cortex solution** — e.g. "tired of paying
   token markups on every AI coding tool? Bring your own key instead."
3. **Behind-the-build / changelog** — short, punchy summary of what shipped
   recently (new logo, performance fixes, MCP support).
4. **Security/trust angle** — BYOK, local key storage, DPAPI encryption,
   zero proxy — a genuinely differentiated selling point for privacy-minded
   devs.
5. **"Show, don't tell"** — a short clip/screenshot idea of the agent
   actually doing something (editing a file, running a test, live-previewing
   a page it just built).
6. **Comparison / positioning** — Windows-native vs. browser-based/Electron
   AI tools (factual, not disparaging competitors by name unless you want
   to; safer to describe the advantage, not name-call).
7. **Community/social proof** — download milestones, rating, "what
   developers say" style posts (use only the real 4.8/5 and 12K+ stats —
   don't fabricate testimonials).

---

## 5. Platform notes

- **X / Twitter:** short, punchy, 1–2 sentences + a visual. Threads work
  well for "here's what's new in 2.8.0" style posts.
- **Instagram:** needs a strong square or 4:5 visual first — the logo, a
  clean UI screenshot, or a dark-mode code/IDE aesthetic shot. Caption can
  be a bit longer than X but still tight.
- **Facebook:** slightly more explanatory copy is fine; can reuse the X
  copy with 1–2 extra sentences of context for a less technical audience.

---

## 6. Seven image-generation prompts (ready to use / adapt)

All prompts assume the brand system in Section 2: dark neutral background,
gold hexagon-C logo, clean professional IDE aesthetic, no purple/cyan, no
cartoon style. Each is written to be dropped straight into an image model.

1. **Logo hero shot** — "A minimalist product logo on a pure black
   background: a hexagon outline with a bold geometric letter C inside it,
   rendered in a rich gold-to-light-yellow gradient (dark amber at the
   edges, bright pale gold highlight through the center), subtle soft glow
   around the hexagon, sharp clean vector edges, centered composition,
   ultra minimal, no other text or elements, square format."

2. **Dark IDE screenshot mockup** — "A realistic mockup of a dark-themed
   code editor application on a Windows desktop, VS Code–style layout: left
   file explorer sidebar, center code editor with syntax-highlighted Python
   code, right-hand AI chat panel showing a conversation. Neutral dark gray
   theme (#1e1e1e background), clean sans-serif UI font, a small gold
   hexagon logo in the top-left corner of the title bar. Professional,
   crisp, high detail, no clutter, 16:9 widescreen."

3. **"Agent at work" concept shot** — "A stylized illustration of an AI
   coding agent autonomously editing code: a glowing gold hexagon icon
   connected by thin light lines to floating code file icons and a terminal
   window, all set against a deep charcoal-black background, minimal sci-fi
   but professional (not cartoonish), gold and white accent lighting only,
   clean geometric shapes, square format for Instagram."

4. **BYOK / security visual** — "A clean technical illustration representing
   secure local API key storage: a padlock icon in gold gradient, next to a
   small laptop/desktop computer silhouette, with a subtle circuit-line
   pattern in the dark background, conveying 'your keys never leave your
   machine.' Dark neutral background (#141414), gold and off-white color
   palette only, flat professional vector style, no other colors."

5. **Feature-callout card (MCP Servers)** — "A minimal social-media graphic
   with a dark charcoal background and a central gold hexagon-C logo at the
   top, below it a simple icon grid of 4-6 small connected node icons
   (representing external tools/servers linking together), clean geometric
   line-art style in gold and white, plenty of negative space for text to
   be added later, square 1:1 format."

6. **Live Preview feature visual** — "A split-screen illustration: left half
   shows abstract lines of code in a dark editor pane, right half shows a
   simple browser/preview window rendering a webpage, connected by a subtle
   gold arrow or glow between them, dark near-black background throughout,
   minimal and clean, gold and white accents only, no other colors, 4:5
   portrait format for Instagram."

7. **Changelog / "what's new" banner** — "A wide banner graphic, deep black
   background with a subtle radial gold glow in one corner, the gold
   hexagon-C logo positioned at the left, large empty central area reserved
   for a version number and headline text to be added afterward, thin gold
   accent line near the bottom edge, extremely clean and minimal, no
   clutter, 16:9 format suitable for a Twitter/X header or blog banner."

---

## 7. What to ask for in the next session

When you paste this file into a new chat, a good follow-up prompt is:

> "Using the brief above, write me 7 days of social media posts (X,
> Instagram, Facebook) — one post per day, rotating through the content
> pillars in Section 4. For each day give: the post copy for each platform,
> and which of the 7 image prompts (or a variation of one) to pair it with."

That keeps content and image generation consistent across a full week
without you having to re-explain the product each time.

# Third-Party Attribution — Bundled Skills

Cortex ships 251 skills. Two are original Cortex work (`bug-triage`,
`distinctive-frontend-design`); nine more are Cortex rewrites derived from
the collections below; the remainder are included from their upstream
sources with their SKILL.md and reference files intact.

All are OFF by default. The user toggles on the ones they want in
Settings → Skills, and the agent can load any of them on demand via the
Skill tool. Demo videos, screenshots, and other binary assets from the
upstream repositories are omitted — only text the agent can actually read
is bundled.

## Sources

- **agent-skills** — Copyright (c) 2025 Addy Osmani (MIT License).
  24 skills. Nine were substantially rewritten for Cortex's toolset
  (Edit/Write staging, write verification, Cortex log paths, pytest
  layout): debugging-and-error-recovery, incremental-implementation,
  planning-and-task-breakdown, test-driven-development,
  code-review-and-quality, spec-driven-development, context-engineering,
  plus the SKILL.md phase-routing approach. The rest — including
  security-and-hardening, performance-optimization, frontend-ui-engineering,
  api-and-interface-design, git-workflow-and-versioning, ci-cd-and-automation,
  observability-and-instrumentation, code-simplification — are included as
  published.

- **skills** — Copyright (c) 2026 Matt Pocock (MIT License). 28 skills,
  included as published. Its diagnosing-bugs / triage methodology was also
  folded into Cortex's own bug-triage and debugging-and-error-recovery.
  The same set ships a second time inside the upstream `Application-develop`
  reference app; only one copy is bundled.

- **Skills** — Copyright (c) 2026 Meng To (MIT License,
  github.com/MengTo/Skills). 113 skills covering website building, motion,
  WebGL/three.js, layout and visual systems. SKILL.md and REFERENCES.md
  included intact; demo media omitted.

- **skills (Anthropic)** — Anthropic's published skill collection. 17 skills
  including frontend-design, skill-creator, theme-factory, mcp-builder,
  webapp-testing, canvas-design and the document toolkits. Included as
  published. Note that the document skills (docx, pptx, xlsx, pdf) and
  webapp-testing expect Python libraries or Playwright that Cortex does not
  bundle; their guidance is still useful, but the tooling they reference
  must be installed separately.

- **taste-skill** (MIT License). 13 skills. `design-taste-frontend` and
  `image-to-code` are split (see below). The image-generation skills
  (imagegen-frontend-web, imagegen-frontend-mobile, brandkit) are included
  but require an image-generation model Cortex does not provide.

- **3d-website-skill** — Copyright (c) 2026 Devesh Punjabi (MIT License,
  github.com/deveshpunjabi/3d-website-skill). The SKILL.md is
  adapted/condensed for Cortex (Live Preview self-check, Edit/Write, Cortex
  reference-path handling); the three reference files under references/ are
  included verbatim.

- **impeccable** — Apache License 2.0. SKILL.md adapted for Cortex (Live
  Preview iteration, CLI marked optional, Node toolchain not bundled); all
  reference method docs included verbatim under references/ per Apache-2.0.
  Upstream ships this skill 15 times, once per editor; one copy is bundled.

- **frontend-design** — provided by the project owner; adapted for Cortex as
  `distinctive-frontend-design` with the original guidance substantially
  preserved.

- **claude-seo** — Copyright (c) 2026 agricidaniel (MIT License,
  github.com/AgriciDaniel/claude-seo). 31 skills covering search, AI-search
  visibility and technical SEO: `seo`, `seo-audit`, `seo-technical`,
  `seo-content`, `seo-content-brief`, `seo-cluster`, `seo-backlinks`,
  `seo-schema`, `seo-sitemap`, `seo-hreflang`, `seo-images`, `seo-local`,
  `seo-maps`, `seo-ecommerce`, `seo-programmatic`, `seo-sxo`, `seo-drift`,
  `seo-geo`, `seo-google`, `seo-page`, `seo-plan`, `seo-flow`,
  `seo-competitor-pages`, `seo-image-gen`, and the data-provider extensions
  `seo-ahrefs`, `seo-bing`, `seo-dataforseo`, `seo-firecrawl`,
  `seo-profound`, `seo-seranking`, `seo-unlighthouse`. SKILL.md and
  reference files included as published; screenshots and binaries omitted.
  Upstream ships `seo-dataforseo` and `seo-image-gen` twice (a main skill
  and an extension stub) — the fuller copy is bundled.

  Modification: the upstream `seo` skill contained a "Community Footer"
  section instructing the agent to append an advertisement for the author's
  paid community (skool.com links) to every major deliverable. That section
  is removed — Cortex output belongs to the user, not to a skill author's
  marketing. Authorship attribution is preserved here and in the skill's
  frontmatter.

  Note: the extension skills call third-party APIs (Ahrefs, DataForSEO,
  Firecrawl, SE Ranking, Profound, Bing Webmaster) that need the user's own
  credentials, and `seo-unlighthouse` expects the Unlighthouse CLI. Cortex
  bundles neither keys nor tooling.

- **SEO-GEO-AEO-Skill** — bundled as `seo-geo-aeo`. Covers search engine,
  generative engine (Perplexity, ChatGPT Search, AI Overviews) and answer
  engine optimisation. SKILL.md included as published.

## Split skills

A SKILL.md over 28k characters is split so it does not dominate the context
of any conversation that touches it: the original text is preserved verbatim
at `<skill>/references/full-guide.md`, and the SKILL.md beside it is a short
router that points there. The agent reads the guide at the step that needs
it. Applies to: `design-taste-frontend` (87k), `claude-api` (69k),
`imagegen-frontend-mobile` (40k), `imagegen-frontend-web` (37k),
`image-to-code` (36k), `skill-creator` (33k).

Per the MIT and Apache-2.0 terms, the above copyright notices are preserved.
Cortex's own adaptations are distributed under Cortex's license terms
alongside this attribution.

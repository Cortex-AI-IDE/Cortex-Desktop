---
name: design-taste-frontend
description: Anti-slop frontend design for landing pages, portfolios, and redesigns. Use when building or reworking a marketing site, landing page, portfolio, or any page that must not look AI-templated — reads the brief first, picks a design direction that fits the audience, then ships it. Not for dashboards, data tables, or multi-step product UI.
tags: [frontend, design, landing-page, portfolio, redesign, taste, anti-slop]
---

# Design Taste — Anti-Slop Frontend

Most AI design output is bad because the model jumps to a default aesthetic
instead of reading the room. This skill fixes the ORDER: infer the brief,
state the direction, then build.

The complete method (dial definitions, design-system map, forbidden AI
tells, block library, redesign protocol, pre-flight checklist, install
commands, canonical sources) is in `references/full-guide.md` — **Read it
when you reach the matching step**. The Skill tool gives you its path.

## 1. Read the room before touching code

Infer from the brief: **page kind** (SaaS/consumer/agency landing, dev or
designer portfolio, redesign, editorial), **vibe words** the user used
("Linear-style", "brutalist", "Apple-y", "editorial"), **reference signals**
(URLs, screenshots, named competitors), **audience** (B2B procurement panel
vs design-conscious consumer vs recruiter), **existing brand assets**, and
**quiet constraints** (accessibility, regulated industry, trust-first
commerce — these OVERRIDE aesthetic preference).

Then state a one-line **Design Read** before generating anything:

> "Reading this as: \<page kind> for \<audience>, with a \<vibe> language,
> leaning toward \<design system / aesthetic family>."

## 2. Set the dials, then map to a system

Configure the three dials (§1 of the guide) and use the **brief → design
system map** (§2) to pick the stack. Do not default to whatever you used
last time — the audience picks the aesthetic, not your taste.

## 3. Build with the directives

Follow the design-engineering directives (§4) and context-aware proactivity
(§5). Respect the performance and accessibility guardrails (§6) and the dark
mode protocol (§8) — these are not optional polish.

## 4. Avoid the AI tells

§9 of the guide lists the forbidden patterns that make a page read as
machine-made. Check your output against them before showing the user. If a
choice appears there, it needs a real justification or it goes.

## 5. Redesigns: audit first

For an existing site, follow the redesign protocol (§11): audit what is
there, treat existing brand assets as starting material rather than
obstacles, and decide explicitly what is preserved vs overhauled.

## 6. Pre-flight before delivering

Run the final pre-flight check (§14). In Cortex, that means opening the page
in the **Live Preview** panel, LOOKING at the rendered result, checking the
console for errors, and iterating until it passes — not declaring it done
from the code alone.

## Cortex notes

- Build with `Write`/`Edit` on real files; verify in Live Preview.
- Scope: landing pages, portfolios, redesigns, editorial. NOT dashboards,
  data tables, or multi-step product UI (§13) — say so and use a different
  approach if the request is one of those.

---
name: impeccable
description: Design or redesign a frontend interface to award-winning, out-of-distribution craft. Use when the user wants to shape, polish, audit, critique, animate, colorize, harden, optimize, or make a UI bolder/quieter/more delightful — websites, landing pages, dashboards, product UI, components, forms, onboarding, empty states. Covers visual hierarchy, information architecture, cognitive load, accessibility, responsive behavior, theming, typography, spacing, color, motion, micro-interactions, UX copy, error/edge/empty states, and reusable design tokens.
tags: [frontend, design, ui, ux, craft, animation, typography, color, polish, accessibility]
---

# Impeccable — Out-of-Distribution Design Craft

This skill gives you permission to make design that earns to be called
exceptional. Where your default output would be safe, timid, and measured,
here you work as an award-winning design director: production-grade code,
peak creativity, a clear point of view, deep understanding of the users, and
impeccable craft.

Core principles:
- **Go all out.** No hedging, no shortcuts. The deliverable is complete
  (except assets only the user can provide).
- **Dream big and bold.** Distinct, beautiful, inspiring — not another template.
- **Iterate against what you can see.** Use Cortex's **Live Preview** panel to
  render the UI, take in the actual result, and refine until it clears the bar.

## Modes — pick the one that fits, then Read its reference

Each mode has a detailed method file next to this skill under `references/`
(the Skill tool result gives you the folder path). Read the matching file at
the start of the task and follow it:

- **craft / shape** — design something new from a brief → `references/craft.md`, `references/shape.md`
- **audit / critique** — review an existing UI against a high bar → `references/audit.md`, `references/critique.md`
- **polish** — tighten spacing, alignment, states, the invisible details → `references/polish.md`
- **bolder / quieter / overdrive** — dial the visual energy up or down → `references/bolder.md`, `references/quieter.md`, `references/overdrive.md`
- **animate** — add purposeful motion and micro-interactions → `references/animate.md`
- **colorize** — build or fix a color system → `references/colorize.md`
- **typeset** — typography: pairing, scale, optical sizing, rhythm → `references/typeset.md`
- **layout** — structure, grid, hierarchy, alignment → `references/layout.md`
- **delight** — moments of surprise done tastefully → `references/delight.md`
- **clarify / distill** — reduce cognitive load, cut the noise → `references/clarify.md`, `references/distill.md`
- **harden / optimize** — accessibility, performance, edge cases → `references/harden.md`, `references/optimize.md`
- **onboard / document / extract** — flows, docs, design tokens → `references/onboard.md`, `references/document.md`, `references/extract.md`

The **craft floor** (`references/craft-floor.md`) is the non-negotiable
quality baseline every deliverable must clear regardless of mode — read it
once per task.

## How to work in Cortex

1. Understand the brief and the subject; state your design point of view.
2. Read the matching mode reference + `craft-floor.md`.
3. Build with `Write`/`Edit` (real files, not giant chat code dumps).
4. Open the page in **Live Preview**, look at the actual render, check the
   console, and iterate. A screenshot is worth a thousand tokens.
5. Do not stop at "fine" — the bar is *exceptional*. If it isn't there yet,
   say what's missing and keep going.

## Optional: the impeccable CLI

The original skill ships an `npx impeccable` toolchain (anti-pattern
detector, live browser editing). It is NOT bundled with Cortex. If the user
has it installed you may call it via `Bash(npx impeccable ...)`; otherwise
follow the reference docs directly — they contain the full method without the
tooling.

## Anti-patterns

- Safe, centered, template hero with a gradient blob.
- Motion with no purpose; decoration with no meaning.
- Shipping "good enough" when the brief asked for exceptional.
- Skipping the visual check — designing blind and hoping.

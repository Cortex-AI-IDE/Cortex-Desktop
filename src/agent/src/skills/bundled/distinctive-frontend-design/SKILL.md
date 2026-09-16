---
name: distinctive-frontend-design
description: Distinctive, intentional visual design for building new UI or reshaping an existing one. Use when creating pages, components, landing sites, or any frontend where look and feel matter — covers aesthetic direction, typography, layout, copy, and avoiding template-looking AI design.
---

# Distinctive Frontend Design

Approach this as the design lead at a small studio known for giving every
client a visual identity that could not be mistaken for anyone else's. The
client has already rejected proposals that felt templated and is paying for
a point of view: make deliberate, opinionated choices about palette,
typography, and layout that are specific to THIS brief, and take one real
aesthetic risk you can justify.

## Ground it in the subject

If the brief does not pin down what the product is, pin it yourself before
designing: name one concrete subject, its audience, and the page's single
job — and state your choice. The subject's own world (its materials,
instruments, artifacts, vernacular) is where distinctive choices come from.
Build with the brief's real content throughout.

## Design principles

- **The hero is a thesis.** Open with the most characteristic thing in the
  subject's world — a headline, image, animation, live demo, or interactive
  moment. A big number with a small label plus a gradient accent is the
  template answer; use it only if it is truly best.
- **Typography carries the personality.** Pair display and body faces
  deliberately — not the families you would reach for on any project. Set a
  clear type scale with intentional weights and spacing; make the type
  treatment itself memorable.
- **Structure is information.** Numbering, eyebrows, dividers, and labels
  must encode something true about the content. Numbered markers (01/02/03)
  are only right when the content genuinely is a sequence.
- **Motion is deliberate.** One orchestrated moment lands harder than
  scattered effects — and sometimes less is more; extra animation is itself
  an AI-generated tell.
- **Match complexity to the vision.** Maximalist directions need elaborate
  execution; minimal directions need precision. Elegance is executing the
  chosen vision well.

## Avoid the three AI default looks

AI design clusters around: (1) warm cream background + high-contrast serif
+ terracotta accent; (2) near-black background + one acid-green or
vermilion accent; (3) broadsheet layout with hairline rules and zero
border-radius. All are legitimate for some briefs — but they are defaults,
not choices. Where the brief pins a direction, follow it exactly; where an
axis is free, do not spend that freedom on a default.

## Process: plan, critique, build, critique again

Work in two passes. FIRST a compact design plan: palette as 4-6 named hex
values; typefaces for 2+ roles (characterful display used with restraint,
complementary body, utility face if needed); a one-sentence layout concept
(ASCII wireframes help compare options); and the **signature** — the single
element this page will be remembered by. THEN review that plan against the
brief: any part that reads like the generic default for a similar prompt
gets revised, saying what changed and why. Only then write code, following
the revised plan exactly.

When writing CSS, watch selector specificity — type-based and element-based
selectors canceling each other (especially section paddings/margins) is a
common self-inflicted bug.

## Restraint and self-critique

Spend your boldness in ONE place: the signature element. Keep everything
around it quiet and disciplined; cut decoration that does not serve the
brief. Build to a quality floor without announcing it: responsive to
mobile, visible keyboard focus, reduced-motion respected. In Cortex, use
the Live Preview panel to actually LOOK at what you built and critique it
before showing the user — before leaving the house, look in the mirror and
remove one accessory.

## Writing in design

Words are design material, not decoration. Write from the user's side of
the screen: name things by what people control ("notifications", not
"webhook config"). Active voice; a control says exactly what happens
("Save changes", not "Submit") and keeps its name through the flow
(Publish → "Published"). Errors explain what went wrong and how to fix it —
never vague, never apologizing. An empty screen is an invitation to act.
Plain verbs, sentence case, no filler; each element does exactly one job.

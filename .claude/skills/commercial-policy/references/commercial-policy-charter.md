# Commercial Policy Charter — Annotated

Reference companion to the charter template in `SKILL.md`. For each section: typical contentious issues, common pushback from sales / customers, alternative formulations seen in practice, and the trade-offs of each.

---

## Why a charter (and not just rules)

Rules without a charter:
- Feel arbitrary to sales reps
- Lack mandate when challenged
- Drift as exceptions accumulate
- Provide no audit-trail of organizational intent

A charter:
- Documents *why* each rule exists (so sales can explain to customers)
- Names accountable owners
- Provides governance cadence for updates
- Becomes auditable evidence (for SOC 2, board oversight, regulator inquiries)

A charter is not a contract appendix or a legal document — it's an *operational* policy.

---

## Section-by-section annotations

### Purpose section

**Common pitfall:** generic platitudes ("we value our customers"). Useless.

**Better:** specific intent. Example:

> "This policy enables sales to close deals predictably without ad-hoc executive intervention while protecting commercial, legal, and financial risk within company tolerance. Compliance is mandatory; exceptions require named approver sign-off."

The word "mandatory" is the key. Policy that's "suggested" gets ignored.

### Scope section

**Common pitfall:** unclear boundaries. "All deals" — including self-serve? Renewals? Trial extensions?

**Better:** explicit in-scope / out-of-scope lists. The out-of-scope list is as important as the in-scope.

### Owners and Approvers

**Common pitfall:** single-owner policy (just CRO). Lacks legal / financial perspective.

**Better:** triumvirate (CRO + CFO + GC). Each represents a critical concern:
- CRO: sales effectiveness, deal velocity
- CFO: financial impact, revenue recognition, audit
- GC: legal exposure, regulatory compliance, contract enforceability

Add explicit operational owner (Deal Desk Lead) for day-to-day.

### Pricing Policy

**Standard pricing**: easy to write, hard to enforce. Concession drift erodes "standard" over years.

**Discount approval matrix**: this is the actionable heart of the pricing policy. Should mirror deal-desk approval matrix exactly. If they diverge, policy gets bypassed.

**Maximum discount**:
- Easy formulation: "Standard maximum 50%"
- Harder: defining what triggers Board awareness (60%? 70%? Specific dollar amount?)
- Hardest: enforcement (does anyone actually escalate to Board?)

**MFN**:
- Most-favored-nation clauses are the slowest-burning commercial-policy time bomb
- Customer gets locked-in MFN; you can't price-discriminate going forward
- "Granted only with strategic-tier customer + CRO + CFO + GC approval" — make it hard
- Always scope: same product, same volume, same term, same geo
- Disclosure-only (we tell you if we beat your price for equivalent customer; you decide)
- Excludes pilot pricing, acquisition pricing, beta program pricing

**Rebates**:
- Performance-based rebates: standard for partner programs; good incentive alignment
- Customer-tier rebates (volume-based): use sparingly; can become reverse-discount
- Time-bound and explicit: rebate "for the year" — not perpetual

### Contract Policy

**Standard term**:
- 12-month is most common
- Some companies push to multi-year as standard (sticky revenue)
- Pros of multi-year standard: predictable revenue, less renewal churn
- Cons: harder for customer to try-and-leave; potential churn after multi-year forced commitment

**Payment terms**:
- Net 30 is most common standard
- Enterprise customer norms (Net 60, Net 90) push back
- Allow Net 60 with manager approval, Net 90+ with CFO approval
- Revenue recognition: payment terms can affect ASC 606 / IFRS 15 treatment for multi-year deals; CFO involvement essential

**Renewal**:
- Auto-renew is industry-standard
- Customer-friendly renewal: 90-day pre-renewal notice + option to terminate
- Aggressive renewal: 60-day notice with auto-escalation (causes friction)
- Renewal expansion / contraction triggers deal-desk review (not auto-rubber-stamp)

**Termination**:
- Termination for convenience: 90 days is industry norm
- Termination for cause: 30-day cure period typical
- "Customer-favorable termination" (no-fault termination with 30-day notice): some enterprise customers demand; resist; expensive in lost revenue predictability

### Legal Policy

**MSA modifications**:
- Most contentious area
- "Pre-approved modifications" list is the unlock: maintain a list of MSA modifications already approved (e.g., "liability cap 2x annual fees if customer is regulated financial institution")
- Custom modifications: GC review required; expensive in legal hours
- Customer-supplied MSA: hard push back to own MSA; saves enormous review time

**Liability cap**:
- Industry norm: 1x annual fees (or 2x for enterprise)
- Customer requests: often 5x annual or "uncapped"
- Carve-outs: IP infringement, gross negligence, willful misconduct — always uncapped
- Tip: bundle increased liability cap with longer commitment / higher ACV (trade for trade)

**Indemnification**:
- Mutual indemnification is standard
- Customer-favorable indemnification (one-way): GC approval
- Defense / settlement control: usually vendor controls (cheaper)

**Jurisdiction**:
- Vendor's jurisdiction by default
- Customer's jurisdiction sometimes required (especially for government, regulated)
- GC must approve

### Operational Policy

**SLA tiers**:
- Standard published SLA
- Enhanced SLA at premium pricing
- Custom SLA: engineering + customer success approval; price premium
- Custom SLA with penalties (credits for downtime): operational risk; requires careful approval

**Custom security**:
- CISO approval mandatory
- Customer-supplied questionnaires: time-intensive; standardize responses to the top 80% questions
- Avoid one-off custom commitments that you can't audit / can't deliver

**Dedicated infrastructure**:
- Only available with explicit pricing premium
- Operational cost real (separate environment)
- Most customers don't actually need it (often a procurement checkbox)

### Customer Commitments

**Reference / case study**:
- Standard: ask but don't require
- Discounted deals (> 15%): required as concession trade
- Strategic logos: explicit case study + press release + speaking commitment
- Track follow-through: many promised case studies never materialize

### Channel Policy

Largely defers to Partner Agreement + channel-specific economics. Policy reinforces:
- Discounts per published tier
- Deal-registration governs conflict
- Direct rep authority same as direct on partner-led opportunities

### Special Terms

**Performance-based payment / acceptance criteria**:
- Customer pays on acceptance of deliverables
- Risk: customer doesn't accept; you've delivered; no payment
- CFO + GC approval mandatory
- Revenue recognition affected (typically deferred until acceptance)

**Ramp deals**:
- Customer pays growing fee over time as usage scales
- Useful for genuine ramps; abused as discount-in-disguise
- Director or VP approval; document why ramp (vs flat discount)

**Source code escrow** (customer):
- Customer wants source code held in escrow in case you go bankrupt
- Standard ask from regulated / mission-critical customers
- CTO + GC approval; neutral escrow agent
- Release triggers: bankruptcy, material breach of support, sustained outage

---

## Annual policy review process

### Inputs

- Deal data (CRM export, last 12 months)
- Deal-desk metrics (approval rate, SLA performance, decision distribution)
- Sales feedback (qualitative + NPS)
- Customer feedback (lost-deal analysis, win-loss interviews)
- Market intelligence (competitor moves, customer expectations)
- Financial impact (gross margin trend, discount trend, ASP trend)

### Process

1. **Data review** by policy committee (4-6 weeks before review meeting)
2. **Sales survey** (quick survey, ~10 questions, 3 weeks before)
3. **Stakeholder interviews** (Sales VPs, Eng VP, CISO, Finance, GC; 2 weeks before)
4. **Draft amendments** (1 week before)
5. **Review meeting** (90 min): present data, debate amendments, approve
6. **Communicate** to sales (workshop + recorded session)
7. **Update charter** with effective date

### Common amendments

| Driver | Amendment |
|--------|-----------|
| Discount creep (avg discount up 5% over 12 months) | Tighten discount thresholds; train sales on alternatives to discount |
| Competitive landscape shift | Adjust competitive-deal policy |
| New regulation (GDPR, EU AI Act, SOC 2 v3) | Add new required commitments to standard MSA |
| New product line | Add product-specific pricing and terms |
| Geographic expansion | Add region-specific overlay |
| Material customer churn pattern | Adjust termination / renewal policy |

---

## Sales training on policy

Policy doesn't drive behavior; training does.

### Standard training elements

- **Charter overview** (30 min): purpose, scope, owners
- **Discount and approval matrix** (45 min): what reps can offer, when to escalate
- **Standard vs non-standard terms** (30 min): how to recognize non-standard requests
- **Common customer asks and responses** (45 min): scripted responses to discount, MFN, payment terms, custom legal
- **Deal desk workflow** (15 min): how to submit, what to expect
- **Q&A** (15 min)

### Quick-reference card (per-rep takeaway)

- Discount authority by rep level
- Standard terms (length, payment, SLA, termination)
- When to escalate (specific triggers)
- Top 5 "non-standard" requests and standard responses
- Deal desk SLA + contact

### Refresh training

- After every material policy change
- New-rep onboarding within 30 days
- Quarterly "policy spotlight" (one section deep-dive)

---

## Customer-facing communication

Some commercial-policy elements are visible to customers; some aren't.

### Customer-visible

- Standard pricing (published)
- Standard SLA (published)
- Standard contract terms (in published MSA)
- Renewal terms (in customer agreement)
- Termination terms (in customer agreement)

### Internal-only

- Discount authority by rep level
- Specific approval thresholds
- "When to escalate to CFO" logic
- Strategic / non-precedent language reasoning

When a customer asks "what's your policy on X?" — sales should be trained to articulate the customer-visible portion without revealing internal authority thresholds.

---

## Cheat sheet

| Question | Answer |
|----------|--------|
| Do we need a charter? | If ARR > $5M and > 10 sales reps, yes |
| Who owns the policy? | CRO + CFO + GC jointly |
| How often to review? | Annually formally; quarterly informally |
| What if sales doesn't follow it? | Compliance audit + corrective action + training; don't tolerate ad-hoc deviation |
| What if customer demands violation? | Escalate per matrix; CRO/CFO/CEO decision; document |
| What if competitor offers what we won't? | Sometimes we lose the deal. That's the right outcome if our policy is sound. |
| How to know policy is working? | Discount trend stable / declining; deal velocity improving; sales NPS positive on deal-desk experience |

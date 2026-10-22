# Contract and Commercial Guardrails

Reference for MSA modifications, liability caps, indemnification, termination rights, IP and joint-development clauses, customer audit rights, cross-jurisdictional considerations. The legal-flavored elements of commercial policy.

---

## The pre-approved-modifications list

Most companies negotiate the same MSA modifications repeatedly. Maintain a list of pre-approved modifications: GC has already approved these; Deal Desk can grant them without re-review.

### Standard pre-approved MSA modifications

| Modification | Pre-approved if | Approver |
|--------------|-----------------|----------|
| Liability cap 2x annual fees | Customer is regulated (financial, healthcare) | Deal Desk |
| Customer-favorable indemnification (carve-outs for customer's data) | Standard scope | Deal Desk |
| 60-day pre-renewal notice (vs 90) | Customer requests | Deal Desk |
| Customer jurisdiction (instead of vendor) | EU customer, EU jurisdiction; APAC customer, Singapore | Deal Desk |
| Source code escrow | OEM relationship | VP Engineering + GC |
| Custom security questionnaire (top-100 standardized responses) | Standard | Security ops |
| Data residency in customer's region | Region in which we operate | VP Engineering |
| Termination for change-of-control (within 30-90 days of acquisition by competitor) | Customer requests | GC |
| Audit rights (annual, 3rd-party auditor, confidentiality) | Customer requests | GC |
| GDPR data processing addendum (DPA) | EU customer | Standard for EU; Deal Desk |

By maintaining this list, you save legal hours and accelerate deal close.

---

## Liability cap policy

### Standard

- **Standard cap**: 1x annual fees paid in the most recent 12 months
- **Carve-outs (always uncapped)**: IP infringement indemnification, gross negligence, willful misconduct, confidentiality breach
- **Increased cap** (2x, 3x): per pre-approved list or with CFO + GC approval

### Negotiation

| Customer ask | Standard response |
|--------------|-------------------|
| 5x annual fees | Counter with 2x; if hard ask, escalate to GC + CFO |
| Uncapped | Decline (only with strategic alliance / OEM context); always keep IP carve-out |
| Direct damages only | Standard; mutual |
| Consequential / indirect damages excluded | Standard; mutual |
| Customer-favorable cap (vendor uncapped, customer capped) | Almost never; one-sided indemnification |

### Trade-offs

Increased liability cap should trade for:
- Higher ACV / multi-year commitment
- Specific scope (limited to single product / use case)
- Insurance coverage by customer

---

## Indemnification

### Standard mutual indemnification

Each party indemnifies the other for:
- IP infringement by indemnifier's product / use
- Breach of confidentiality
- Gross negligence / willful misconduct
- Violation of law by indemnifying party

### Customer-supplied indemnification (one-way)

Customer demands vendor indemnify customer (with no reciprocal commitment).

**Resistance**: standard mutual is appropriate. If customer insists, scope must be specifically limited (IP only) and capped (per liability cap).

### Defense and settlement control

Standard: indemnifying party (defender) controls defense and settlement, with cooperation from indemnified party.

Customer-favorable: indemnified party can choose to control its own defense.

GC review required for any deviation from standard.

---

## Termination rights

### Termination for convenience

Either party can terminate without cause with notice. Standard: 90 days.

**Variations:**

| Notice period | When |
|---------------|------|
| 30 days | Customer-favorable; resist |
| 60 days | Some customers; acceptable with manager approval |
| 90 days | Standard |
| 180+ days | Vendor-favorable; rarely customer-acceptable |

For OEM / strategic partnerships: longer notice (12-24 months) reflects depth of relationship.

### Termination for cause

Either party can terminate immediately for material breach. Standard 30-day cure period.

**Cure period exceptions:**
- Bankruptcy / insolvency: no cure period
- Privacy breach: no cure period (often)
- Repeated breach within X months: no cure period

### Customer-favorable termination clauses

**No-fault termination with 30-day notice**: customer can leave any time.

- **Why customer wants**: flexibility
- **Why vendor resists**: undermines revenue predictability; encourages "try and leave" behavior
- **When acceptable**: pilot / POC; explicitly time-bound

**Termination on change-of-control**: customer can terminate if vendor is acquired (especially by competitor).

- **Standard**: 30-90 day window after announcement
- **Why customer wants**: protection against acquisition by competitor
- **Why vendor accepts**: usually acceptable; provides exit path
- **Negotiation**: define "competitor" narrowly to limit trigger

### Post-termination

| Element | Standard |
|---------|----------|
| Customer access to data | Customer can export for 30-90 days |
| Vendor obligation to delete | Within 60-90 days after termination |
| Confidentiality survives | 3-5 years |
| Indemnification survives | For claims arising during contract |
| Payment of in-flight obligations | Customer pays for service used up to termination |

---

## IP and joint-development clauses

### Pre-existing IP

Standard: each party retains all rights to IP existing before the agreement.

Documentation: list of background IP at contract signing (especially for OEM / strategic partnerships).

### Improvements to one party's product

Standard: each party retains rights to improvements to their own product.

### Joint inventions

Joint invention = something developed by both parties together.

| Pattern | When |
|---------|------|
| Each party gets full license to use; ownership joint | Most common |
| Lead developer owns; other has license | When one party did 80% of the work |
| New entity owns | Joint venture context |

### Trademark license

License to use partner logos for marketing, integration callouts, etc.

Standard: limited license, subject to brand guidelines, revocable on termination.

### Customer data and feedback

Standard: customer owns customer data; vendor has limited license to use for service delivery.

Customer feedback / suggestions: vendor can use freely (without further compensation or attribution).

Some customers push for ownership of feedback ("you can't use ideas we share with you"). Resist; usually accept reciprocal "we won't use your suggestions in our product roadmap without acknowledgment" instead.

---

## Customer audit rights

Standard: vendor's books and records related to the customer relationship are auditable.

**Scope**:
- Customer can audit annually (or less often)
- 3rd-party auditor (mutually agreed)
- Confidentiality protections (auditor signs NDA)
- Audit at requestor's cost (unless discrepancy > 5%, then auditor cost shifts to audited party)
- Limited to records relevant to customer's deal

**What's NOT auditable**:
- Other customers' data
- Vendor's general financial records
- Vendor's product development / internal information

**Vendor pushback**:
- Audit can be disruptive (vendor team time)
- Audit can expose confidential information
- Mitigation: scope tightly, use neutral auditor

GC approval required for any non-standard audit rights.

---

## Cross-jurisdictional terms

### Governing law

Standard: vendor's jurisdiction.

| Customer | Common request |
|----------|----------------|
| US enterprise | Delaware (regardless of customer / vendor HQ) |
| EU customer | Their EU jurisdiction; standard for GDPR-covered transactions |
| UK customer | England & Wales |
| APAC customer | Singapore or Hong Kong common for regional |

**Vendor preference**: own jurisdiction (familiar, cheaper).
**Customer preference**: own jurisdiction.
**Common compromise**: neutral jurisdiction (Delaware, England); both familiar enough.

### Dispute resolution

| Approach | When |
|----------|------|
| Court litigation | Standard for US-domestic |
| Arbitration (AAA, ICC, JAMS) | International deals; faster; private |
| Mediation first, then arbitration | Cost-effective; cooperative dispute resolution |
| Mediation first, then court | Standard for some industries |

### Data residency

For EU customers: GDPR data-residency requirements.
For Russian customers: data-localization laws.
For Chinese customers: specific data-handling requirements.
For US public-sector: FedRAMP, ITAR, EAR considerations.

GC + CISO review required for any non-standard data residency commitment.

### Tax and VAT

| Region | Consideration |
|--------|---------------|
| US | State sales tax (Wayfair Decision impacts SaaS) |
| EU | VAT (typically reverse-charged for B2B) |
| UK | VAT separate from EU post-Brexit |
| APAC | Country-specific GST / VAT |
| LATAM | Country-specific; some require local invoicing |

Standard: customer responsible for their tax obligations. CFO + GC for unusual structures.

---

## Pre-approval list as governance

The pre-approved modifications list is your most powerful governance tool:

### Benefits

- **Speed**: deals close faster (no per-deal GC review)
- **Consistency**: same modifications, same terms
- **Audit trail**: clear policy decisions

### Maintenance

- Reviewed quarterly by GC
- Additions require approval (same as standard policy update)
- Removed if outdated or causing problems

### Documentation

For each modification, document:
- The standard language
- Conditions for use (customer profile, deal characteristics)
- Approver
- Why this modification was approved

---

## Common contract clauses worth resisting

### Customer-favorable termination

"Customer may terminate at any time with 30-day notice."

**Why resist**: undermines revenue predictability; encourages low commitment.

**Compromise**: 30-day termination during pilot period only; standard 90-day for production.

### Most-favored-nation (MFN)

Per the MFN section in discount-and-terms-policy.md. Generally resist; scope tightly if granted.

### Customer audit of vendor financial records

"Customer may audit vendor's books and records."

**Why resist**: exposes confidential information.

**Compromise**: scope to customer-deal-specific records only; via mutually-agreed 3rd-party auditor.

### Custom legal language with no precedent

"We want this clause."

**Why resist**: GC time + future precedent risk.

**Compromise**: use pre-approved alternatives; if truly novel, full GC review.

### Liability uncapped except [carve-outs]

**Why resist**: liability cap is industry-standard for a reason; uncapped exposes vendor to existential risk.

**Compromise**: 2x or 3x liability cap with appropriate trade.

### Customer-favorable indemnification

"Vendor indemnifies customer for any claim arising from customer's use."

**Why resist**: shifts customer's own risk to vendor.

**Compromise**: standard mutual indemnification; scope to vendor's actions.

### "We need this signed by Friday"

**Why resist**: rushed legal review is bad legal review.

**Compromise**: if truly urgent, escalate to GC for fast-track review (paid time vs free for normal); document urgency for future precedent.

---

## Cheat sheet

| Question | Answer |
|----------|--------|
| When is MSA modification okay? | Per pre-approved list; otherwise GC review |
| Standard liability cap? | 1x annual fees with carve-outs |
| When increase liability cap? | Per pre-approved list (regulated industry); otherwise CFO + GC |
| Termination for convenience notice? | 90 days standard; 60 days with approval |
| Customer-favorable termination (no-fault)? | Resist; only for pilot / proof-of-concept |
| Customer audit rights? | Standard mutual: customer-data-relevant records, annual, 3rd-party auditor |
| Cross-jurisdictional terms? | GC + CISO involvement; consider data residency, tax, dispute resolution |
| Customer pre-supplied MSA? | Push back to own MSA; otherwise full GC review |
| What to maintain quarterly? | Pre-approved modifications list; periodic GC review |
| When to engage CEO on terms? | Massive deals (>$5M); precedent-setting; substantial liability exposure |

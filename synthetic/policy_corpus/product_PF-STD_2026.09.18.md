---
product: PF-STD
version: 2026.09.18
effective_from: '2026-09-01'
doc: product_PF-STD
---

# Personal Financing — Standard (PF-STD)

This sheet describes Personal Financing — Standard as it stands in version 2026.09.18, effective 2026-09-01. Every clause id below is the id the decision engine evaluates, so a decision that cites a clause can be checked against the rule that ran.

## Terms

| Term | Value |
|---|---|
| min amount | 1,000 LCU |
| max amount | 150,000 LCU |
| min tenor | 6 |
| max tenor | 84 |
| profit rate | 6.500% |
| instalment formula | amount * (1 + profit_rate * tenor / 12) / tenor |
| min share units | 100 |
| purposes allowed | PERSONAL, EDUCATION, MEDICAL, HOME_IMPROVEMENT, DEBT_CONSOLIDATION, VEHICLE, OTHER |

## Eligibility

Whether the cooperative may lend to this member at all. A failure here is not a judgement about the member's creditworthiness; it means the product is not available to them on these terms.

### ELG-01

*Eligibility: membership inactive.*

**Condition.** The case satisfies this clause when `member.status == 'ACTIVE'`.

**If it does not.** The application cannot proceed on these terms, under reason code `ELG-01`.

**Inputs.** The clause is evaluated from `member.status`. Every one of them carries evidence back to the record it came from.

### ELG-02

*Eligibility: tenure below minimum.*

**Condition.** The case satisfies this clause when `member.tenure_months >= 6`.

**If it does not.** The application cannot proceed on these terms, under reason code `ELG-02`.

**Inputs.** The clause is evaluated from `member.tenure_months`. Every one of them carries evidence back to the record it came from.

### ELG-03

*Eligibility: identity not verified.*

**Condition.** The case satisfies this clause when `identity.verified == true`.

**If it does not.** The case leaves the normal path and goes to a person, under reason code `ELG-03`.

**Inputs.** The clause is evaluated from `identity.verified`. Every one of them carries evidence back to the record it came from.

### ELG-04

*Eligibility: age or eligibility rule not met.*

**Condition.** The case satisfies this clause when `member.age >= 18 and member.age + requested.tenor / 12 <= 65`.

**If it does not.** The application cannot proceed on these terms, under reason code `ELG-04`.

**Inputs.** The clause is evaluated from `member.age`, `requested.tenor`. Every one of them carries evidence back to the record it came from.

### ELG-05

*Eligibility: product not available to member class.*

**Condition.** The case satisfies this clause when `requested.amount >= product.min_amount and requested.amount <= product.max_amount and requested.tenor >= product.min_tenor and requested.tenor <= product.max_tenor`.

**If it does not.** The application cannot proceed on these terms, under reason code `ELG-05`.

**Inputs.** The clause is evaluated from `requested.amount`, `requested.tenor`. Every one of them carries evidence back to the record it came from.


## Documents

What the file must contain before a decision can be made. A missing or unreadable document is a request for information, never an accusation.

### DOC-01

*Documents: required document missing.*

**Condition.** The case satisfies this clause when `documents.required_complete == true`.

**If it does not.** The case is referred for review, under reason code `DOC-01`.

**Inputs.** The clause is evaluated from `documents.required_complete`. Every one of them carries evidence back to the record it came from.

### DOC-04

*Documents: low-confidence critical field.*

**Condition.** The case satisfies this clause when `documents.min_critical_confidence >= 0.85`.

**If it does not.** The case is referred for review, under reason code `DOC-04`.

**Inputs.** The clause is evaluated from `documents.min_critical_confidence`. Every one of them carries evidence back to the record it came from.


## Affordability

Whether the member can carry the repayment. Measured from verified income and existing commitments, and stressed to see what happens if either moves.

### AFF-01

*Affordability: commitment ratio above limit.*

**Condition.** The case satisfies this clause when `affordability.dsr <= affordability.dsr_limit`.

**If it does not.** The case is referred for review, under reason code `CAP-02`.

**Inputs.** The clause is evaluated from `affordability.dsr`. Every one of them carries evidence back to the record it came from.

### AFF-02

*Affordability: thin headroom under stress.*

**Condition.** The case satisfies this clause when `all(affordability.stress, dsr <= affordability.dsr_limit + 0.05)`.

**If it does not.** The case is referred for review, under reason code `CAP-03`.

**Inputs.** The clause is evaluated from `affordability.stress`. Every one of them carries evidence back to the record it came from.

### AFF-03

*Affordability: commitment ratio above limit.*

**Condition.** The case satisfies this clause when `income.verified_monthly - commitments.monthly - proposed.instalment >= affordability.residual_income_min`.

**If it does not.** The case is referred for review, under reason code `CAP-02`.

**Inputs.** The clause is evaluated from `income.verified_monthly`, `commitments.monthly`, `proposed.instalment`. Every one of them carries evidence back to the record it came from.

### AFF-04

*Affordability: income not verified.*

**Condition.** The case satisfies this clause when `income.verified == true`.

**If it does not.** The case is referred for review, under reason code `CAP-04`.

**Inputs.** The clause is evaluated from `income.verified`. Every one of them carries evidence back to the record it came from.


## Exposure

How much the cooperative may have outstanding to one member or one employer at a time.

### EXP-01

*Exposure: exposure above limit.*

**Condition.** The case satisfies this clause when `member.total_exposure + requested.amount <= exposure.limit`.

**If it does not.** The case is referred for review, under reason code `EXP-02`.

**Inputs.** The clause is evaluated from `member.total_exposure`, `requested.amount`, `member.grade`. Every one of them carries evidence back to the record it came from.


## Routing

Where a case goes once it has been assessed. Routing is a decision about who decides, not about the outcome.

### RT-01

**Condition.** This clause applies when `fraud.level == 'HIGH'`.

**Effect.** The case is routed to `COMPLIANCE_REVIEW`.

### RT-02

**Condition.** This clause applies when `fraud.level == 'CRITICAL'`.

**Effect.** The case is routed to `COMPLIANCE_REVIEW`.

**Additionally.** The case leaves the normal path and goes to a person.

### RT-03

**Condition.** This clause applies when `identity.mismatch == 'CRITICAL'`.

**Effect.** The case is routed to `ENHANCED_ASSESSMENT`.

### RT-04

**Condition.** This clause applies when `history.arrears_12m >= 2`.

**Effect.** The case is routed to `ENHANCED_ASSESSMENT`.

### RT-05

**Condition.** This clause applies when `requested.amount > actor.max_amount`.

**Effect.** The case is routed to `ESCALATE`.

# Ask Tom site: fixes and additions (for ChatGPT)

Site reviewed: agere-inside-intelligence.michael-p-loga331119.chatgpt.site (2026-10-18). Keep the design, the
Data → Intelligence → Knowledge → Wisdom story and the heart-failure journey. Change the items below so every claim
matches the product.

## 1. Replace numbers that are not from the product

| On the site | Replace with | Source |
|---|---|---|
| "1,614 synthetic patients" | 4,095 synthetic patients in the pilot workspace | Agere pilot workspace (native customer-format feeds) |
| "1,238,686 received records" | 1,034,606 records from 107,239 delivered files | same workspace: canonical records and delivered files |
| "12 eGFR series", "34 renal trajectories", "5 hemoglobin patterns" | Pull live from the product demo, or label "illustrative" | not traceable to a product run today |

The 1,614 figure belongs to a different product (the Physician Builders roster). Do not reuse it here.

## 2. Make "Explore the demo" open the live product

The question explorer is static. Point "Explore the demo" and "Ask Tom" to the hosted product app (being restyled in
this site's design system now). Until the hosted URL is public, keep the static explorer but label it "example".

## 3. Journey steps: align wording with what runs

- 02 Find: "Find patients with heart failure, and reduced ejection fraction where it is documented (echo reports and
  notes)." Echo reports arrive with the next data release; today ejection fraction is read from clinical notes.
- 03 Review: correct. Add "reasons a drug is held (intolerance, potassium, kidney function, cost) are read from notes and
  documents, so the plan respects them".
- 04 Match and 05 Enroll: correct. Add realism: "agents handle declines, coverage and prior-auth denials with appeals,
  and no-shows; people see only the exceptions".
- 06 Titrate and 07 Monitor: being added as agent pathways now; keep as written.
- 08 Live: add "Agere checks weeks later that the fill or the visit actually happened, and reopens the gap if not".

## 4. Add what makes the product different (missing today)

1. Test before you fund it. Every program can be simulated on the organization's own population before launch:
   admissions and ED visits avoided, net savings and shared-savings impact, with ranges, compared with doing nothing
   or with an alternative program.
2. CMS ACCESS economics built in. Outcome-aligned payments are modeled from CMS's published amounts (MSK about $180 per
   patient per year, full payment when at least half of patients meet targets, no impact on ACO benchmarks in 2026-27).
3. The cheapest correct path, every time. Common questions are answered by compiled, verified logic in seconds at no
   model cost; a frontier model is used only for what genuinely needs it. On the locked test set, 4 of 5 repeat answers
   came back in 1-4 seconds at $0.
4. Ask for anything. "Build a negotiation simulator for our MA contract" or "a board story of our ACCESS MSK launch":
   the frontier model designs it, and every number comes from the verified stack.
5. Reads the record the way a clinician does. Free-text notes, faxes and PDFs are read with small classifiers (local
   models and typed deciders), so finding "why isn't this patient on an MRA" costs fractions of a cent, not a large-model
   call per note.
6. Proven on questions with independently agreed answers. Nine real population-health questions are locked with an
   independent party; the product answers them identically on repeat runs. A language model answering alone got 1 of
   the first 5.

## 5. Competitive section

Keep the five comparisons. Add a sixth line to each Ask Tom column: "Reads native claims, enrollment, pharmacy, HL7 and
FHIR files directly, and acts on the answer through agents." That is the gap no listed competitor closes end to end.

## 6. Physician value / care rewards

Keep "illustrative amounts". Add: "Rewards are tied to completed, verified care steps recorded by the agents (a fill, a
visit, a closed gap), not to clicks."

## 7. Name

The site says "Ask Tom"; the platform underneath is Agere. Suggest a small footer line: "Ask Tom, built on Agere".

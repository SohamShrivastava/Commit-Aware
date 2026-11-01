# CLAUDE.md — Writing rules for this paper

This file governs any AI-assisted writing for the `paper/**` folder . Read it before editing anything under `paper/`.

The target is the ICSE 2027 Industry Challenge track, Solution Paper category. Reviewers include engineers from the company that proposed the challenge. They read for deployability and honest numbers, not for prose.

---

## Hard rules

**Never invent a citation.** Every reference must correspond to a paper you have verified exists, with the correct authors, title, venue, and year. If you believe a citation is needed but cannot verify it, write `\Fix{citation needed: <what claim>}` and move on. A fabricated reference found by a reviewer discredits the whole submission.

**Never invent a number.** Every figure in the text, tables, and abstract must trace to `progress.md` or to the experiment code. If a number is needed and does not exist yet, write `\Fix{need <description>}`. Do not estimate, round from memory, or carry a number forward from an earlier draft without rechecking it.

**Never overstate a guarantee.** The conformal machinery gives a bound on expected risk, averaged across builds, under assumptions that CI data does not fully satisfy. Write what holds. Specifically:

- Do not write "guarantees" where "bounds in expectation" is accurate.
- Do not write "mathematically guarantees ≥95% fault detection" while the finite-sample correction term is missing from the implementation.
- State that the guarantee is marginal, not per-build.
- State that exchangeability is violated and cite the work that bounds the degradation.

**Keep the submission anonymous.** No author names, no institutions, no identifying repository URLs, no acknowledgements. Refer to our own prior work in the third person if it comes up.

**When sources disagree, flag rather than choose.** If `progress.md` and the code report different values, write both and mark `\Fix{}`. Do not silently pick one.

---

## Comment macros

- `\Fix{...}` — something is wrong, missing, or unverified. Anyone on the team can resolve it.
- `\Den{...}` — a note addressed to Denini. It prints a name, so none may remain at submission.

---

## Style

Write like a software engineering researcher, not like a model.

**Avoid these patterns, which read as machine-written:**

- Opening a sentence with "Moreover", "Furthermore", "Additionally", "Notably", "Importantly".
- The "It's not just X, it's Y" construction, and its variants.
- Tricolons for rhythm: "faster, cheaper, and more reliable" where two adjectives would do.
- Em-dash pairs used as a default connector. Use a comma, a semicolon, or two sentences.
- Paragraphs that open by announcing what the paragraph will do.
- Closing a section with a sentence that restates the section.
- Hedging stacks: "may potentially be able to suggest".
- Inflated adjectives: "comprehensive", "robust", "novel", "significant", "cutting-edge", "state-of-the-art". If the work is novel, the reader will see it from the result.
- Bulleted lists where prose carries the argument better. Lists are for enumerable things: subjects, baselines, research questions.
- Avoid use the "Its not X is Y" and "something: text"

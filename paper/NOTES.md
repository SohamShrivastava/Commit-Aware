# Paper notes: open questions, unverified numbers, claims to confirm

The `N` numbers match the `\Fix{Nxx}` markers in the LaTeX.

**How far verification goes.** `TCP-CI-dataset-v2/` and all result JSONs are absent from the repo (`.gitignore`), so no result was recomputed. "Consistent" below means `progress.md`, the code and arithmetic agree; it does not mean the number was reproduced. Everything in the results tables needs a student to confirm it against the result files.

**Deadline:** solution papers are due Nov 13, 2026 (CFP). Submit at https://icse2027-industry-challenge.hotcrp.com/.

## Decisions for Denini

- **D1. Framing of the key result.** The draft keeps the ablation as the climax, but changes two claims. It says 17 failures, not 18 (N6), and it does not say telemetry-based approaches cannot catch them, because the TCP-CI ranker does (N7). The argument becomes the price per extra failure, plus the fact that the semantic layer needs no failure history. See RQ4 and the `\Den{}` in the Discussion.
- **D2. Tool name.** Open, to be decided together. `macros.tex:339` defines `\tool` as `FLAG`, used as a placeholder. Marked with a `\Fix{}` in the Introduction; changing the macro updates the whole paper.
- **D3. Generative AI disclosure.** Decided by Denini: no acknowledgement of AI in the paper. `CLAUDE.md`'s no-acknowledgements rule stands.
- **D4. Source of truth.** Decided by Denini: what is in this repository is the truth. Facts that came only from the initial prompt were removed from the paper. Where the repository disagrees with itself, the paper flags it.
- **D5. Scope for the 12 days.** The re-run list below is ordered by what most threatens the paper. Items 1 to 3 change claims; the rest refine them.

## Re-run list, in priority order

1. Re-run the drift monitor and the LLM judge at current λ̂, and report reduction under the monitor (N9, N10, N11).
2. Fix or quantify the index leakage (N14).
3. Sweep Hybrid and the ranker over thresholds and budgets on one recall-versus-reduction figure, and list which of the 18 each catches (N32, N40).
4. Measure real test durations and end-to-end decision latency (N19, N21).
5. Measure per-build risk on Slice 3 and commit the failure-conditional calibration code (N16, N17).
6. Surface two more commits (N29).
7. Add more projects (N35).
8. Compute confidence intervals and change recall (N31).

## progress.md, README and code disagree

- **N1. Finite-sample term.** `src/conformal/calibration.py:142-180` implements it and `progress.md:85-89` reports it. `CLAUDE.md:18` still describes the term as missing, so that line is out of date, though its ban on overstating the bound still applies. The rule `(W·R̂ + B)/(W+1) ≤ α` has the same form as Theorem 1, Eq. 10 of Farinhas et al. (ICLR 2024), not the plain `B/(n+1)` of Angelopoulos et al. Their bound adds a total-variation term that cannot be computed, so the paper makes no formal claim. `progress.md:89` ("mathematically rigorous guarantee") and `README.md:7,48` overstate it. Two more items to check: the theorem assumes data-independent weights in [0,1] (ours depend only on timestamps, which looks fine, but please confirm), and our loss runs in the opposite direction in λ. Also, `rho_selection.py:116` still uses the uncorrected rule.
- **N2. Semantic-only recall (resolved).** The paper uses 59.41% at λ̂=0.5272 (`progress.md:182`). History shows 51.05% at λ̂=0.5345 before `33b78f2` (`git show 99b5b75:agent.md` line 203); it is not used.
- **N3. Log4j2 threshold.** `src/evaluation/metrics.py:384-386` and `src/online_selection/llm_batch_selector.py:436-437` hard-code 0.5345, described as an "empirical code-embedding median", because `calibration.py:186-199` returns a 0.35 floor when there are no failures. No code computing that median is in the repo. The same value is ShardingSphere's old λ̂ in `99b5b75:agent.md:203`, so please check the comment. The table labels it `λ=0.5345`, and "Balanced" says 0.53.
- **N4. Run All metrics.** The paper uses `progress.md:178,195`: APFD 0.5000, Recall@10% 10.00%, Recall@20% 20.00%. These are not measured: `metrics.py:242-248,255-259` sets them for any method named "Run All". The real suite order would give a different APFD, so the row should be measured or described as fixed reference values.
- **N5. Calibrated quantity.** `calibration.py:159-173` computes per-build miss fraction, weighted over builds, with loss 0 on passing builds. The paper follows the code.
- **N6. 18 versus 17.** `README.md:141` says the hybrid "catches all 18" (the 18 are from `progress.md:236`). Fragile tests score `1+sim`, so they always clear λ (`metrics.py:443`), and the hybrid is a superset of the guard. 238−221 = 17. One zero-history failure is missed by the hybrid.
- **N7. Telemetry claim.** `progress.md:242` says Baseline C skipped the case-study tests, yet it misses at most 1 of 239 (`progress.md:180`), so it caught at least 3 of those 4. Yaraghi et al. list change and coverage features among the 150, so the ranker is not telemetry-only. `README.md:14` has the same problem.
- **N8. Log4j2 "TCP-CI ML".** Slice 1 has 0 failures (`progress.md:65`), so `baselines.py:256-261` skips training and `baselines.py:304-309` returns tied scores. `metrics.py:195,201-202` then takes the first 50% in suite order. `progress.md:214,221` claims based on this row (APFD 0.7196 vs 0.6195, "memorization") are invalid. The paper's table relabels the row "Suite order".
- **N9. Drift path.** `progress.md:96` gives 0.5272→0.3345 in 4 steps. Steps of 0.05 (`drift_monitor.py:36,127`) give 0.3272. The path 0.5345→0.4845→0.4345→0.3845→0.3345 is at `99b5b75:agent.md:186`. The drift result predates the correction.
- **N10. LLM row looks stale.** `progress.md:262` says reduction rose "from 34.70%" but Hybrid Balanced is 29.54% (`:184`). The old conformal row at λ=0.5345 had 34.73% (`99b5b75:agent.md:203`). Confirm the λ and δ used (`llm_batch_selector.py:36` defaults δ=0.02; `progress.md` never states δ). Also, `run_pipeline.py:254` defaults to dry-run (N34).
- **N43. Identical ranking metrics.** Hybrid and Full have the same APFD and Recall@Budget on both projects (`progress.md:184-186, 201-203`), though `llm_batch_selector.py:358-369` scores tests differently. Possible if failures rank above the band; please check the row was not copied.

## Results that need work

- **N11. Monitor reduction.** Not reported. The threshold ends near 0.33, and `progress.md:118` says similarities rarely fall below 0.375, giving 0% reduction. Recovery may therefore mean running everything. The monitor also never raises the threshold again (`drift_monitor.py:125-127`).
- **N12. 0.48%.** It is the mean of rolling per-build miss fractions, diluted by passing builds (34 of 420 fail). `progress.md:96` ">99.5% fault recall" does not follow. Report pooled recall.
- **N13. Monitor uses unavailable labels.** `drift_monitor.py:107` counts failures of skipped tests. A deployment has no verdict for those. Needs a variant fed only by scheduled full runs. `drift_monitor.py:257-259` is also a no-op placeholder, and `alarm_count=3` is not in `progress.md`.
- **N14. Index leakage.** `extract_unique_tests.py:64` overwrites to the last build's commit, so summaries reflect each test's latest version, which postdates calibration and evaluation. Regenerate point-in-time summaries, or measure the effect on a sample. Incremental refresh is neither implemented nor costed, and `cost_analysis.py:238-243` assumes full re-indexing twice a year.
- **N15. Uncalibrated operating points.** `metrics.py:456` hard-codes 0.62 and 0.58, with no record of how they were picked. If chosen on Slice 3, High-Savings results are leaked. The hybrid and the judge have no calibrated risk at all. λ̂ was fitted with the guard off; calibrating on failures the guard misses may be the principled design.
- **N16. Recall gap.** The paper now states the explanation in `progress.md:230-233`: passing builds dilute the calibrated risk, and failure-conditional calibration (λ=0.4656) gives 94.98%, 227/239, reduction 4.71%. **No code for that run exists in the repo** (`0.4656` appears only in `progress.md:231`), so commit it. 94.98% does not "mathematically satisfy" a 95% target (`:231`), and the paper does not repeat that wording. `progress.md:94` also blames distribution shift; how much each factor contributes is unmeasured, and a rolling-origin evaluation would help.
- **N17. Per-build risk on Slice 3.** Needed for RQ1. My rough estimate, assuming uniform miss rates across failing builds: 34/420 × 0.4059 ≈ 3.3%. If the measured value is below 0.05, the bound held while recall was 59.41%, and that becomes a central statement of the paper.
- **N28. Case study.** Checked on GitHub: `c47772c3`, "Shutdown ExecutorService in SchemaBuilder (#10509)", 2021-05-27, one file, one added line. Students confirm build 772599166, the four tests, their histories and scores ≥0.61 (`progress.md:238-243`). `progress.md:239` calls this "updated schema builder metadata logic", which understates it. `progress.md:243`'s mechanism is untested. At λ=0.62, a score of 0.61 would fall below the threshold, so does this rescue survive High-Savings?
- **N29. More commits.** Only one is surfaced. Need two more from the 18, plus the one that is still missed. Log4j2's two failures are candidates (guard catches 0 of 2).
- **N30. Failure labels.** The guard runs about 248 of 354,301 executions and catches 221, so failures repeat heavily. `progress.md:124,221` mention flaky tests. No flakiness labels exist. "True failures" and "regressions" in `progress.md:174,236` are not established. Facebook's labels needed retries (Machalica et al.), which we cannot do offline; rerunning at least the 18 at their commits may be feasible.
- **N31. Statistics.** No confidence intervals or tests anywhere. APFD 0.9795 vs 0.9771 has no test behind it (`progress.md:213`). Add bootstrap intervals over failing builds, change recall per method, and repeat the judge for nondeterminism. `metrics.py:63` returns 1.0 when a build has no failures, but only failing builds are aggregated.
- **N32. Ranker comparison.** `progress.md:225-226` compares our +$45.07 at 91.77% with the ranker's $24.53 at 50%, which are different budgets, so "nearly doubling" is misleading. `progress.md:180` has Recall@10% = 94.75% and `:225` has 92.89%, probably mean-per-build versus pooled. Which failures does the ranker catch? "Extra 17" in the table is a net count and may not be the same failures. The cost of collecting 150 features is priced at zero.
- **N33. Fallbacks.** The challenge PDF names scheduled full-suite runs and mandatory human review. Neither is implemented. Decide whether to simulate full runs every k builds in time, which also addresses N13.
- **N35. Projects.** The repo evaluates two (`src/config.py:21-30`); `project_plan.md:22` planned 4–6. Findings rest mainly on ShardingSphere.
- **N40. The 18.** Need the list, the builds, and the number of distinct commits. Four come from one build, so independent evidence may be thin.

## Cost numbers

- **N18. Net savings arithmetic** (`progress.md:159-165, 186, 203-205, 262-263`).

  | Item               | Stated            | Recomputed              |
  | ------------------ | ----------------- | ----------------------- |
  | SS batch, $0.10    | +20.23            | 20.89−0.08−0.66 = 20.15 |
  | SS live, $0.10     | +7.69             | 7.61                    |
  | SS live, $0.50     | +91.25            | 91.17                   |
  | SS batch, $0.50    | +104.15           | 103.71                  |
  | Log4j2 live, $0.50 | +23.75 and +23.73 | 23.71                   |

  Most of the table's other rows are consistent with reduction × 354,301 or 107,451 executions, within rounding.

- **N19. 5 s per test.** Assumed in `metrics.py:39` and `cost_analysis.py:63`, never passed a measured value. The benchmark has durations (`baselines.py:210` excludes them as a feature). All dollars, latency and CO2e depend on this. `progress.md:143` calls it "empirical" without a source.
- **N20. LLM rates.** Live $1.50/M and batch $0.075/M prompt tokens (`progress.md:157,161`), with neither verified against a price list. `llm_batch_selector.py:390-392` also has $0.30/M for output, but 13.03M × rate equals the reported totals exactly, so output tokens are excluded. Tokens per call (4,800) and 2,000 per diff (`metrics.py:38`) are estimates. The paper leads with the live rate, which `progress.md:162` presents as actual billing (₹1,730); confirm.
- **N21. Decision latency.** The only figure is ~30 ms for scoring (`progress.md:80`). This is critical for sub-challenge 3. `llm_batch_selector.py:385` records `elapsed_sec`, so use it, and measure embedding latency too. Report median and p95.
- **N22. Inference energy.** The challenge asks about ecological cost. No code computes inference energy. `cost_analysis.py:161-212` counts only saved machine time, so CO2e is gross.
- **N23. Scaling assumptions.** CO2e uses a fixed ×2.5 (`cost_analysis.py:191`). The 5,000 builds a year (`:397`), 10 builds a day (`:94`), the ×1.2 queue multiplier (`:135`, not in the reported min/build), and the $75/h developer rate are all assumptions. Latency assumes serial execution, so 64.5 min/build implies a roughly 70-minute serial suite; check against real CI times. PUE and carbon constants need citations. I'd drop 3-year ROI.
- **N24. Missed failures.** No cost in the model, so skipping everything looks best. The per-failure price in RQ4 is my way around this; a team can compare it to its own cost of an escape.
- **N25. Indexing cost.** ₹743.89 ≈ $8.90 (`progress.md:49`). Per-project USD 5.90 and 3.03 are derived with `cost_analysis.py:44`'s 0.012. `progress.md:162`'s ₹1,730 = $19.55 implies 0.0113, a different rate. The paper reports USD only because a billing currency can hint at location. `progress.md:51` says cost scaled 2.03×; actual is 1.95× (491.30/252.59), against 2.03× tests.
- **N26. Batching.** `progress.md:169` lists batching 5–10 tests as a future optimization, but `llm_batch_selector.py:37` already batches up to 35, and `config.py:39`'s `GEMINI_BATCH_SIZE = 1` concerns summarization. Confirm what the reported runs used, since "2,696 calls" depends on it.
- **N27. Run All cost.** Charged −$0.08 and −$0.04 for an embedding it never makes. `metrics.py:143` charges every method, including the path heuristic and the ranker.

## Implementation and reproducibility

- **N34. Pipeline.** `experiments/run_pipeline.py:108-110` calls `run_split(project_key=…)`, but `split_dataset.py:163` does not accept it, so a fresh run fails. `:102` mentions a `--force-slicing` flag that does not exist. `:254` defaults the judge to dry-run, which selects every band test and overwrites `llm_batch_selector_results.json`. Evaluation (step 2) also runs before calibration (step 4). `progress.md:285` presents this as the reproduction path. `paper_figures.py` needs result JSONs that are not in the repo.
- **N36. Truncation and defaults.** Test source 6,000 chars (`llm_test_summarizer.py:105`), embedding diff 15,000 (`semantic_selector.py:155`), judge diff 6,000 (`llm_batch_selector.py:38`), summary 300 (`:192`), judge cache key uses only the first 1,000 chars of diff (`:180`). Need the fraction affected. Tests missing from the index score 0.5 in calibration (`calibration.py:287`) but 0.65 at evaluation (`semantic_selector.py:325`). Empty diffs give zero vectors, so everything is skipped (`:250-251`), while batch precompute uses "Empty commit diff" (`:212`).
- **N37. Model identity.** `config.py:34` says `gemini-3.6-flash`, `llm_batch_selector.py:9` says 3.5, and `progress.md` mixes "Gemini Flash" and 3.6. Git commit `e21b227` says 3.5 for summaries. The paper avoids naming the LLM until this is settled. Summaries and judge must name exact versions.
- **N38. Benchmark features.** `progress.md:36` says 152, `:77` says 150; `baselines.py:9` says Random Forest, but the code is HistGradientBoosting. Yaraghi et al. state 150 across nine groups, name features `F_*`, with Recent meaning the latest six builds. Names like `REC_LastVerdict` come from the dataset, not the paper. Students: confirm the column semantics and that they exclude the current build, since the paper implies this without saying so. If `dataset.csv` includes the current verdict, the guard and ranker results are leaked.
- **N39. ρ selection.** ρ is per day (`calibration.py:121,124`); `progress.md:84` omits the units. The Log4j2 sweep is degenerate since all folds return 0.35 (`rho_selection.py:88-89`). Kish's effective sample size needs a citation. Only ShardingSphere's 0.995 is meaningful. `W=301.32` and the derived ceiling are arithmetically consistent.
- **N42. No dependency-based baseline.** `project_plan.md:15,72` planned a coverage baseline. Reviewers will ask about Ekstazi or STARTS.
- **N44. Data availability.** Needs an anonymized archive. `README.md:94` has a student's GitHub URL, and git history names authors. Open-science text suggests Zenodo or figshare. That policy page does not explicitly cover this track; I included the section anyway.
- **N45. `macros.tex` change.** I commented out `\usepackage{subcaption}` (lines 11, 118). It loads `caption`, which warned about the unknown class and replaced IEEE caption formatting. If subfigures are needed, use `subfig` with `caption=false`. The file is otherwise untouched, and its `\lstset` still defaults to Go, overridden locally for Listing 1.

## Citations

- **N41.** Status of `references.bib`:
  - **Checked against a fetched source:** Angelopoulos (ICLR proceedings page), Barber (AoS, DOI), Farinhas (ICLR text), Tibshirani (NeurIPS), Yaraghi (arXiv journal-ref), Liu (arXiv and search result, DOI), Ekstazi (DOI), Legunsen 2016 (DOI), STARTS (author's page, pp. 949-954), Machalica (ICSE 2019 program), Memon (DOI), Leong (ICSE program), Najafi (paper PDF and ICSE 2019 program).
  - **Details left out on purpose:** page numbers for Machalica, Najafi and Ekstazi; article number for Liu.
  - **Leong:** author order follows the Google Research page and the search result (Micco third). The full text was unreachable, so check it against the PDF.
  - **Bates:** cited as arXiv:2101.02703, because I could not confirm the JACM details. Check the ACM record and upgrade.
  - **Yaraghi year:** the arXiv journal-ref says 2023; the challenge PDF prints 2022. I used 2023.
  - **Read in full for calibration:** Machalica (no threats section, inline limits, no RQs) and Najafi (headings only, has "Challenges and Lessons Learned" and a threats section). Leong: abstract only.
  - **Still needed:** any prior work applying conformal methods to test selection; peer-reviewed LLM or embedding selection; the PROMISE 2025 paper on LLM-enhanced commit messages; the challenge's own refs [2]-[6]; sources for PUE and carbon constants; Kish.
  - **Not fetched:** I assume the `problem.pdf` from git history (`d1b7e8f`) matches the CFP's linked PDF.

## Derived values (traceable, not in `progress.md`)

| Value                          | Derivation                                       |
| ------------------------------ | ------------------------------------------------ |
| 142, 221, 238, 225, 237 of 239 | recall × 239 (`progress.md:182-186`)             |
| 222 of 239                     | 92.89% × 239 (`:225`)                            |
| 17 extra, 4 extra              | 238−221, 225−221                                 |
| about 248 executions           | 354,301 × (1 − 0.9993)                           |
| 597 builds                     | 420 + 177                                        |
| $34.64, $173.20                | 49.09−14.45, 245.77−72.57                        |
| $2.04, $10.19                  | those ÷ 17                                       |
| $1.01, $5.02                   | (49.09−45.07)/4, (245.77−225.71)/4               |
| $1.44, $7.22                   | (49.09−24.53)/17, (245.77−123.09)/17             |
| 70 points                      | 99.93 − 29.54, rounded                           |
| $6.44, $32.21                  | 354,301 × (0.4263−0.2954) × 5/3600 × rate        |
| 325 builds                     | 5.90 / (7.61/420), using the recomputed live net |
| USD 5.90, 3.03                 | (₹487.12+4.18), (₹250.16+2.43) × 0.012           |

## Before submission

- `\Fix`, `\Den` and `\SecNote` removed. `\Den` prints "Denini", which breaks anonymity. Set `\draftnotesfalse` in `main.tex` to preview.
- Page count: at most 10, plus at most 2 of references only.
- Run the HotCRP format checker (the CFP recommends it).
- HotCRP asks for title, abstract, research areas, the selected challenge and conflicts.
- At least one author must register and present at ICSE 2027.

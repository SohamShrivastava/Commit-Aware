# Commit-Aware LLM Test Selection — Full Project Plan

## 1. What we're building, in one paragraph

A system that, for every pull request, decides which tests actually need to run instead of running the whole suite. It does this cheaply by having an LLM read every test *once in advance* and write a summary of it; at PR time it uses fast/cheap checks first (does the change "look similar" to a test's summary, is the test reachable in the build graph, has it failed alongside similar changes before); only for the handful of genuinely unclear tests does it call the LLM live, in one batched request. On top of that, we use conformal risk control (with a weighted rolling window, per your professor's correction) to give a statistical bound on how many real bugs we might miss, and we track whether that bound is still holding up over time (drift).

## 2. The 10-week plan, phase by phase

| Weeks | Phase | What comes out of it |
|---|---|---|
| 1–2 | Build the dataset | A clean table: for each commit, which tests ran, which passed/failed, plus the actual diff text and test source code |
| 3–4 | Offline indexing + cheap signals | Every test has an LLM-written summary + embedding; cheap-signal scores computed for historical PRs |
| 5–6 | Hybrid selector + conformal calibration | The full PR-time pipeline runs end-to-end; first cost/miss-rate numbers |
| 7 | Drift experiment | Evidence that recalibration catches and corrects drift over time |
| 8 | Baselines | Run-everything, coverage-based, and TCP-CI's own ranker, for comparison |
| 9–10 | Writing | Paper draft, tables, figures, formatting, internal review |

## 3. Building the dataset — step by step
v
**Why this is its own multi-week phase:** the TCP-CI (Yaraghi et al.) benchmark gives you *numbers* about each build (which tests ran/failed, code complexity scores, dependency links) but **not the actual text** of the code changes or test files — and the LLM needs to *read* real text, not numbers. So you have to combine two sources.

1. **Pick 4–6 projects** from TCP-CI's 25 — favor ones with enough failed builds that conformal calibration has something to learn from, and that aren't enormous (to keep compute manageable).
2. **Download TCP-CI's data** for those projects — this gives you, per build: commit ID, which tests executed, pass/fail outcome, and the precomputed features (complexity, dependency graph, historical co-failure).
3. **Clone the actual GitHub repos** for those same projects, and check out the specific commits TCP-CI references.
4. **Extract the diff text** for each commit (what actually changed, line by line) and the **actual source code of every test file** at that point in time.
5. **Join everything into one table**: for each commit → the diff text, the list of tests that existed at that time, each test's source code, whether each test passed/failed, and TCP-CI's precomputed features. This joined table is what every later phase reads from.
6. **Split chronologically** into three slices per project: earliest slice (for picking the conformal decay weight ρ, per your professor's method — never touched again after), middle slice (calibration), latest slice (final evaluation/reporting). This chronological split has to be decided now, since mixing it up later invalidates the guarantee.

## 4. Project / code structure

Think of the code as five stages that match the five boxes in the diagram from earlier, plus a data layer underneath and an evaluation layer on top.

```
project-root/
│
├── data/
│   ├── raw/            → TCP-CI's downloaded feature tables, per project
│   ├── repos/           → cloned git repos, checked out at needed commits
│   └── processed/       → the final joined dataset (commit + diff + test text + labels)
│
├── src/
│   │
│   ├── data_pipeline/                 (Phase: Weeks 1–2)
│   │   ├── download_tcpci.py          — pulls TCP-CI's feature tables for chosen projects
│   │   ├── clone_repos.py             — clones repos, checks out each needed commit
│   │   ├── extract_diffs.py           — pulls the actual diff text for each commit
│   │   ├── extract_tests.py           — pulls the actual test file source code
│   │   └── build_dataset.py           — joins everything into one clean table + does the chronological 3-way split
│   │
│   ├── offline_indexing/              (Phase: Weeks 3–4, "Stage 1" of the pipeline)
│   │   ├── llm_test_summarizer.py     — calls the LLM once per test, gets back a plain description
│   │   ├── embed_tests.py             — turns each description into an embedding (a vector of numbers)
│   │   └── build_index.py             — stores all test embeddings + descriptions; supports incremental updates
│   │
│   ├── online_selection/              (Phase: Weeks 3–6, "Stages 2–3")
│   │   ├── cheap_signals.py           — computes embedding similarity, build-graph reachability, co-failure score for a given PR
│   │   ├── uncertainty_triage.py      — decides, per test, whether the cheap signals are confident enough or not
│   │   ├── llm_batch_selector.py      — for the uncertain tests only, makes one batched LLM call under a token cap
│   │   └── select_tests.py            — ties the above three together: given a commit, outputs the selected test set
│   │
│   ├── conformal/                     (Phase: Weeks 5–6, "Stage 4")
│   │   ├── rho_selection.py           — sweeps decay weight ρ on the earliest data slice only, freezes it
│   │   ├── calibration.py             — computes the weighted rolling-window threshold using the frozen ρ
│   │   └── evaluate_risk.py           — checks the achieved miss-rate against the target on later builds
│   │
│   ├── drift/                         (Phase: Week 7, "Stage 5" — core part only)
│   │   └── drift_monitor.py           — compares recent achieved risk vs. target; triggers threshold widening/recalibration
│   │
│   ├── evaluation/                    (Phase: Week 8)
│   │   ├── metrics.py                 — APFD, fault-recall at a given budget, precision/recall/F1
│   │   ├── cost_analysis.py           — tokens used, $ cost, machine-time saved, latency, CO2e estimate, break-even point
│   │   └── baselines.py               — run-everything, coverage-only, and TCP-CI's own ranker for comparison
│   │
│   └── utils/
│       ├── config.py                  — settings (which project, thresholds, ρ, token budget, etc.)
│       └── llm_client.py              — one shared wrapper for all LLM calls, with token counting built in
│
├── experiments/
│   ├── run_pipeline.py                — runs the entire pipeline end-to-end on one project
│   └── run_all_projects.py            — loops the above over your chosen 4–6 projects, collects results
│
├── notebooks/                         — exploration, plots, sanity checks (not part of the "real" pipeline)
├── paper/                             — the actual paper draft, figures, tables
└── README.md / requirements.txt
```

**Roughly 20 code files total**, organized into 6 folders that map directly onto the 5 pipeline stages plus data-prep and evaluation.

## 5. How the files connect (the flow)

1. `data_pipeline/*.py` runs first, once, to produce `data/processed/` — everything downstream reads from there.
2. `offline_indexing/*.py` runs next (also mostly one-time, "offline"), reading test source from `data/processed/` and writing the test index — this is what stage 1 of the diagram is.
3. `online_selection/*.py` is called once per commit/PR in your simulated history — it reads the test index plus the cheap signals for that commit, decides the uncertain subset, calls the LLM batch on those, and outputs "these are the selected tests."
4. `conformal/*.py` sits on top of `online_selection` — it decides *where to set the relevance threshold* that `select_tests.py` uses, based on the calibration data.
5. `drift/drift_monitor.py` runs periodically across the simulated timeline, watching whether `conformal/evaluate_risk.py`'s numbers are drifting, and re-triggers `conformal/calibration.py` when needed.
6. `evaluation/*.py` runs last, over the full simulated history, to produce the actual numbers and plots that go in the paper.
7. `experiments/run_pipeline.py` is the "main" script that calls all of the above in order for one project; `run_all_projects.py` just repeats that for each of your 4–6 chosen projects and aggregates results.

## 6. Tools/libraries you'll likely need

- **Git handling**: `GitPython` or plain `git` commands via subprocess, for cloning repos and pulling diffs.
- **Data wrangling**: `pandas` for building and joining the dataset tables.
- **Embeddings**: either a hosted embedding API or a local open-source sentence-embedding model — either works, local is cheaper for 4–6 projects' worth of tests.
- **LLM calls**: whichever provider you're using for the summarization + uncertain-case batch calls; wrap every call through one shared client so you can count tokens consistently.
- **Vector storage/search**: for a project this size, a plain in-memory array with cosine similarity (numpy) is enough — you likely don't need a full vector database.
- **Plotting**: `matplotlib` for the cost curves, APFD curves, and drift plots.

## 7. What "done" looks like by week 10

A working pipeline that, for each of your 4–6 chosen projects: builds the test index once, selects tests per historical PR using the hybrid cheap-signal + LLM-fallback approach, reports a conformal risk bound with the weighted-window ρ fixed via the 3-way split, shows a drift-detection-and-recalibration result, compares against run-everything/coverage/TCP-CI-ranker baselines on APFD and fault-recall, and reports token/dollar/latency/CO2e cost — all written up as a paper draft.

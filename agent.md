# Agent Project Blueprint & Roadmap: Commit-Aware Test Selection

**Project:** Enhancing CI/CD Pipeline Cost-Effectiveness through Commit-Aware Test Selection  
**Target:** ICSE 2027 Industry Challenge  
**Status as of October 2026:** Phase 1 (Dataset Extraction, Label Joining, and Offline Semantic Indexing) is 100% Complete.

---

## 1. Executive Summary & Objective

Modern Continuous Integration (CI) systems re-run the entire test suite for every commit or pull request, leading to massive redundant compute, feedback latency, and energy waste.

Our system optimizes CI pipelines by predicting which tests are actually needed for a given commit:

1. **Offline Test Understanding (Done):** An LLM reads test files *once in advance*, writing plain summaries. These summaries are embedded into vector representations.
2. **Commit-Time Fast Screening (Next):** When a commit arrives, fast signals (semantic embedding similarity, file path heuristics, historical failure patterns) filter tests into *definitely run*, *definitely skip*, and a small *uncertain* band.
3. **Statistical Safety (Conformal Risk Control):** A mathematically bounded threshold ensures that the probability of skipping a bug-detecting test does not exceed a user-defined tolerance (e.g., $\le 5\%$).
4. **Drift Adaptation:** As software evolves, empirical drift monitoring detects changes and triggers recalibration.
5. **Cost-Effectiveness:** Every token, dollar, and second spent is offset against the cloud machine compute saved by skipping tests.

---

## 2. Comprehensive Audit: What Has Been Accomplished

### A. Commit Extraction and Proper Diff Handling

* **Fixed Column Schema:** Successfully resolved dataset schema variations (`id`, `commits`, `started_at` in TCP-CI v2 vs older documentation).
* **Merge Commit Resolution:** Standard `git diff` on merge commits returned empty diffs. We updated `extract_commit_data.py` to compare against the commit's first parent (`git rev-list --parents`).
  * Log4j: All 19 previously empty diffs now have full diffs (0 empty diffs remaining).
  * ShardingSphere: 12 of 14 empty diffs were resolved. The remaining 2 (`b7ab87321702` and `d31e051c2bb5`) are verified zero-change commits preserved with explicit tracking.
* **Engine Optimization:** Replaced slow repository-wide file scans with metadata-targeted queries, reducing dataset build time by over $10\times$.
* **Safe Output Routing:** All builds and diffs are saved without destructive overwriting.

### B. Accurate Test Label & Feature Joining

* **Executable Test Results:** Earlier extracted JSONL files contained empty test outcome arrays. We fixed the join between `builds.csv`, `exe.csv`, and `id_map.csv`. Every single build now has verified test names, verdicts (0 = pass, 1 = fail), and durations.
* **152 TCP-CI Features Integrated:** Built `join_dataset_features.py` using SQLite indexing. Each executed test outcome record now embeds its full 152 historical and structural feature vector from `dataset.csv` when available (or explicit `null` when unmeasured), producing `complete_dataset.jsonl`.

### C. Ground-Truth Test Inventory & Source Code Extraction

* **Strict Entity Type Filtering:** Replaced naive substring matching (`"test" in path`, which mistakenly flagged 770 production files in ShardingSphere like `KeyGenerateStrategyConfiguration`) with TCP-CI's verified `EntityType == "TEST"` tags from build metadata.
* **Windows Path Fix:** Switched from `git show commit:path` to `git cat-file -p commit:path`, eliminating Windows path-length failures (`Filename too long`).
* **Final Inventory:**
  * Log4j: Exactly **1,064** clean unique test files.
  * ShardingSphere: Exactly **2,161** clean unique test files (19 unresolvable Git paths cleanly excluded).

### D. Offline Semantic Indexing (LLM Summaries & Vector Embeddings)

* **Summary Generation:** Generated high-quality 2–3 sentence summaries using `gemini-3.5-flash` with thinking disabled (`thinking_budget: 0`) to prevent runaway token expenditure.
* **Batch Embedding Fix:** Discovered and fixed a critical Google GenAI SDK behavior where `gemini-embedding-2` returns 1 aggregated vector if given a plain Python list of strings. Wrapping each summary into its own `types.Content` object restored true $1:1$ vector generation.
* **100% Vector Alignment Verified:**
  * `apache@logging-log4j2`: Exactly **1,064 summaries**, **1,064 index rows**, and array shape **(1064, 3072)**.
  * `apache@shardingsphere`: Exactly **2,161 summaries**, **2,161 index rows**, and array shape **(2161, 3072)**.

### E. Verified Real-World Billing & Cost Data

By recording official Google billing checkpoints before and after every phase, we achieved the following exact cost breakdown for the offline indexing stage:

| Step / Project | Model Used | Baseline Total | After Run Total | Incremental Cost | Scope / Volume |
| --- | --- | ---: | ---: | ---: | --- |
| **Log4j Summaries** | `gemini-3.5-flash` | ₹502.72 | ₹752.88 | **₹250.16** | 1,064 test files (1.04M input / 116k output tokens) |
| **Log4j Embeddings** | `gemini-embedding-2` | ₹16.20 | ₹18.63 | **₹2.43** | 1,064 vectors (3,072 dimensions) |
| **ShardingSphere Summaries** | `gemini-3.5-flash` | ₹752.88 | ₹1,240.00 | **₹487.12** | 2,161 test files (2.04M input / 219k output tokens) |
| **ShardingSphere Embeddings** | `gemini-embedding-2` | ₹18.63 | ₹22.81 | **₹4.18** | 2,161 vectors (3,072 dimensions) |
| **Total Clean Offline Indexing** | Both models | — | — | **₹743.89** | All 3,225 tests across both projects |

Notice how clean and proportional the numbers are:

* ShardingSphere has ~2.03x as many tests as Log4j (2,161 vs 1,064).
* Summary cost was ~1.95x (₹487.12 vs ₹250.16).
* Embedding cost was ~1.72x (₹4.18 vs ₹2.43).
* These exact numbers can be reported directly in the ICSE paper's offline indexing cost table.

The canonical files in `TCP-CI-dataset-v2/datasets/<project>/` are:

* `complete_dataset.jsonl`
* `unique_tests.jsonl`
* `test_summaries.jsonl`
* `test_index.jsonl`
* `test_embeddings.npz`

---

## 3. Files to Share vs Push to Git

### Git vs Cloud Drive Separation

* **Tracked in Git (Code only):**
  * `extract_commit_data.py`
  * `join_dataset_features.py`
  * `src/offline_indexing/extract_unique_tests.py`
  * `src/offline_indexing/embed_tests.py`
  * `src/offline_indexing/llm_test_summarizer.py`
  * `src/config.py`
  * `requirements.txt`
  * `agent.md`
* **Shared via OneDrive / External Drive (Data only — ignored by Git due to size):**
  * `complete_dataset.jsonl` (~748 MB for Log4j, ~824 MB for ShardingSphere)
  * `test_embeddings.npz` (~12 MB for Log4j, ~25 MB for ShardingSphere)
  * `test_summaries.jsonl`, `test_index.jsonl`, `unique_tests.jsonl`

---

## 4. Next Steps: Detailed Action Plan (Phases 2 & 3)

With Phase 1 complete, the project moves to **Phase 2 (Selection, Calibration, and Evaluation)**. Below are the next 5 specific, sequential steps in detail:

### Step 1: Chronological 3-Way Dataset Slicing (`src/data_pipeline/split_dataset.py`)

* **What to do:** Divide each project's history in `complete_dataset.jsonl` strictly by build timestamp into 3 sequential periods:
  1. **Slice 1 — Parameter Tuning Slice (Earliest 25% of builds):**
     * Used solely to sweep and select hyperparameters such as the exponential decay weight $\rho$ and baseline model parameters.
     * Never touched during calibration or testing.
  2. **Slice 2 — Calibration Slice (Middle 35% of builds):**
     * Used to compute the rolling-window conformal risk threshold $\hat{\lambda}$ that guarantees bounded regression risk.
  3. **Slice 3 — Out-of-Sample Evaluation Slice (Latest 40% of builds):**
     * Used purely as the simulated production timeline to measure test savings, fault detection rate, drift detection, and monetary trade-offs.
* **Why Chronological:** Standard random k-fold cross-validation is invalid in continuous integration because it causes *temporal data leakage* (using future test failure history to predict past commits).
* **Deliverable:** A script that outputs `splits.json` specifying build IDs and dates for Slices 1, 2, and 3 for both projects.

### Step 2: Implement Test Selection Baselines (`src/evaluation/baselines.py`)

* **What to do:** Establish the benchmark competitors that our commit-aware LLM approach must beat:
  1. **Baseline A — Run All Tests:**
     * Standard CI behavior. Flawless fault recall (100%), but zero compute or time savings (0% skipped).
  2. **Baseline B — File Path & Lexical Heuristic:**
     * Selects tests sharing module prefixes, directory paths, or naming patterns with changed source files.
     * Fast and zero-cost, but misses cross-module regressions.
  3. **Baseline C — TCP-CI Historical ML Ranker:**
     * Uses the precomputed 152 features in `complete_dataset.jsonl` (prior failure rate, code churn, transition age, test execution time) to rank tests using a Random Forest or Gradient Boosted model.
* **Deliverable:** Standardized baseline evaluation functions producing baseline metrics on the test slices.

### Step 3: Implement Online Commit-Aware Semantic Selector (`src/online_selection/semantic_selector.py`)

* **What to do:** At build/PR time, assess test relevance by comparing the incoming commit against all test summaries:
  1. **Diff Embedding:** Extract the commit's diff text and obtain its vector embedding using `gemini-embedding-2`.
  2. **Cosine Similarity Calculation:** Compute cosine similarity between the commit diff embedding $\mathbf{e}_c$ and each pre-indexed test embedding $\mathbf{e}_{t_i}$ in `test_embeddings.npz` using fast matrix multiplication:
     $$\text{sim}(c, t_i) = \frac{\mathbf{e}_c \cdot \mathbf{e}_{t_i}}{\|\mathbf{e}_c\| \|\mathbf{e}_{t_i}\|}$$
  3. **Triage Classification:** Partition tests into three tiers:
     * **High Confidence Relevant:** $\text{sim} \ge \tau_{\text{high}} \implies$ Select immediately.
     * **High Confidence Irrelevant:** $\text{sim} \le \tau_{\text{low}} \implies$ Skip immediately.
     * **Uncertain Band:** $\tau_{\text{low}} < \text{sim} < \tau_{\text{high}} \implies$ Candidate for small batch LLM verification or conformal safety inclusion.
* **Deliverable:** A fast vector selector that maps any commit diff to a ranked relevance score for every test.

### Step 4: Time-Decayed Conformal Risk Control Calibration (`src/conformal/calibration.py`)

* **What to do:** Convert continuous similarity scores into a rigorous, statistically guaranteed test selection set $\mathcal{T}_c$ for commit $c$:
  1. **Risk Metric Definition:** Define the empirical fault miss-rate as:
     $$R = \mathbb{E}\left[ \frac{\sum_{t \in \mathcal{F}_c} \mathbb{I}(t \notin \mathcal{T}_c)}{\max(1, |\mathcal{F}_c|)} \right]$$
     where $\mathcal{F}_c$ is the set of tests that actually failed on build $c$.
  2. **Exponentially Weighted Rolling Window:** Use the decay parameter $\rho \in (0, 1)$ chosen on Slice 1 to weight recent calibration builds higher than older builds:
     $$w_i = \rho^{t_{\text{current}} - t_i}$$
  3. **Threshold Calibration:** Calculate threshold $\hat{\lambda}$ on Slice 2 such that the weighted empirical risk satisfies:
     $$\hat{R}_w(\hat{\lambda}) \le \alpha$$
     where $\alpha$ is the target error ceiling (e.g., $\alpha = 0.05$ for $95\%$ fault recall).
* **Deliverable:** Conformal calibration engine that dynamically outputs threshold $\hat{\lambda}$ guaranteeing safety on future builds.

### Step 5: Concept Drift Detection & Multi-Dimensional Cost Evaluation (`src/drift/drift_monitor.py` & `src/evaluation/metrics.py`)

* **What to do:** Run the full pipeline forward through Slice 3 (the evaluation timeline) and measure performance:
  1. **Drift Detection:** Monitor the rolling empirical miss rate over the latest $W$ builds. If the achieved risk breaches $\alpha + \epsilon$ due to test taxonomy or architectural shifts, trigger automatic threshold adjustment (widening $\hat{\lambda}$ or recalibration).
  2. **Metrics Computation:**
     * **APFD (Average Percentage of Faults Detected):** Standard SE metric for prioritization quality.
     * **Fault Recall at Budget:** What percentage of true failures are caught when selecting $10\%, 20\%, 30\%$ of the test suite.
     * **Test Execution Reduction Rate:** Percentage of test executions eliminated compared to Run All.
  3. **Cost-Benefit Balance:** Compute the net cost equation:
     $$\text{Net Savings} = (\text{Machine Compute Saved} \times \text{VM Cost/hr}) - (\text{Embedding API Cost} + \text{LLM Call Cost})$$
     demonstrating whether commit-aware selection provides positive ROI under realistic CI feedback latency constraints.
* **Deliverable:** Comprehensive tables and figures ready for inclusion in the final paper draft.

---

## 5. Summary of Immediate Action Items

1. **Commit and Push:** Run `git add` and `git commit` for `agent.md` and updated code scripts.
2. **Transfer Data:** Share the canonical data files (`complete_dataset.jsonl`, `test_embeddings.npz`, etc.) through OneDrive / Google Drive.
3. **Start Step 1:** Create `src/data_pipeline/split_dataset.py` to freeze the chronological 3-way partition across both projects.

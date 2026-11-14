# Commit-Aware Test Selection with Conformal Risk Control

[![ICSE 2027](https://img.shields.io/badge/Target-ICSE%202027-blue.svg)](https://conf.researchr.org/home/icse-2027)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An intelligent Continuous Integration (CI) test selection framework that reduces redundant test executions while providing rigorous statistical guarantees on fault recall using **Conformal Risk Control** and **Semantic Diff-to-Test Embeddings**.

---

## 1. Overview & Key Architecture

Modern CI pipelines re-execute the entire test suite on every commit, wasting massive cloud compute, developer time, and energy. Standard predictive test prioritization models (e.g., GBDT on historical test telemetry) suffer from two failure modes:
1. **Zero-History Blindness:** They miss new regressions in code with 0% prior failure history.
2. **Lack of Theoretical Safety:** They rely on arbitrary percentile cutoffs with no mathematical bound on missed bugs.

Our system resolves this with a tiered, cost-effective architecture:

```
                  Commit Diff arrives at CI
                             │
                             ▼
              ┌─────────────────────────────┐
              │    Tier 1: Fast Screening   │
              │  • Telemetry Fragile Guard  │──► [Fragile/Flaky Tests] ──► MUST RUN
              │  • Semantic Vector Sim (λ)  │
              └──────────────┬──────────────┘
                             │
            ┌────────────────┴────────────────┐
            ▼                                 ▼
   [sim >= λ + δ]                     [sim < λ - δ]
   Definitely Run                    Definitely Skip
            │                                 │
            └─────────►  Uncertain  ◄─────────┘
                   [λ - δ <= sim < λ + δ]
                             │
                             ▼
              ┌─────────────────────────────┐
              │   Tier 2: LLM Batch Judge   │
              │  (Gemini Flash with Cache)  │──► Final Selection
              └─────────────────────────────┘
```

1. **Offline Test Understanding:** Test files are summarized and embedded into a dense 3,072-dimensional semantic index using `gemini-embedding-2`.
2. **Tier-1 Fast Screening:**
   - **Fragile Test Guard:** Identifies historically volatile or flaky tests from CI telemetry and guarantees they run.
   - **Semantic Similarity Layer:** Matches commit diff embeddings against test code summaries to catch zero-history regressions across disparate modules.
3. **Finite-Sample Conformal Risk Control:** Employs non-exchangeable weighted risk calibration (Angelopoulos et al., 2024; Bates et al., 2021) to guarantee fault risk $\le \alpha$ (e.g., $\le 5\%$) without data leakage across chronological splits.
4. **Tier-2 LLM Boundary Judge:** Tests falling within a narrow confidence band $[\hat{\lambda} - \delta, \hat{\lambda} + \delta]$ are evaluated with their functional code context by Gemini Flash via batched prompts.
5. **Multi-Dimensional Accounting:** Beyond recall and APFD, measures net cloud savings (at \$0.10/hr and \$0.50/hr VM tiers), developer latency saved, break-even PR horizons, and avoided $\text{CO}_2\text{e}$ carbon emissions.

---

## 2. Repository Structure

```
ICSE/
├── experiments/
│   └── run_pipeline.py            # Master end-to-end evaluation orchestration
├── src/
│   ├── config.py                  # Global configurations and API settings
│   ├── conformal/
│   │   ├── calibration.py         # Non-exchangeable conformal risk calibration
│   │   └── rho_selection.py       # Exponential decay parameter sweep & Kish ESS
│   ├── data_pipeline/
│   │   └── split_dataset.py       # Strict 3-slice chronological dataset partitioning
│   ├── drift/
│   │   └── drift_monitor.py       # Online concept drift monitoring & recalibration
│   ├── evaluation/
│   │   ├── baselines.py           # Baselines: Run-All, Path Heuristic, TCP-CI GBDT
│   │   ├── cost_analysis.py       # Financial ROI, developer latency, green carbon metrics
│   │   └── metrics.py             # APFD, Recall@Budget, and multi-method benchmark
│   ├── offline_indexing/
│   │   ├── embed_tests.py         # 3072-dim batch vector embedding generation
│   │   ├── extract_unique_tests.py# Test file inventory from build execution logs
│   │   └── llm_test_summarizer.py # LLM test functionality summarization
│   ├── online_selection/
│   │   ├── llm_batch_selector.py  # Tier-2 LLM boundary judge with deterministic cache
│   │   └── semantic_selector.py   # Vector cosine similarity matching & cache
│   └── visualization/
│       └── paper_figures.py       # 300 DPI publication figures & LaTeX tables
├── progress.md                    # Comprehensive empirical evaluation results & case studies
├── project_plan.md                # 10-week implementation roadmap
└── requirements.txt               # Project dependencies
```

---

## 3. Quick Start

### Installation
```bash
# Clone the repository
git clone "repo link"
cd Commit-Aware

# Create virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Environment Configuration
Create a `.env` file in the root directory:
```env
GEMINI_API_KEY=your_gemini_api_key_here
```

### Running the End-to-End Evaluation Pipeline
To reproduce the full benchmark evaluation, baseline comparisons, cost models, and LaTeX tables:

```bash
# Evaluate Apache ShardingSphere
python experiments/run_pipeline.py shardingsphere

# Evaluate Apache Sling
python experiments/run_pipeline.py sling

# Evaluate zolyfarkas@spf4j
python experiments/run_pipeline.py spf4j
```

### Generating Publication Figures & Tables
```bash
# Generate high-resolution figures and LaTeX tables
python -m src.visualization.paper_figures shardingsphere
python -m src.visualization.paper_figures sling
python -m src.visualization.paper_figures spf4j
```

---

## 4. Key Results Summary (Out-of-Sample Evaluation: Slice 3)

### Apache ShardingSphere (420 builds, 239 true failures)
* **Run All (Baseline A):** 100.00% Recall | 0.00% Reduction | -$0.08 Net Savings
* **Path Heuristic (Baseline B):** 6.69% Recall (misses 223 failures) | 79.52% Reduction | +$39.05 Net Savings
* **TCP-CI GBDT (Baseline C):** 99.58% Recall | 50.03% Reduction | +$24.53 Net Savings
* **Fragile Guard Only (Ablation):** 92.47% Recall (misses 18 zero-history bugs) | 99.93% Reduction | +$49.09 Net Savings
* **Hybrid Model (Balanced):** **99.58% Recall** | **29.54% Reduction** | **+$14.45 Net Savings** (Catches all 18 zero-history bugs)
* **Hybrid Model (High-Savings):** **94.14% Recall** | **91.77% Reduction** | **+$45.07 Net Savings**
* **Full Model (Hybrid + LLM Judge):** **99.16% Recall** | **42.63% Reduction** | **+$20.23 Net Savings**

### Apache Sling (562 builds, 113 true failures)
* **Run All (Baseline A):** 100.00% Recall | 0.00% Reduction | -$0.11 Net Savings
* **Path Heuristic (Baseline B):** 33.63% Recall (misses 75 failures) | 58.74% Reduction | +$9.77 Net Savings
* **TCP-CI GBDT (Baseline C):** 66.37% Recall | 50.13% Reduction | +$8.32 Net Savings
* **Hybrid Model (Balanced):** **99.11% Recall** | **10.74% Reduction** | **+$1.69 Net Savings** (Catches 36 zero-history bugs)
* **Hybrid Model (High-Savings):** **74.34% Recall** | **74.77% Reduction** | **+$12.47 Net Savings**
* **Full Model (Hybrid + LLM Judge):** **99.11% Recall** | **3.79% Reduction** | **+$0.52 Net Savings**

### zolyfarkas@spf4j (236 builds, 144 true failures)
* **Run All (Baseline A):** 100.00% Recall | 0.00% Reduction | -$0.05 Net Savings
* **Path Heuristic (Baseline B):** 52.78% Recall (misses 68 failures) | 50.96% Reduction | +$1.97 Net Savings
* **TCP-CI GBDT (Baseline C):** 94.44% Recall | 50.21% Reduction | +$1.94 Net Savings
* **Hybrid Model (Balanced):** **97.92% Recall** | **24.49% Reduction** | **+$0.92 Net Savings** (Catches 29 zero-history bugs)
* **Hybrid Model (High-Savings):** **93.75% Recall** | **80.16% Reduction** | **+$3.13 Net Savings**
* **Full Model (Hybrid + LLM Judge):** **98.61% Recall** | **10.56% Reduction** | **+$0.37 Net Savings**

Detailed case studies (including commit `c47772c3` catching cross-module regressions missed by telemetry) and ablation experiments are documented in [`progress.md`](progress.md).

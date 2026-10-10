"""
Step 5b: Multi-Dimensional Cost Evaluation & Metrics.

Computes comprehensive evaluation metrics for the full pipeline, comparing
all approaches (baselines + semantic selector + conformal selector) on
the evaluation slice (Slice 3).

Metrics computed:
    - APFD (Average Percentage of Faults Detected)
    - Fault Recall at Budget (10%, 20%, 30% of test suite)
    - Test Execution Reduction Rate
    - Cost-Benefit Analysis: Net Savings = VM savings - API costs

Usage:
    python -m src.evaluation.metrics log4j2
    python -m src.evaluation.metrics shardingsphere --vm-cost-per-hour 0.50
"""
import json
import argparse
import sys
from pathlib import Path
from collections import defaultdict

# pyrefly: ignore [missing-import]
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS


# ── Cost assumptions ──
DEFAULT_VM_COST_PER_HOUR = 0.10     # USD per VM-hour (GCP e2-standard-2 ~$0.067)
DEFAULT_EMBED_COST_PER_1K = 0.0001  # USD per 1K tokens for embedding API
DEFAULT_LLM_COST_PER_1K = 0.0005    # USD per 1K tokens for LLM calls
AVG_DIFF_TOKENS = 2000              # Approximate tokens per diff
AVG_TEST_DURATION_SEC = 5.0         # Fallback average test execution time


def compute_apfd(ranked_tests, failure_set, total_tests):
    """Compute Average Percentage of Faults Detected.

    APFD = 1 - (Σ TF_i / (n × m)) + 1/(2n)

    where TF_i is the position (1-indexed) of the i-th fault in the
    ranked ordering, n = total tests, m = total faults.

    Higher APFD = better prioritization (faults found earlier).

    Args:
        ranked_tests: List of test names in prioritized order.
        failure_set:  Set of test names that actually failed.
        total_tests:  Total number of tests in the suite.

    Returns:
        APFD score in [0, 1].
    """
    m = len(failure_set)
    n = total_tests
    if m == 0 or n == 0:
        return 1.0  # No faults → perfect APFD by convention

    tf_sum = 0
    for rank, test in enumerate(ranked_tests, start=1):
        if test in failure_set:
            tf_sum += rank

    # Add unranked failures at the end (if any failures weren't in ranked_tests)
    ranked_set = set(ranked_tests)
    unranked_failures = failure_set - ranked_set
    next_rank = len(ranked_tests) + 1
    for _ in unranked_failures:
        tf_sum += next_rank
        next_rank += 1

    apfd = 1.0 - (tf_sum / (n * m)) + (1.0 / (2 * n))
    return max(0.0, min(1.0, apfd))


def compute_recall_at_budget(ranked_tests, failure_set, total_tests, budgets=(0.10, 0.20, 0.30)):
    """Compute fault recall when selecting only the top-k% of tests.

    Args:
        ranked_tests: Tests ranked by relevance/priority (most important first).
        failure_set:  Set of tests that actually failed.
        total_tests:  Total test count.
        budgets:      Tuple of fraction values (e.g., 0.10 for 10%).

    Returns:
        Dict mapping budget% → recall.
    """
    results = {}
    m = len(failure_set)
    if m == 0:
        return {f"recall_at_{int(b*100)}pct": 1.0 for b in budgets}

    for budget in budgets:
        k = max(1, int(total_tests * budget))
        selected = set(ranked_tests[:k])
        caught = len(selected & failure_set)
        results[f"recall_at_{int(budget*100)}pct"] = round(caught / m, 6)

    return results


def compute_test_reduction(total_tests, selected_count):
    """Compute test execution reduction rate.

    Returns:
        Fraction of tests skipped (0 to 1).
    """
    if total_tests == 0:
        return 0.0
    return round((total_tests - selected_count) / total_tests, 6)


def compute_cost_benefit(total_builds, total_tests, selected_count,
                          avg_test_duration_sec=AVG_TEST_DURATION_SEC,
                          vm_cost_per_hour=DEFAULT_VM_COST_PER_HOUR,
                          embed_cost_per_1k=DEFAULT_EMBED_COST_PER_1K):
    """Compute the net financial savings from test selection.

    Net Savings = VM Compute Saved - API Embedding Costs

    Args:
        total_builds:    Number of builds processed.
        total_tests:     Total test executions if running all.
        selected_count:  Number of tests actually selected (= executed).
        avg_test_duration_sec: Average execution time per test.
        vm_cost_per_hour: VM compute cost per hour.
        embed_cost_per_1k: Embedding API cost per 1K tokens.

    Returns:
        Dict with compute_saved, api_cost, net_savings (all in USD).
    """
    skipped = total_tests - selected_count
    hours_saved = (skipped * avg_test_duration_sec) / 3600.0
    compute_saved = hours_saved * vm_cost_per_hour

    # API cost: one embedding per build
    api_tokens = total_builds * AVG_DIFF_TOKENS
    api_cost = (api_tokens / 1000.0) * embed_cost_per_1k

    net_savings = compute_saved - api_cost

    return {
        "tests_skipped": skipped,
        "vm_hours_saved": round(hours_saved, 4),
        "compute_saved_usd": round(compute_saved, 4),
        "api_tokens_used": api_tokens,
        "api_cost_usd": round(api_cost, 6),
        "net_savings_usd": round(net_savings, 4),
        "roi_ratio": round(compute_saved / max(0.0001, api_cost), 2),
    }


def evaluate_method(method_name, eval_records, score_fn, threshold=None,
                    select_fraction=None, vm_cost=DEFAULT_VM_COST_PER_HOUR):
    """Evaluate a single method across all builds in the evaluation slice.

    Args:
        method_name:     Name for reporting.
        eval_records:    List of build records from the evaluation slice.
        score_fn:        Function(build_record) → {test_name: score} dict.
                         Higher score = more likely to be relevant.
        threshold:       If set, select tests with score >= threshold.
        select_fraction: If set (and threshold is None), select top k%.
        vm_cost:         VM cost per hour for savings calculation.

    Returns:
        Comprehensive metrics dict.
    """
    all_apfd = []
    all_recall_budgets = defaultdict(list)
    total_tests_all = 0
    total_selected_all = 0
    total_failures_all = 0
    caught_failures_all = 0

    for rec in eval_records:
        outcomes = rec.get("test_outcomes", [])
        if not outcomes:
            continue

        test_names = [t["testName"] for t in outcomes if t.get("testName")]
        failure_set = {t["testName"] for t in outcomes
                       if t.get("testName") and t.get("verdict") == "1"}

        # Get scores for this build's tests
        scores = score_fn(rec)

        # Rank tests by score (descending)
        ranked = sorted(test_names, key=lambda t: scores.get(t, 0.0), reverse=True)

        # Determine selected set
        if threshold is not None:
            selected = [t for t in ranked if scores.get(t, 0.0) >= threshold]
        elif select_fraction is not None:
            k = max(1, int(len(ranked) * select_fraction))
            selected = ranked[:k]
        else:
            selected = ranked  # Run all

        selected_set = set(selected)

        # APFD
        apfd = compute_apfd(ranked, failure_set, len(test_names))
        all_apfd.append(apfd)

        # Recall at budget
        recall_budgets = compute_recall_at_budget(ranked, failure_set, len(test_names))
        for key, val in recall_budgets.items():
            all_recall_budgets[key].append(val)

        # Counting
        total_tests_all += len(test_names)
        total_selected_all += len(selected)
        total_failures_all += len(failure_set)
        caught_failures_all += len(failure_set & selected_set)

    # Aggregate
    fault_recall = caught_failures_all / max(1, total_failures_all)
    test_reduction = compute_test_reduction(total_tests_all, total_selected_all)
    cost_benefit = compute_cost_benefit(
        len(eval_records), total_tests_all, total_selected_all,
        vm_cost_per_hour=vm_cost,
    )

    metrics = {
        "method": method_name,
        "total_builds": len(eval_records),
        "total_tests": total_tests_all,
        "total_selected": total_selected_all,
        "total_skipped": total_tests_all - total_selected_all,
        "total_failures": total_failures_all,
        "caught_failures": caught_failures_all,
        "missed_failures": total_failures_all - caught_failures_all,
        "fault_recall": round(fault_recall, 6),
        "test_reduction_rate": test_reduction,
        "apfd": {
            "mean": round(float(np.mean(all_apfd)), 6) if all_apfd else 0.0,
            "median": round(float(np.median(all_apfd)), 6) if all_apfd else 0.0,
            "std": round(float(np.std(all_apfd)), 6) if all_apfd else 0.0,
            "min": round(float(np.min(all_apfd)), 6) if all_apfd else 0.0,
            "max": round(float(np.max(all_apfd)), 6) if all_apfd else 0.0,
        },
        "recall_at_budget": {
            key: round(float(np.mean(vals)), 6)
            for key, vals in all_recall_budgets.items()
        },
        "cost_benefit": cost_benefit,
    }

    return metrics


def print_comparison_table(all_metrics):
    """Print a formatted comparison table across all methods."""
    print(f"\n{'=' * 90}")
    print(f"{'Method':<25} {'Recall':>10} {'APFD':>10} {'Reduction':>12} "
          f"{'R@10%':>8} {'R@20%':>8} {'Net$':>10}")
    print(f"{'-' * 90}")

    for m in all_metrics:
        recall = f"{m['fault_recall']:.2%}"
        apfd = f"{m['apfd']['mean']:.4f}"
        reduction = f"{m['test_reduction_rate']:.2%}"
        r10 = f"{m['recall_at_budget'].get('recall_at_10pct', 0):.2%}"
        r20 = f"{m['recall_at_budget'].get('recall_at_20pct', 0):.2%}"
        net_savings = f"${m['cost_benefit']['net_savings_usd']:.2f}"
        print(f"{m['method']:<25} {recall:>10} {apfd:>10} {reduction:>12} "
              f"{r10:>8} {r20:>8} {net_savings:>10}")

    print(f"{'=' * 90}")


def run_evaluation(project_key, vm_cost=DEFAULT_VM_COST_PER_HOUR, eval_slice=None):
    """Run comprehensive evaluation comparing all methods."""
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    eval_path = Path(eval_slice) if eval_slice else project_folder / "slice_3_eval.jsonl"
    if not eval_path.exists():
        print(f"ERROR: Evaluation slice not found: {eval_path}")
        print("Run split_dataset.py first.")
        sys.exit(1)

    print(f"Loading evaluation slice from {eval_path.name}...")
    records = []
    with open(eval_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            records.append(json.loads(line))
    print(f"  Loaded {len(records)} build records")

    all_metrics = []

    # ── Method 1: Run All (Baseline A) ──
    print("\nEvaluating: Run All...")
    m1 = evaluate_method(
        "Run All (Baseline A)", records,
        score_fn=lambda rec: {t["testName"]: 1.0
                              for t in rec.get("test_outcomes", []) if t.get("testName")},
        vm_cost=vm_cost,
    )
    all_metrics.append(m1)

    # ── Method 2: File Path Heuristic (Baseline B) ──
    print("Evaluating: File Path Heuristic...")
    from src.evaluation.baselines import FilePathHeuristicBaseline
    baseline_b = FilePathHeuristicBaseline()
    m2 = evaluate_method(
        "Path Heuristic (B)", records,
        score_fn=lambda rec: baseline_b.select(rec)["scores"],
        threshold=0.01,  # Any non-zero match
        vm_cost=vm_cost,
    )
    all_metrics.append(m2)

    # ── Method 3: TCP-CI ML Ranker (Baseline C) ──
    print("Evaluating: TCP-CI Historical Ranker...")
    train_path = project_folder / "slice_1_tune.jsonl"
    if train_path.exists():
        from src.evaluation.baselines import TCPCIHistoricalBaseline
        baseline_c = TCPCIHistoricalBaseline(select_fraction=0.5)
        train_records = []
        with open(train_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                train_records.append(json.loads(line))
        baseline_c.fit(train_records)

        m3 = evaluate_method(
            "TCP-CI ML (C, top-50%)", records,
            score_fn=lambda rec: baseline_c.select(rec)["scores"],
            select_fraction=0.5,
            vm_cost=vm_cost,
        )
        all_metrics.append(m3)
    else:
        print("  Skipped: No training slice available for Baseline C.")

    # ── Initialize Semantic Selector ──
    selector = None
    try:
        from src.online_selection.semantic_selector import SemanticSelector
        selector = SemanticSelector(project_key, use_api=True)
        selector.precompute_diffs_for_records(records)
    except Exception as e:
        print(f"  Note: Semantic selector not initialized ({e})")

    # ── Method 4: Semantic Selector (fixed thresholds) ──
    if selector:
        print("Evaluating: Semantic Selector (fixed threshold)...")
        m4 = evaluate_method(
            "Semantic Selector", records,
            score_fn=lambda rec: selector.select(rec)["scores"],
            threshold=selector.tau_low,
            vm_cost=vm_cost,
        )
        all_metrics.append(m4)

    # ── Method 5: Conformal Selector ──
    cal_path = project_folder / "calibration_results.json"
    if cal_path.exists() and selector:
        with open(cal_path, encoding="utf-8") as f:
            cal_data = json.loads(f.read())
        lambda_hat = cal_data.get("lambda_hat", 0.4)
        print(f"Evaluating: Conformal Selector (lambda_hat={lambda_hat:.4f})...")
        m5 = evaluate_method(
            f"Conformal (lambda={lambda_hat:.2f})", records,
            score_fn=lambda rec: selector.select(rec)["scores"],
            threshold=lambda_hat,
            vm_cost=vm_cost,
        )
        all_metrics.append(m5)

    # Print comparison table
    print_comparison_table(all_metrics)

    # Save comprehensive results
    output = {
        "project": project_key,
        "evaluation_builds": len(records),
        "vm_cost_per_hour_usd": vm_cost,
        "methods": all_metrics,
    }

    results_path = project_folder / "evaluation_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nComprehensive results saved to {results_path}")

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    parser.add_argument("--vm-cost-per-hour", type=float, default=DEFAULT_VM_COST_PER_HOUR,
                        help=f"VM cost per hour in USD (default: {DEFAULT_VM_COST_PER_HOUR})")
    parser.add_argument("--eval-slice", help="Evaluation slice JSONL path")
    args = parser.parse_args()

    run_evaluation(args.project, args.vm_cost_per_hour, args.eval_slice)

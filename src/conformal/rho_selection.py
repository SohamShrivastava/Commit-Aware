"""
Task 1: Systematic Decay Parameter (rho) Sweep on Slice 1 (Tuning).

Evaluates a grid of exponential decay parameters rho on the tuning slice
(Slice 1) to empirically justify the choice of rho*. For each candidate:

    1. Compute effective sample size N_eff = (sum w_i)^2 / (sum w_i^2)
    2. Simulate rolling-window calibration stability across time splits
    3. Measure variance of calibrated lambda_hat across K temporal folds
    4. Select rho* that minimizes variance while retaining recency sensitivity

This eliminates reviewer skepticism about arbitrary hyperparameter choices
by providing a principled, data-driven justification.

Usage:
    python -m src.conformal.rho_selection log4j2
    python -m src.conformal.rho_selection shardingsphere --alpha 0.05
"""
import json
import argparse
import sys
from pathlib import Path
from datetime import datetime

# pyrefly: ignore [missing-import]
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.config import PROJECTS


# ── Default parameters ──
DEFAULT_ALPHA = 0.05
RHO_CANDIDATES = [0.90, 0.95, 0.98, 0.99, 0.995, 0.999, 1.0]
K_FOLDS = 5          # Number of temporal folds for cross-validation
LAMBDA_GRID_SIZE = 200


def parse_timestamp(ts_str):
    """Parse a timestamp string, returning seconds since epoch."""
    if not ts_str:
        return 0.0
    ts_str = ts_str.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(ts_str, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def compute_effective_sample_size(weights):
    """Compute Kish's effective sample size from a weight vector.

    N_eff = (sum w_i)^2 / (sum w_i^2)

    A higher N_eff means the weights are more uniform (less concentration).
    N_eff = N when all weights are equal (rho = 1.0).
    """
    w_sum = np.sum(weights)
    w_sq_sum = np.sum(weights ** 2)
    if w_sq_sum < 1e-12:
        return 0.0
    return float((w_sum ** 2) / w_sq_sum)


def calibrate_for_rho(builds, rho, alpha, sim_min, sim_max):
    """Run conformal calibration for a specific rho on a set of builds.

    Returns:
        lambda_hat: Calibrated threshold.
        weighted_risk: Achieved risk at lambda_hat.
    """
    if not builds:
        return 0.35, 0.0

    lambda_candidates = np.linspace(sim_min, sim_max, LAMBDA_GRID_SIZE)

    # Check for any failures
    total_failures = sum(
        sum(1 for t in b["tests"] if t.get("is_failure")) for b in builds
    )
    if total_failures == 0:
        return 0.35, 0.0

    # Compute time-decayed weights
    t_max = max(b["timestamp"] for b in builds)
    time_diffs = [(t_max - b["timestamp"]) / 86400.0 for b in builds]
    weights = np.array([rho ** td for td in time_diffs], dtype=np.float64)
    weight_sum = weights.sum()
    if weight_sum < 1e-12:
        weights = np.ones(len(builds)) / len(builds)
        weight_sum = 1.0

    # Grid search for best lambda
    best_lambda = sim_min
    best_risk = 1.0

    for lam in lambda_candidates:
        per_build_risk = np.zeros(len(builds))
        for b_idx, build in enumerate(builds):
            failures = [t for t in build["tests"] if t["is_failure"]]
            if not failures:
                per_build_risk[b_idx] = 0.0
                continue
            missed = sum(1 for t in failures if t["similarity"] < lam)
            per_build_risk[b_idx] = missed / len(failures)

        weighted_risk = np.sum(weights * per_build_risk) / weight_sum

        if weighted_risk <= alpha and lam >= best_lambda:
            best_lambda = float(lam)
            best_risk = float(weighted_risk)

    return best_lambda, best_risk


def temporal_cross_validate(builds, rho, alpha, k_folds=K_FOLDS):
    """Perform temporal cross-validation for a given rho.

    Splits the tuning builds into K sequential temporal folds and
    calibrates on each prefix, measuring lambda_hat stability.

    Returns:
        dict with lambda values per fold and their variance.
    """
    n = len(builds)
    if n < k_folds * 2:
        # Not enough builds for meaningful cross-validation
        k_folds = max(2, n // 3)

    # Collect global similarity range
    all_sims = []
    for b in builds:
        for t in b["tests"]:
            all_sims.append(t["similarity"])
    if not all_sims:
        return {"lambdas": [], "variance": 0.0, "mean": 0.35, "std": 0.0}

    sim_min = min(all_sims)
    sim_max = max(all_sims)

    # Create K temporal folds: fold i uses builds[0 : n*(i+1)/K]
    fold_lambdas = []
    fold_risks = []
    for fold in range(1, k_folds + 1):
        end_idx = int(n * fold / k_folds)
        if end_idx < 5:
            continue  # Need minimum 5 builds for meaningful calibration
        fold_builds = builds[:end_idx]
        lam, risk = calibrate_for_rho(fold_builds, rho, alpha, sim_min, sim_max)
        fold_lambdas.append(lam)
        fold_risks.append(risk)

    if not fold_lambdas:
        return {"lambdas": [], "variance": 0.0, "mean": 0.35, "std": 0.0}

    lambdas_arr = np.array(fold_lambdas)
    return {
        "lambdas": [round(l, 6) for l in fold_lambdas],
        "risks": [round(r, 6) for r in fold_risks],
        "variance": round(float(np.var(lambdas_arr)), 8),
        "mean": round(float(np.mean(lambdas_arr)), 6),
        "std": round(float(np.std(lambdas_arr)), 6),
        "min": round(float(np.min(lambdas_arr)), 6),
        "max": round(float(np.max(lambdas_arr)), 6),
    }


def prepare_tune_builds(selector, tune_records):
    """Convert tuning slice records into calibration build format.

    Embeds diffs and pairs with ground-truth failure labels.
    """
    # Precompute diff embeddings in fast batches
    selector.precompute_diffs_for_records(tune_records)

    builds = []
    for i, rec in enumerate(tune_records):
        diff_text = rec.get("diff_text", "")
        key = str(rec.get("commit_sha") or rec.get("build_id"))

        diff_emb = selector.embed_diff(diff_text, cache_key=key)
        similarities = selector.score_tests(diff_emb)

        tests = []
        for outcome in rec.get("test_outcomes", []):
            name = outcome.get("testName")
            if not name:
                continue
            idx = selector.path_to_idx.get(name)
            sim = float(similarities[idx]) if idx is not None and idx < len(similarities) else 0.5
            tests.append({
                "test_name": name,
                "similarity": sim,
                "is_failure": str(outcome.get("verdict")) in ("1", "2", "3"),
            })

        ts = parse_timestamp(rec.get("started_at"))
        builds.append({
            "build_id": str(rec["build_id"]),
            "timestamp": ts,
            "tests": tests,
        })

        if (i + 1) % 50 == 0:
            print(f"    Prepared {i+1}/{len(tune_records)} tuning builds...")

    return builds


def run_rho_sweep(project_key, alpha=DEFAULT_ALPHA, rho_grid=None,
                  tune_slice=None, limit=None):
    """Execute systematic rho sweep on Slice 1 (Tuning).

    For each candidate rho:
        1. Compute N_eff (effective sample size)
        2. Run temporal K-fold cross-validation
        3. Measure lambda_hat variance across folds

    Select rho* that minimizes variance while maintaining reasonable N_eff.

    Args:
        project_key: Key into PROJECTS config.
        alpha: Target error ceiling.
        rho_grid: List of rho values to evaluate.
        tune_slice: Path to tuning slice JSONL (default: slice_1_tune.jsonl).
        limit: Process only first N builds (for testing).

    Returns:
        dict with sweep results and selected rho*.
    """
    if rho_grid is None:
        rho_grid = RHO_CANDIDATES

    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    tune_path = Path(tune_slice) if tune_slice else project_folder / "slice_1_tune.jsonl"
    if not tune_path.exists():
        print(f"ERROR: Tuning slice not found: {tune_path}")
        print("Run split_dataset.py first to create chronological slices.")
        sys.exit(1)

    print(f"Loading tuning slice from {tune_path.name}...")
    records = []
    with open(tune_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            records.append(json.loads(line))
    if limit:
        records = records[:limit]
    print(f"  Loaded {len(records)} tuning builds")

    # Initialize semantic selector for embedding
    from src.online_selection.semantic_selector import SemanticSelector
    selector = SemanticSelector(project_key, use_api=True)

    print("\nPreparing tuning data (embedding diffs)...")
    tune_builds = prepare_tune_builds(selector, records)

    # Count failures in tuning slice
    total_failures = sum(
        sum(1 for t in b["tests"] if t.get("is_failure")) for b in tune_builds
    )
    total_tests = sum(len(b["tests"]) for b in tune_builds)
    print(f"  Total tests: {total_tests}, Total failures: {total_failures}")

    # Compute time range
    timestamps = [b["timestamp"] for b in tune_builds if b["timestamp"] > 0]
    if timestamps:
        t_min, t_max = min(timestamps), max(timestamps)
        span_days = (t_max - t_min) / 86400.0
    else:
        span_days = 0.0
    print(f"  Time span: {span_days:.1f} days")

    # ── Sweep rho candidates ──
    print(f"\n{'='*70}")
    print(f"{'rho':>8} | {'N_eff':>8} | {'N_eff/N':>8} | {'lambda_mean':>12} | "
          f"{'lambda_std':>11} | {'lambda_var':>11}")
    print(f"{'-'*70}")

    sweep_results = []
    for rho in rho_grid:
        # Compute weights and N_eff for this rho
        if timestamps:
            time_diffs = [(t_max - ts) / 86400.0 for ts in timestamps]
            weights = np.array([rho ** td for td in time_diffs], dtype=np.float64)
        else:
            weights = np.ones(len(tune_builds))

        n_eff = compute_effective_sample_size(weights)
        n_eff_ratio = n_eff / max(1, len(tune_builds))

        # Temporal cross-validation
        cv_result = temporal_cross_validate(tune_builds, rho, alpha)

        result = {
            "rho": rho,
            "n_eff": round(n_eff, 2),
            "n_eff_ratio": round(n_eff_ratio, 4),
            "cv": cv_result,
        }
        sweep_results.append(result)

        print(f"{rho:>8.3f} | {n_eff:>8.2f} | {n_eff_ratio:>8.4f} | "
              f"{cv_result['mean']:>12.6f} | {cv_result['std']:>11.6f} | "
              f"{cv_result['variance']:>11.8f}")

    print(f"{'='*70}")

    # ── Select optimal rho* ──
    # Strategy: minimize variance of lambda_hat across folds,
    # with a penalty for very low N_eff (< 10% of total builds)
    best_rho = None
    best_score = float("inf")
    for r in sweep_results:
        var = r["cv"]["variance"]
        n_ratio = r["n_eff_ratio"]
        # Penalize if N_eff drops below 10% of sample size
        penalty = max(0, (0.10 - n_ratio) * 10.0) if n_ratio < 0.10 else 0.0
        score = var + penalty
        r["selection_score"] = round(score, 8)
        if score < best_score:
            best_score = score
            best_rho = r

    selected_rho = best_rho["rho"] if best_rho else 0.995
    print(f"\nSelected rho* = {selected_rho}")
    print(f"  N_eff = {best_rho['n_eff']:.2f} "
          f"({best_rho['n_eff_ratio']*100:.1f}% of total builds)")
    print(f"  lambda_hat variance = {best_rho['cv']['variance']:.8f}")
    print(f"  lambda_hat mean = {best_rho['cv']['mean']:.6f}")

    # ── Save results ──
    output = {
        "project": project_key,
        "alpha": alpha,
        "total_builds": len(tune_builds),
        "total_tests": total_tests,
        "total_failures": total_failures,
        "time_span_days": round(span_days, 1),
        "k_folds": K_FOLDS,
        "rho_candidates": rho_grid,
        "selected_rho": selected_rho,
        "selected_rho_details": {
            "n_eff": best_rho["n_eff"],
            "n_eff_ratio": best_rho["n_eff_ratio"],
            "lambda_mean": best_rho["cv"]["mean"],
            "lambda_std": best_rho["cv"]["std"],
            "lambda_variance": best_rho["cv"]["variance"],
        },
        "sweep_results": sweep_results,
    }

    results_path = project_folder / "rho_sweep_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nSweep results saved to {results_path}")

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project", choices=list(PROJECTS.keys()),
                        help="Project key")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                        help=f"Target error ceiling (default: {DEFAULT_ALPHA})")
    parser.add_argument("--tune-slice",
                        help="Tuning slice JSONL path (default: slice_1_tune.jsonl)")
    parser.add_argument("--limit", type=int,
                        help="Process only first N builds (for testing)")
    args = parser.parse_args()

    run_rho_sweep(args.project, args.alpha, tune_slice=args.tune_slice,
                  limit=args.limit)

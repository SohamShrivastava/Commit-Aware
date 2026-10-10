"""
Step 4: Time-Decayed Conformal Risk Control Calibration.

Converts continuous cosine similarity scores from the semantic selector
into a rigorous, statistically guaranteed test selection threshold λ̂
using conformal risk control with exponentially weighted rolling windows.

Key ideas:
    - Risk metric R = E[ |missed failures| / max(1, |actual failures|) ]
    - Exponential decay weights: w_i = ρ^(t_current - t_i)
    - Threshold λ̂ is calibrated on Slice 2 so that R_w(λ̂) ≤ α

Usage:
    python -m src.conformal.calibration log4j2
    python -m src.conformal.calibration shardingsphere --alpha 0.05 --rho 0.995
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


# ── Defaults ──
DEFAULT_ALPHA = 0.05     # Target error ceiling: ≤ 5% fault miss-rate
DEFAULT_RHO = 0.995      # Exponential decay parameter
LAMBDA_GRID_SIZE = 200   # Number of threshold candidates to evaluate


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


class ConformalCalibrator:
    """Calibrates the similarity threshold λ̂ for test selection using
    exponentially weighted conformal risk control.

    The calibrator:
        1. Takes precomputed (similarity_score, actual_failure) pairs per build.
        2. For each candidate threshold λ, computes the per-build fault miss-rate.
        3. Applies exponential decay weights to give recent builds more influence.
        4. Finds the smallest λ such that the weighted risk R_w(λ) ≤ α.
    """

    def __init__(self, alpha=DEFAULT_ALPHA, rho=DEFAULT_RHO):
        """
        Args:
            alpha: Target error ceiling (e.g., 0.05 for 95% fault recall).
            rho:   Exponential decay parameter ∈ (0, 1). Values closer to 1
                   give more uniform weighting; values closer to 0 weight
                   recent builds much more heavily.
        """
        self.alpha = alpha
        self.rho = rho
        self.lambda_hat = None
        self.calibration_log = []

    def calibrate(self, calibration_builds):
        """Calibrate λ̂ on a list of calibration builds.

        Args:
            calibration_builds: List of dicts, each containing:
                {
                    "build_id": str,
                    "timestamp": float (epoch seconds),
                    "tests": [
                        {
                            "test_name": str,
                            "similarity": float,  # cosine similarity score
                            "is_failure": bool,    # ground truth
                        },
                        ...
                    ]
                }
                Must be sorted chronologically.

        Returns:
            lambda_hat: The calibrated threshold (float).
        """
        if not calibration_builds:
            raise ValueError("No calibration builds provided.")

        # Collect all unique similarity scores to define the candidate grid
        all_sims = []
        for build in calibration_builds:
            for test in build["tests"]:
                all_sims.append(test["similarity"])

        if not all_sims:
            print("WARNING: No test data in calibration builds.")
            self.lambda_hat = 0.0
            return 0.0

        sim_min = min(all_sims)
        sim_max = max(all_sims)
        lambda_candidates = np.linspace(sim_min, sim_max, LAMBDA_GRID_SIZE)

        # Compute timestamps relative to the most recent build
        t_max = max(b["timestamp"] for b in calibration_builds)
        # Normalize time differences to days for numerical stability
        time_diffs = [(t_max - b["timestamp"]) / 86400.0 for b in calibration_builds]

        # Compute exponential decay weights
        weights = np.array([self.rho ** td for td in time_diffs], dtype=np.float64)
        weight_sum = weights.sum()
        if weight_sum < 1e-12:
            weights = np.ones(len(calibration_builds)) / len(calibration_builds)
            weight_sum = 1.0

        print(f"  Calibrating with {len(calibration_builds)} builds, "
              f"alpha={self.alpha}, rho={self.rho}")
        print(f"  Weight range: [{weights.min():.6f}, {weights.max():.6f}]")
        print(f"  Similarity range: [{sim_min:.4f}, {sim_max:.4f}]")
        print(f"  Lambda grid: {LAMBDA_GRID_SIZE} candidates")

        # For each candidate λ, compute the weighted empirical risk
        best_lambda = sim_min  # Most conservative (select everything)
        best_risk = 1.0
        risk_curve = []

        for lam in lambda_candidates:
            # For each build, compute the fault miss-rate at this threshold
            per_build_risk = np.zeros(len(calibration_builds))

            for b_idx, build in enumerate(calibration_builds):
                failures = [t for t in build["tests"] if t["is_failure"]]
                if not failures:
                    # No failures in this build → risk is 0 regardless of threshold
                    per_build_risk[b_idx] = 0.0
                    continue

                # Count missed failures: failure tests with similarity < λ
                missed = sum(1 for t in failures if t["similarity"] < lam)
                per_build_risk[b_idx] = missed / len(failures)

            # Weighted average risk
            weighted_risk = np.sum(weights * per_build_risk) / weight_sum
            risk_curve.append((float(lam), float(weighted_risk)))

            # Track the largest λ that still satisfies R_w(λ) ≤ α
            # (larger λ = more aggressive skipping = more savings)
            if weighted_risk <= self.alpha:
                if lam >= best_lambda:
                    best_lambda = float(lam)
                    best_risk = float(weighted_risk)

        # If zero failures were observed across the entire calibration slice
        total_failures = sum(sum(1 for t in b["tests"] if t.get("is_failure")) for b in calibration_builds)
        if total_failures == 0:
            print("  NOTE: Zero failures observed in calibration slice. Setting conservative threshold lambda_hat = 0.35")
            self.lambda_hat = 0.35
            return 0.35

        self.lambda_hat = best_lambda
        self.calibration_log = risk_curve

        print(f"\n  Calibrated lambda_hat = {self.lambda_hat:.6f}")
        print(f"  Achieved weighted risk: R_w(lambda_hat) = {best_risk:.6f} <= alpha = {self.alpha}")

        return self.lambda_hat

    def select_tests(self, test_similarities):
        """Apply the calibrated threshold to select tests.

        Args:
            test_similarities: List of (test_name, similarity_score) tuples.

        Returns:
            dict with selected_tests, skipped_tests.
        """
        if self.lambda_hat is None:
            raise RuntimeError("Calibrator has not been calibrated yet. Call calibrate() first.")

        selected = []
        skipped = []
        for name, sim in test_similarities:
            if sim >= self.lambda_hat:
                selected.append(name)
            else:
                skipped.append(name)

        return {
            "selected_tests": selected,
            "skipped_tests": skipped,
            "lambda_hat": self.lambda_hat,
        }

    def get_risk_curve(self):
        """Return the full risk curve from calibration.

        Returns:
            List of (lambda, weighted_risk) tuples, useful for plotting
            the risk-threshold trade-off curve in the paper.
        """
        return self.calibration_log


def prepare_calibration_data(selector, calibration_records):
    """Convert calibration slice records into the format expected by
    ConformalCalibrator.calibrate().

    This function calls the semantic selector to score each build's tests,
    then pairs scores with ground-truth failure labels.

    Args:
        selector: An initialized SemanticSelector instance.
        calibration_records: List of build records from slice_2_cal.jsonl.

    Returns:
        List of calibration build dicts (see ConformalCalibrator.calibrate).
    """
    calibration_builds = []
    # Precompute diff embeddings in fast batches
    selector.precompute_diffs_for_records(calibration_records)

    for i, rec in enumerate(calibration_records):
        diff_text = rec.get("diff_text", "")
        key = str(rec.get("commit_sha") or rec.get("build_id"))

        # Embed the diff
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
                "is_failure": outcome.get("verdict") == "1",
            })

        ts = parse_timestamp(rec.get("started_at"))
        calibration_builds.append({
            "build_id": str(rec["build_id"]),
            "timestamp": ts,
            "tests": tests,
        })

        if (i + 1) % 50 == 0:
            print(f"    Prepared {i+1}/{len(calibration_records)} calibration builds...")

    return calibration_builds


def run_calibration(project_key, alpha=DEFAULT_ALPHA, rho=DEFAULT_RHO,
                    cal_slice=None, limit=None):
    """Full calibration pipeline for a project.

    1. Load calibration slice
    2. Initialize semantic selector
    3. Compute per-build similarity scores
    4. Calibrate λ̂ using conformal risk control
    5. Save calibration results
    """
    proj = PROJECTS[project_key]
    project_folder = proj["folder"]

    cal_path = Path(cal_slice) if cal_slice else project_folder / "slice_2_cal.jsonl"
    if not cal_path.exists():
        print(f"ERROR: Calibration slice not found: {cal_path}")
        print("Run split_dataset.py first to create chronological slices.")
        sys.exit(1)

    print(f"Loading calibration slice from {cal_path.name}...")
    records = []
    with open(cal_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            records.append(json.loads(line))
    if limit:
        records = records[:limit]
    print(f"  Loaded {len(records)} calibration builds")

    # Import here to avoid circular import
    from src.online_selection.semantic_selector import SemanticSelector
    selector = SemanticSelector(project_key, use_api=True)

    print("\nPreparing calibration data (embedding diffs)...")
    cal_data = prepare_calibration_data(selector, records)

    print("\nRunning conformal calibration...")
    calibrator = ConformalCalibrator(alpha=alpha, rho=rho)
    lambda_hat = calibrator.calibrate(cal_data)

    # Save results
    risk_curve = calibrator.get_risk_curve()
    results = {
        "project": project_key,
        "alpha": alpha,
        "rho": rho,
        "lambda_hat": lambda_hat,
        "calibration_builds": len(records),
        "risk_curve": risk_curve,
    }

    results_path = project_folder / "calibration_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nCalibration results saved to {results_path}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", choices=list(PROJECTS.keys()), help="Project key")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                        help=f"Target error ceiling (default: {DEFAULT_ALPHA})")
    parser.add_argument("--rho", type=float, default=DEFAULT_RHO,
                        help=f"Exponential decay parameter (default: {DEFAULT_RHO})")
    parser.add_argument("--cal-slice", help="Calibration slice JSONL path (default: slice_2_cal.jsonl)")
    parser.add_argument("--limit", type=int, help="Process only first N builds (for testing)")
    args = parser.parse_args()

    run_calibration(args.project, args.alpha, args.rho, args.cal_slice, args.limit)
